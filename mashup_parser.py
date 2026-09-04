"""Best-effort parser for Power Query M expressions: resolves a table's or
dataflow entity's expression to whatever it actually reads from.

Like the Qlik side's load-script parser (see qlik_core.parse_load_tables),
this is deliberately NOT a full M language interpreter -- M supports
arbitrary function definitions, `each` lambdas, custom connectors, and
computed paths that a text-level parser cannot safely evaluate. What this
module resolves with confidence:

  - A direct external-source connector call (Sql.Database, PostgreSQL.Database,
    Odbc.DataSource, AzureStorage.*, Web.Contents, SharePoint.*, Exchange.Contents,
    AnalysisServices.Database, Snowflake.Databases, GoogleBigQuery.Database,
    Folder.Contents, Excel.Workbook, Csv.Document, ...), plus a best-effort
    extraction of the specific table/view/file navigated to afterward
    (the first {[Schema="...", Item="..."]}-style selector following the
    connector call).
  - Which fields survive from that source, IF the M code says so explicitly
    via one of two common patterns: a native SQL passthrough
    (`Sql.Database(..., [Query="SELECT a, b FROM t"])`) or an explicit
    `Table.SelectColumns(source, {"a", "b"})` narrowing step. Absent either
    pattern, the field list is reported as unresolved -- meaning "every
    source column passes through unnarrowed by this scan", not "unknown".
    Full column-level M lineage (tracking every rename/transform step) is
    out of scope, the same stance taken on table-level resolution below.
  - A Power BI dataflow reference: PowerPlatform.Dataflows(...) or
    PowerBI.Dataflows(...) followed by a chain of {[key=value]} selectors
    identifying the workspace, dataflow, and entity -- the exact literal
    text pattern Power Query emits for "Get Data > Power BI dataflows".
  - A same-document reference: `let Source = OtherQueryName in Source`,
    common in dataflows as a "staging query" pattern -- followed one hop at
    a time into the referenced query's own expression, up to a bounded depth.
  - Multiple connector calls feeding one table -- whether written out more
    than once in the table's own text (e.g. two Sql.Database(...) calls
    combined via Table.Combine to enrich/fix data from the same server), or
    brought in via a merge/append/join onto ANOTHER query in the same
    document (Table.NestedJoin, Table.Join, Table.FuzzyNestedJoin,
    Table.Combine -- Power Query's "Merge queries"/"Append queries" UI
    actions) -- each reported as its own source (tagged with which query it
    came in via, for the merge case) rather than the second one being
    silently dropped.

Everything else is reported as unresolved rather than guessed -- see
PARSER_LIMITATIONS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PARSER_LIMITATIONS = (
    "This is a text-level scan of the M expression, not an evaluator: it "
    "does not resolve custom function calls, parameter-substituted "
    "connection strings, or values computed earlier in the same `let` block "
    "(e.g. a server name built from a variable rather than written literally "
    "is captured as that variable's raw text, not resolved to its value). "
    "Same-document query references are followed by name for at most "
    "MAX_REFERENCE_DEPTH hops; a longer chain, or one that loops, stops and "
    "is flagged rather than followed indefinitely. A table produced by "
    "merging/joining another query (Table.NestedJoin, Table.Join, "
    "Table.FuzzyNestedJoin, Table.Combine) has that query's own source(s) "
    "resolved too, tagged with which query brought them in -- but only when "
    "the referenced query is itself visible to this scan: for a Gen1 "
    "dataflow, every query is (the whole document is exported), but for a "
    "dataset, only queries loaded as an actual model table are (a Power "
    "Query helper query set to 'Enable load = false', used only to feed a "
    "merge, is invisible to the Scanner API and cannot be traced). A local "
    "`let`-step that happens to share its name with a real sibling query is "
    "indistinguishable from an actual reference to it. A merge partner that "
    "is itself a dataflow reference, or that could not itself be resolved, "
    "is still listed but not chased any further. Transformation logic that "
    "changes which columns survive a merge/join is not analyzed. "
    "Field-level detail is only reported when the M code uses a native SQL "
    "Query= passthrough or an explicit Table.SelectColumns call -- any "
    "other transform chain (renames, computed columns, multi-step "
    "narrowing) leaves the field list unresolved rather than guessed, and "
    "is detected across the whole expression rather than per individual "
    "source, so a table with more than one source may not have its field "
    "list map cleanly to just one of them."
)

MAX_REFERENCE_DEPTH = 5

_DATAFLOW_FUNCTIONS = ("PowerPlatform.Dataflows", "PowerBI.Dataflows")

# Connector function name -> human label. Extend as new connectors show up
# in real scripts; an unlisted function simply falls through to "unresolved"
# rather than being misreported.
_DIRECT_SOURCE_FUNCTIONS = (
    "Sql.Database",
    "Sql.Databases",
    "PostgreSQL.Database",
    "MySQL.Database",
    "Odbc.DataSource",
    "OleDb.DataSource",
    "Oracle.Database",
    "AzureStorage.DataLake",
    "AzureStorage.Blobs",
    "DataLake.Contents",
    "SharePoint.Contents",
    "SharePoint.Files",
    "Web.Contents",
    "Exchange.Contents",
    "AnalysisServices.Database",
    "Snowflake.Databases",
    "GoogleBigQuery.Database",
    "Teradata.Database",
    "Folder.Contents",
    "Excel.Workbook",
    "Csv.Document",
    "Json.Document",
)

_SELECTOR_RE = re.compile(r"\{\s*\[([^\]]*)\]\s*\}")
_KV_RE = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|([A-Za-z0-9_.]+))""")
_LET_RETURN_RE = re.compile(r"\bin\s+(#\"[^\"]+\"|\w+)\s*$", re.IGNORECASE | re.DOTALL)
_STEP_ASSIGN_RE = re.compile(
    r"""(?:#"([^"]+)"|(\w+))\s*=\s*(.+?)(?=,\s*(?:#"[^"]+"|\w+)\s*=|,\s*$|$)""",
    re.DOTALL,
)

# Field-level detail: only these two well-defined M patterns are trusted.
_SQL_QUERY_ARG_RE = re.compile(r'\bQuery\s*=\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_SQL_SELECT_RE = re.compile(r"(?is)\bSELECT\s+(.*?)\s+FROM\b")
_SELECT_COLUMNS_RE = re.compile(r"Table\.SelectColumns\s*\(.*?\{([^}]*)\}", re.DOTALL)
_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass
class DataflowReference:
    workspace_id: str | None
    dataflow_id: str | None
    workspace_name: str | None
    dataflow_name: str | None
    entity: str | None


@dataclass
class DirectSource:
    connector: str
    connection_args: str  # raw literal text of the connector call's arguments
    resolved_table: str | None  # built from the first Schema/Item/Name/Table selector found
    selector: dict = field(default_factory=dict)
    fields: list[str] | None = None  # None = not narrowed by a recognized pattern (all columns pass through)
    fields_source: str | None = None  # "sql_query" | "select_columns" | None
    via_query: str | None = None  # sibling query name this was pulled in through via a merge/
                                  # append/join, e.g. "Merge queries" onto a Products lookup --
                                  # None means it's this table's own primary source.


@dataclass
class SourceResolution:
    status: str  # "dataflow_reference" | "direct_source" | "multiple_direct_sources" | "unresolved"
    dataflow_ref: DataflowReference | None = None
    direct_sources: list[DirectSource] = field(default_factory=list)
    reference_chain: list[str] = field(default_factory=list)  # query names hopped through
    note: str | None = None


def _split_top_level_commas(text: str) -> list[str]:
    parts, buf, depth = [], [], 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def extract_selected_fields(expr: str) -> tuple[list[str] | None, str | None]:
    """Best-effort field list for a table's resolved source, from whichever
    of two common M patterns is present. Returns (fields, source_tag), or
    (None, None) if neither pattern is found -- meaning all source columns
    pass through unnarrowed by this scan, not "unknown"."""
    qm = _SQL_QUERY_ARG_RE.search(expr)
    if qm:
        sm = _SQL_SELECT_RE.search(qm.group(1))
        if sm:
            cols_text = sm.group(1).strip()
            if cols_text != "*":
                cols = [c.strip().strip('"[]') for c in _split_top_level_commas(cols_text)]
                cols = [c for c in cols if c]
                if cols:
                    return cols, "sql_query"

    sc_matches = list(_SELECT_COLUMNS_RE.finditer(expr))
    if sc_matches:
        cols = [m.group(1) for m in _QUOTED_RE.finditer(sc_matches[-1].group(1))]
        if cols:
            return cols, "select_columns"

    return None, None


def _parse_selectors(text: str) -> list[dict[str, str]]:
    """Every {[k="v", ...]} selector block in text, each as a {k: v} dict."""
    blocks = []
    for m in _SELECTOR_RE.finditer(text):
        pairs = {}
        for km in _KV_RE.finditer(m.group(1)):
            key = km.group(1)
            value = km.group(2) if km.group(2) is not None else km.group(3)
            pairs[key] = value
        if pairs:
            blocks.append(pairs)
    return blocks


def _find_all_call_spans(text: str, func_name: str) -> list[tuple[int, int]]:
    """Every func_name(...) call's argument span in text, paren-matched --
    not just the first, so two calls to the SAME connector function (e.g.
    two Sql.Database(...) calls combined via Table.Combine to enrich/fix
    data from the same server) are both found instead of the second being
    silently dropped."""
    spans = []
    marker = func_name + "("
    pos = 0
    while True:
        idx = text.find(marker, pos)
        if idx == -1:
            break
        open_paren = idx + len(func_name)
        depth, end = 0, None
        for i in range(open_paren, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            break
        spans.append((open_paren + 1, end))
        pos = end + 1
    return spans


def extract_dataflow_reference(expr: str) -> DataflowReference | None:
    for func in _DATAFLOW_FUNCTIONS:
        if func in expr:
            idx = expr.find(func)
            selectors = _parse_selectors(expr[idx:])
            merged: dict[str, str] = {}
            for block in selectors:
                merged.update(block)
            if not merged:
                continue
            return DataflowReference(
                workspace_id=merged.get("workspaceId"),
                dataflow_id=merged.get("dataflowId"),
                workspace_name=merged.get("workspaceName"),
                dataflow_name=merged.get("dataflowName"),
                entity=merged.get("entity"),
            )
    return None


def extract_direct_sources(expr: str) -> list[DirectSource]:
    sources = []
    for func in _DIRECT_SOURCE_FUNCTIONS:
        for start, end in _find_all_call_spans(expr, func):
            args = expr[start:end].strip()
            # The first selector after the call's closing paren is (by far the
            # most common M pattern) the one that navigates to the specific
            # table/view/file; selectors further downstream are usually column
            # projections, not further source navigation.
            after = expr[end:]
            selectors = _parse_selectors(after)
            selector = selectors[0] if selectors else {}
            resolved_table = None
            if selector:
                schema = selector.get("Schema")
                item = selector.get("Item") or selector.get("Name") or selector.get("Table")
                if schema and item:
                    resolved_table = f"{schema}.{item}"
                elif item:
                    resolved_table = item
            fields, fields_source = extract_selected_fields(expr)
            sources.append(
                DirectSource(connector=func, connection_args=args, resolved_table=resolved_table,
                             selector=selector, fields=fields, fields_source=fields_source)
            )
    return sources


_MERGE_FUNCTIONS = ("Table.NestedJoin", "Table.Join", "Table.FuzzyNestedJoin", "Table.Combine")
_BARE_TOKEN_RE = re.compile(r'#"([^"]+)"|\b([A-Za-z_]\w*)\b')


def _find_merge_partner_names(expr: str, known_names: set[str]) -> list[str]:
    """Sibling query/entity names referenced as arguments to a merge/append
    call (Table.NestedJoin, Table.Join, Table.FuzzyNestedJoin, Table.Combine)
    -- Power Query's "Merge queries"/"Append queries" UI actions -- so a
    second (or third...) source brought in to enrich or correct the primary
    one isn't silently dropped. Best-effort: string literals (column-name
    lists, join-kind text) are blanked out first so they can't be mistaken
    for a bare identifier; a local `let`-step that happens to share its name
    with a real sibling query is indistinguishable from an actual reference
    to it, same class of risk as every other text-level match in this
    module. Order-preserving, de-duplicated."""
    found = []
    for func in _MERGE_FUNCTIONS:
        for start, end in _find_all_call_spans(expr, func):
            args = _QUOTED_RE.sub(" ", expr[start:end])
            for m in _BARE_TOKEN_RE.finditer(args):
                name = m.group(1) or m.group(2)
                if name in known_names and name not in found:
                    found.append(name)
    return found


def _flatten_merge_partner(via_name: str, nested: "SourceResolution") -> list[DirectSource]:
    """Fold a merge partner's own resolution into this table's source list,
    tagged with which query brought it in. A partner that itself is a Power
    BI dataflow reference or couldn't be resolved is still surfaced (as a
    single best-effort entry) rather than silently dropped, but is not
    chased any further -- re-run the scan targeting that dataflow directly
    if its own ultimate source matters."""
    if nested.status in ("direct_source", "multiple_direct_sources"):
        return [DirectSource(connector=d.connector, connection_args=d.connection_args,
                             resolved_table=d.resolved_table, selector=d.selector,
                             fields=d.fields, fields_source=d.fields_source, via_query=via_name)
               for d in nested.direct_sources]
    if nested.status == "dataflow_reference":
        ref = nested.dataflow_ref
        label = ref.entity or ref.dataflow_name or ref.dataflow_id or "?"
        return [DirectSource(connector="(Power BI dataflow, not chased further)",
                             connection_args=f"dataflowId={ref.dataflow_id or ''}",
                             resolved_table=label, via_query=via_name)]
    return [DirectSource(connector="(unresolved merge partner)",
                         connection_args=nested.note or "", resolved_table=via_name,
                         via_query=via_name)]


def _find_bare_reference(expr: str, known_names: set[str]) -> str | None:
    """If expr is (in effect) `let Source = X in Source` with no connector or
    dataflow call anywhere in it, and the final returned step is itself just
    a bare identifier matching another known query name, return that name.
    """
    match = _LET_RETURN_RE.search(expr)
    if not match:
        return None
    returned = match.group(1).strip('#"')
    # Everything before the matched "in ..." tail -- step matching must not
    # run over the full expr, or the unseparated "in <expr>" clause (there's
    # no comma before it) gets swallowed into the last step's RHS.
    steps_body = expr[: match.start()]
    # Walk the step assignments looking for the one that defines `returned`;
    # if its own right-hand side is just another bare identifier (no "(" at
    # all, i.e. no function call of any kind), that identifier is a same-
    # document query reference worth chasing -- e.g. `Result = OtherQuery`.
    for step_match in _STEP_ASSIGN_RE.finditer(steps_body):
        name = step_match.group(1) or step_match.group(2)
        if name != returned:
            continue
        rhs = step_match.group(3).strip().rstrip(",").strip()
        if "(" not in rhs and rhs in known_names:
            return rhs
        return None
    # No step defines it (e.g. `returned` IS the bare name itself, as in a
    # one-line `let Source = OtherQuery in Source`-shaped alias with no
    # intermediate step) -- treat the returned identifier itself as the hop.
    if returned in known_names:
        return returned
    return None


def resolve_source(
    expr: str,
    sibling_queries: dict[str, str] | None = None,
    _depth: int = 0,
    _chain: list[str] | None = None,
) -> SourceResolution:
    chain = _chain or []
    sibling_queries = sibling_queries or {}

    dataflow_ref = extract_dataflow_reference(expr)
    if dataflow_ref is not None:
        return SourceResolution(status="dataflow_reference", dataflow_ref=dataflow_ref, reference_chain=chain)

    direct = extract_direct_sources(expr)

    if _depth < MAX_REFERENCE_DEPTH:
        available = set(sibling_queries) - set(chain)
        for name in _find_merge_partner_names(expr, available):
            nested = resolve_source(sibling_queries[name], sibling_queries=sibling_queries,
                                    _depth=_depth + 1, _chain=chain + [name])
            direct.extend(_flatten_merge_partner(name, nested))

    if len(direct) == 1:
        return SourceResolution(status="direct_source", direct_sources=direct, reference_chain=chain)
    if len(direct) > 1:
        return SourceResolution(status="multiple_direct_sources", direct_sources=direct, reference_chain=chain)

    if _depth < MAX_REFERENCE_DEPTH:
        ref_name = _find_bare_reference(expr, set(sibling_queries) - set(chain))
        if ref_name:
            return resolve_source(
                sibling_queries[ref_name],
                sibling_queries=sibling_queries,
                _depth=_depth + 1,
                _chain=chain + [ref_name],
            )

    note = (
        f"reference chain exceeded {MAX_REFERENCE_DEPTH} hops or looped"
        if _depth >= MAX_REFERENCE_DEPTH
        else "no recognized connector call, dataflow reference, or resolvable query reference found"
    )
    return SourceResolution(status="unresolved", reference_chain=chain, note=note)


_SHARED_QUERY_RE = re.compile(
    r'shared\s+(?:#"([^"]+)"|(\w+))\s*=\s*(.*?);\s*(?=shared\s+(?:#"[^"]+"|\w+)\s*=|\Z)',
    re.DOTALL,
)


def split_shared_queries(document: str) -> dict[str, str]:
    """Parse a full M mashup document (one dataflow's worth, or a dataset's)
    into {query_name: expression_text}. Each dataflow's model.json stores
    every entity's query as one `shared <Name> = <expr>;` member of a single
    M section -- see mashup_parser module docstring for the Common Data
    Model reference this is based on."""
    queries = {}
    for m in _SHARED_QUERY_RE.finditer(document):
        name = m.group(1) or m.group(2)
        queries[name] = m.group(3).strip()
    return queries

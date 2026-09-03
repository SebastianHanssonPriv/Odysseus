"""Best-effort parser for the QVD-reading LOAD statements in a Qlik load
script.

This is deliberately NOT a full Qlik script interpreter. Qlik's load-script
language has conditional blocks (IF/FOR/SUB), variable substitution
($(var)), and table-driven renames that a text-level parser cannot safely
evaluate. What this module extracts is exact for the common, literal-path
QVD load patterns that make up the large majority of real scripts:

    [Label:]
    LOAD field1, field2 AS alias2, expr(field3) AS derived
    FROM [lib://Connection/Path/File.qvd] (qvd) [WHERE ...];

    [JOIN|LEFT JOIN|INNER JOIN|OUTER JOIN|RIGHT JOIN] (Target)
    LOAD ... FROM ... (qvd);

    CONCATENATE [(Target)]
    LOAD ... FROM ... (qvd);

Everything outside that shape is flagged rather than guessed — see
PARSER_LIMITATIONS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PARSER_LIMITATIONS = (
    "Statements inside IF/FOR/SUB blocks are parsed as if unconditional "
    "(no control-flow evaluation, so a load that only runs under some "
    "condition is reported the same as one that always runs). "
    "Variable-substituted FROM paths ($(var)) are matched as literal text, "
    "so a dynamic path will not match the (qvd) source pattern and is "
    "silently absent from the results rather than flagged — a script that "
    "relies heavily on $(var) paths for QVD loads will under-report. "
    "LOAD * FROM ... (qvd) is reported as a wildcard: the QVD is confirmed "
    "used, but its field list is not resolvable from script text alone "
    "(the Engine API has no metadata-only QVD field peek short of loading "
    "the file). RENAME FIELDS USING <mapping table> (a table-driven bulk "
    "rename) is not resolved; its presence is surfaced as a script-level "
    "warning instead."
)

_QVD_LOAD_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<join>JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|INNER\s+JOIN|OUTER\s+JOIN)\s*
       \(\s*(?P<join_target>[^)]+?)\s*\)\s*)?
    (?:(?P<concat>CONCATENATE)\s*
       (?:\(\s*(?P<concat_target>[^)]+?)\s*\))?\s*)?
    (?:LOAD|SELECT)\s+
    (?P<fields>.*?)
    \s+FROM\s+
    (?P<source>\[[^\]]+\]|'[^']+'|"[^"]+"|\S+)
    \s*\(\s*qvd\s*\)
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_LEADING_LABEL_RE = re.compile(r"^\s*([A-Za-z_][\w .-]*?)\s*:\s*(?=\S)")
_CONTROL_KEYWORD_RE = re.compile(r"(?i)^(if|for|sub|let|set)\b")
_RENAME_FIELD_RE = re.compile(r"RENAME\s+FIELD\s+(.+)", re.IGNORECASE | re.DOTALL)
_RENAME_FIELDS_USING_RE = re.compile(r"RENAME\s+FIELDS\s+USING\s+", re.IGNORECASE)
_RENAME_PAIR_RE = re.compile(
    r"""(?P<old>\[[^\]]+\]|'[^']+'|"[^"]+"|\S+)\s+TO\s+(?P<new>\[[^\]]+\]|'[^']+'|"[^"]+"|\S+)""",
    re.IGNORECASE,
)
_SIMPLE_IDENTIFIER_RE = re.compile(r"^\[?[A-Za-z_][\w .]*\]?$")
_AS_SPLIT_RE = re.compile(r"\s+AS\s+", re.IGNORECASE)


@dataclass
class LoadedField:
    source_name: str  # bare identifier as it appears in the QVD, or the raw expression if derived
    final_name: str  # name in the resulting table, after this LOAD's own AS and any later RENAME FIELD
    simple_passthrough: bool  # True if source_name is a single bare identifier (traceable 1:1)


@dataclass
class QvdLoadStatement:
    target_table: str | None  # None when it cannot be determined from script text alone
    target_resolution: str  # "label" | "join" | "concatenate" | "concatenate_implicit" | "unresolved"
    source_path: str  # the literal FROM-clause text, quotes/brackets stripped
    fields: list[LoadedField]
    wildcard: bool  # True for LOAD * FROM ... (qvd)


def _strip_comments(script: str) -> str:
    # Block comments first, then line comments. A `//` immediately preceded
    # by `:` is treated as part of a URI-style path (lib://, http://) rather
    # than a comment start -- without this, every QVD FROM clause would be
    # truncated at its own connection path. Does not special-case a `//` or
    # `/*` occurring inside a quoted string literal otherwise (rare in a QVD
    # LOAD's own field list / FROM clause) -- a known, accepted gap rather
    # than a full tokenizer, consistent with this module's scoped ambition.
    script = re.sub(r"/\*.*?\*/", " ", script, flags=re.DOTALL)
    script = re.sub(r"(?<!:)//[^\n]*", "", script)
    return script


def _split_top_level(text: str, sep: str, open_chars: str = "(", close_chars: str = ")") -> list[str]:
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in open_chars:
            depth += 1
        elif ch in close_chars:
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name[0] == "[" and name[-1] == "]":
        return name[1:-1]
    if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
        return name[1:-1]
    return name


def _parse_field(raw: str) -> LoadedField:
    parts = _AS_SPLIT_RE.split(raw, maxsplit=1)
    source_expr, alias_raw = (parts[0], parts[1]) if len(parts) == 2 else (raw, raw)
    source_expr = source_expr.strip()
    simple = bool(_SIMPLE_IDENTIFIER_RE.match(source_expr))
    source_name = _unquote(source_expr) if simple else source_expr
    final_name = _unquote(alias_raw.strip())
    return LoadedField(source_name=source_name, final_name=final_name, simple_passthrough=simple)


def parse_qvd_loads(script: str) -> tuple[list[QvdLoadStatement], list[str]]:
    """Return (qvd_load_statements, script_level_warnings)."""
    cleaned = _strip_comments(script)
    warnings: list[str] = []
    if _RENAME_FIELDS_USING_RE.search(cleaned):
        warnings.append(
            "Script uses RENAME FIELDS USING (table-driven rename) — field "
            "name resolution after that point is not tracked by this parser."
        )

    rename_map: dict[str, str] = {}
    results: list[QvdLoadStatement] = []
    last_table_name: str | None = None

    for raw_stmt in _split_top_level(cleaned, ";", open_chars="[", close_chars="]"):
        stmt = raw_stmt.strip()
        if not stmt:
            continue

        label = None
        label_match = _LEADING_LABEL_RE.match(stmt)
        if label_match and not _CONTROL_KEYWORD_RE.match(stmt):
            label = label_match.group(1).strip()
            stmt_body = stmt[label_match.end() :]
        else:
            stmt_body = stmt

        rename_match = _RENAME_FIELD_RE.match(stmt_body.strip())
        if rename_match:
            for pair in _RENAME_PAIR_RE.finditer(rename_match.group(1)):
                rename_map[_unquote(pair.group("old"))] = _unquote(pair.group("new"))
            continue

        match = _QVD_LOAD_RE.search(stmt_body)
        if not match:
            continue

        source_path = _unquote(match.group("source"))
        field_text = match.group("fields").strip()

        if match.group("join_target"):
            target_table, resolution = _unquote(match.group("join_target")), "join"
        elif match.group("concat"):
            if match.group("concat_target"):
                target_table, resolution = _unquote(match.group("concat_target")), "concatenate"
            elif label:
                target_table, resolution = label, "concatenate"
            else:
                # Documented Qlik behavior: an unparenthesized CONCATENATE
                # attaches to the table created by the immediately preceding
                # load, not a guess.
                target_table, resolution = last_table_name, "concatenate_implicit"
        elif label:
            target_table, resolution = label, "label"
        else:
            target_table, resolution = None, "unresolved"

        wildcard = field_text == "*"
        fields = [] if wildcard else [_parse_field(f) for f in _split_top_level(field_text, ",")]

        results.append(
            QvdLoadStatement(
                target_table=target_table,
                target_resolution=resolution,
                source_path=source_path,
                fields=fields,
                wildcard=wildcard,
            )
        )
        if target_table:
            last_table_name = target_table

    # Chain RENAME FIELD statements onto each load's field names so the name
    # checked against the final model is the field's truly final name, not
    # just what this one LOAD statement called it.
    for stmt in results:
        for f in stmt.fields:
            resolved, seen = f.final_name, {f.final_name}
            while resolved in rename_map and rename_map[resolved] not in seen:
                resolved = rename_map[resolved]
                seen.add(resolved)
            f.final_name = resolved

    return results, warnings

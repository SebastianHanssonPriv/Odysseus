# powerbi (part of the Odysseus toolkit)

Three established commands (`collect`, `raw-export`, `analytics`) covering
usage collection, documented in the root README and each module's own
docstring. This file covers the fourth, `model-lineage`: for every semantic
model (dataset) in the tenant, which warehouse table/view each of its tables
actually reads from — direct, or reached through a Gen1 dataflow.

## Why this exists

Power BI has no single API that maps "semantic model table → warehouse
source." The Datasources APIs (`Datasets - Get Datasources`, `Dataflows -
Get Dataflow Data Sources`) only return a flat, tenant/dataset-level list of
distinct connections used somewhere — never which table uses which one. The
only way to get that granularity is to read the actual Power Query M code
behind each table, the same way the Qlik tool reads Qlik load-script text —
see [`mashup_parser.py`](mashup_parser.py) for the parsing approach and its
stated confidence tiers.

## Setup and prerequisite (read this before running it)

`model-lineage` uses the same Power BI service principal and credential
setup as `collect`/`raw-export`/`analytics` (see the root README) — no new
environment variables. But it needs one more thing that the other three
commands don't:

**A tenant admin must enable, in the Admin portal → Tenant settings, both:**
1. "Enhance admin APIs responses with detailed metadata"
2. "Enhance admin APIs responses with DAX and mashup expressions" (only
   selectable once #1 is on)

Without #2, the Scanner API call this tool depends on still succeeds and
still returns the full workspace/dataset/table inventory — but every
table's M code comes back missing, so every table resolves to
`no_expression_available`. This is not a bug the tool can detect or route
around; it's a tenant configuration gate, and it is the single most likely
reason a first run comes back mostly empty.

```
python cli.py powerbi model-lineage [--data-dir PATH] [--interactive] [--scan-timeout SECONDS]
```

## How it works

1. **Enumerate every workspace** (`scanner.list_workspace_ids`, paginated
   `Admin - Groups`).
2. **Run the tenant-wide Scanner API** (`scanner.scan_workspaces`) in
   batches of 100 workspaces, asking for dataset schema, dataset
   expressions (the per-table M code), datasource details, and lineage.
   This is an asynchronous job: trigger, poll status, fetch result — a
   batch that times out or errors is recorded and skipped rather than
   aborting the whole run.
3. **For each table**, its M expression is checked
   (`mashup_parser.resolve_source`) for:
   - a direct external-source connector call (`Sql.Database`,
     `PostgreSQL.Database`, `Odbc.DataSource`, `AzureStorage.*`,
     `Web.Contents`, `SharePoint.*`, and others — see
     `mashup_parser._DIRECT_SOURCE_FUNCTIONS`), plus a best-effort
     extraction of the specific table/view/file navigated to afterward;
   - a **Gen1 dataflow reference** (`PowerPlatform.Dataflows`/
     `PowerBI.Dataflows` followed by a `{[workspaceId=...]}...
     {[dataflowId=...]}...{[entity=...]}` chain — the literal text Power
     Query emits for "Get Data → Power BI dataflows");
   - a same-document **staging-query reference** (`let Source = OtherQuery
     in Source`), chased by name within the same dataset or dataflow.
4. **When a table resolves to a dataflow reference**, that dataflow's own
   definition is fetched once (`dataflow_admin.export_dataflow`, cached per
   run) and its M code parsed the same way (`mashup_parser.split_shared_queries`
   + `resolve_source` again) — chaining through further dataflow references
   (linked entities) up to 5 hops before giving up.

## Output

Writes, under `<data-dir>/model_lineage/`:

- `model_lineage_<UTC-date>.jsonl` — one line per table: `workspace_id`,
  `workspace_name`, `dataset_id`, `dataset_name`, `table_name`, `status`,
  `hops` (the dataflow-boundary chain, if any), `direct_sources`, `note`.
- `model_source_usage_<UTC-date>.csv` — the flat view: one row per table
  with `hop_count`, `connector`, `resolved_table`, and `status` — the file
  to open for "which warehouse table feeds this semantic model table."

`status` values, most to least resolved:

| Status | Meaning |
|---|---|
| `direct_source` | Resolved to exactly one connector call, with the specific table/view/file extracted where the M code's own selector pattern allowed it. |
| `multiple_direct_sources` | The table's M code combines more than one distinct connector call (e.g. `Table.Combine`) — all of them are listed; not narrowed to one. |
| `dataflow_reference_incomplete` / `dataflow_export_failed` / `dataflow_entity_not_found` | The chain crossed into a dataflow but couldn't be completed — see `note` for which of the three failed (a parsing gap, an export call failure such as the service principal lacking access to that dataflow's workspace, or the entity name not found in the dataflow's own parsed queries). |
| `max_hops_exceeded` | A dataflow-to-dataflow reference chain went deeper than 5 hops (or looped) without resolving. |
| `unresolved` | No recognized connector call, dataflow reference, or resolvable query reference was found in the table's own M code. |
| `no_expression_available` | The Scanner API returned no M code for this table at all — see the tenant-setting prerequisite above before assuming this is a parser gap. |

## Known limitations (stated, not silently worked around)

- **Not verified against a live tenant response.** The exact JSON shape at
  two points — the Scanner API's per-table `source[].expression` field, and
  the dataflow export's `pbi:mashup.document` field — is based on Microsoft's
  published REST API schema and the Common Data Model `model.json`
  documentation respectively, not on a captured real response (network
  access to Microsoft's docs was unavailable while building this). Both are
  read defensively: if the expected key is missing, the table/dataflow is
  flagged (`no_expression_available` / a `dataflow_export_failed` note),
  never silently treated as "no source."
- **Text-level parser, not an M interpreter.** Custom function calls,
  parameter-substituted connection strings, and values computed earlier in
  the same `let` block are not evaluated — a server name built from a
  variable is captured as that variable's raw text, not resolved. Full
  detail in `mashup_parser.PARSER_LIMITATIONS`.
- **Table-level only, not column-level.** Unlike the Qlik tool's per-field
  QVD usage tracing, this module resolves which source a table comes from,
  not which specific source columns survive into it. Column-level M lineage
  (tracking a `Table.RenameColumns`/`Table.SelectColumns` chain) was judged
  a materially larger parsing problem than table-level source resolution
  and is not attempted here.
- **Gen1 dataflows only**, matching the current environment. Gen2 dataflows
  (Fabric Data Factory) are a different artifact entirely with a different
  API surface and are out of scope.
- **Same-document reference chasing is bounded** (`MAX_REFERENCE_DEPTH = 5`
  hops) and does not evaluate conditional logic — a staging query wrapped in
  `if` logic is followed as if unconditional, the same stance the Qlik
  script parser takes on `IF`/`FOR`/`SUB` blocks.

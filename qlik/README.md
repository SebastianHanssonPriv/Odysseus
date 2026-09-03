# qlik (part of the Odysseus toolkit)

Extracts data-model lineage for every app in a Qlik Cloud tenant via the
Engine API: load script, script-derived LOAD/STORE lineage, the resident
table/field/key graph, and — the primary governance question this tool
answers — for each source QVD an app's script reads, which specific fields
from that QVD are confirmed present in the app's final data model. Built on
a reusable Engine API connector (`engine_client.py`) so the next Qlik
governance tool in this suite — usage collection, content inventory,
whatever comes next — connects and authenticates the same way instead of
re-solving it.

## Why this exists

Qlik Cloud's own tooling does not give a reliable, exportable, tenant-wide
view of what each app's data model actually contains, which source QVDs it
depends on, or which fields from those QVDs actually survive into the final
model versus being loaded and then dropped, joined away, or left unused.
This tool pulls that directly from the Engine API (the same interface the
Qlik Sense client itself uses) cross-checked against the app's own load
script — see "How QVD field usage is determined" below for exactly how much
confidence to put in each result.

## Setup

Run from the repo root (`cli.py` there is the single entry point for both
platforms — see the [root README](../README.md)):

1. In the Qlik Cloud Management Console, create an OAuth **machine-to-machine
   (M2M)** client (Identity & access → OAuth clients). This is the service
   identity the tool authenticates as — not a personal account, not a
   personal long-lived API key — so access stays attributable and revocable
   independently of any one person, the same governance bar the Power BI
   side holds itself to with its service principal.
2. Copy `.env.example` to `.env` and fill in `QLIK_TENANT_URL` and
   `QLIK_OAUTH_CLIENT_ID`.
3. Configure exactly one credential source for the client secret:
   `QLIK_KEY_VAULT_URL` + `QLIK_KEY_VAULT_SECRET_NAME` (recommended),
   `QLIK_OAUTH_CLIENT_SECRET` in `.env` (local prototyping only), or
   `--interactive` at runtime.
4. `pip install -r requirements.txt`
5. `python cli.py qlik extract`

## Command

```
odysseus qlik extract [--data-dir PATH] [--interactive]
```

Writes, under `<data-dir>/lineage/`:

- `lineage_<UTC-date>.jsonl` — one line per app: `app_id`, `app_name`,
  `script`, `lineage`, `tables`, `keys`, `tables_error`, `qvd_field_usage`,
  `qvd_lineage_warnings`, `extracted_at`, `error`.
- `lineage_summary_<UTC-date>.csv` — one row per app: table/field/key
  counts, lineage statement count, script line count, QVD source count,
  confirmed/unresolved QVD field counts, and any error — a fast tenant-wide
  scan without opening the JSONL.
- `qvd_field_usage_<UTC-date>.csv` — the detail behind those counts: one row
  per (app, QVD source, field) with `target_table`, `source_field` (the
  name/expression as it appears in the QVD load), `final_field` (its name
  after any `AS` alias and `RENAME FIELD`), `simple_passthrough`, and
  `status`. This is the file to open for "which fields from QVD X are
  actually used, and where."

## How QVD field usage is determined

`qvd_field_usage` is not a single Engine API call — the Engine API has no
"which QVD field feeds which model field" method. It is built by parsing
every `LOAD ... FROM <path> (qvd)` statement in the script (`script_parser.py`)
and cross-checking each field against the app's real, currently-resident
data model (`GetTablesAndKeys`, already pulled for the `tables` output).
Each field lands in one status, most to least confident:

| Status | Meaning |
|---|---|
| `confirmed` | Loaded from the QVD with no transformation, and verified present under that exact name in the resolved target table. |
| `confirmed_case_mismatch` | Found only via a case-insensitive match — Qlik field names are case-sensitive, so this is worth a look even though it counts as confirmed. |
| `derived_expression` | The script computes this field from an expression (e.g. `Amount*1.25 AS GrossAmount`) rather than passing the QVD column through unchanged. Confirmed present in the model, but "used" means "used as an input," not "used as-is." |
| `not_found_in_final_model` | The script loads this field from the QVD, but it is not present under this name in the resolved target table — most likely dropped, joined away, or renamed by a construct the parser does not track. |
| `target_table_unresolved` | The parser could not determine which final table this LOAD's fields land in (e.g. an unlabeled load with no JOIN/CONCATENATE target), so no model lookup was possible. |
| `wildcard_unresolved` | `LOAD * FROM ... (qvd)` — the QVD is confirmed used by the app, but which specific fields cannot be determined from script text alone (see limitations below). |

`qvd_sources_count`, `qvd_fields_confirmed_count`, and
`qvd_fields_unresolved_count` in the summary CSV roll these up per app;
`confirmed`, `confirmed_case_mismatch`, and `derived_expression` count as
confirmed, everything else as unresolved.

## Known limitations (stated, not silently worked around)

- **The parser is text-level, not a script interpreter.** It does not
  evaluate `IF`/`FOR`/`SUB` blocks (a load inside a condition is reported as
  if it always runs), and does not resolve `$(variable)` substitution in a
  `FROM` path — a script that builds its QVD paths dynamically will
  under-report those sources rather than have them flagged. `RENAME FIELDS
  USING <mapping table>` (a table-driven bulk rename) is detected and
  surfaced as a script-level warning in `qvd_lineage_warnings`, but not
  resolved, since resolving it would need the mapping table's actual
  contents. Full detail in `script_parser.PARSER_LIMITATIONS`.
- **`LOAD *` cannot be expanded to a field list from script text alone**, and
  the Engine API offers no metadata-only way to peek a QVD's columns without
  loading the file — deliberately not attempted here, since a read-only
  governance tool should not be triggering data loads. A wildcard QVD load
  is reported as "this QVD is used" with fields left `wildcard_unresolved`
  rather than guessed.

- **`GetLineage` can under-report.** Qlik's own documentation and community
  reports note it can return an empty array, or blank discriminator/
  statement pairs, for scripts using precedent load or certain control
  statements. Treat an empty `lineage` as "not reported for this script
  shape", not "this app has no sources" — `script` (the full load-script
  text) is the ground truth.
- **`GetTablesAndKeys` parameter shape is a documented best effort**, not a
  confirmed-per-tenant contract. If a tenant's engine version rejects the
  call, that app's record still carries `script` and `lineage` (the
  higher-confidence outputs); `tables_error` records why the table/key graph
  is missing for that app.
- **Per-app access failures don't abort the run.** If the M2M client can list
  an app via the Items API but lacks engine-level access to open it (a
  common Qlik Cloud space-permission gap for a tenant-admin-scoped M2M
  client), that app's `error` field is set and extraction continues for the
  rest of the tenant.
- **Not attempted:** resolving each table's fields back to a physically
  named external source system beyond what the load script's own LOAD/STORE
  text says. The Engine API does not expose connection metadata for that
  directly.

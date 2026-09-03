# qlik-lineage

Extracts data-model lineage for every app in a Qlik Cloud tenant via the
Engine API: load script, script-derived LOAD/STORE lineage, and the
resident table/field/key graph. Built on a reusable Engine API connector
(`engine_client.py`) so the next Qlik governance tool in this suite — usage
collection, content inventory, whatever comes next — connects and
authenticates the same way instead of re-solving it.

## Why this exists

Qlik Cloud's own tooling does not give a reliable, exportable, tenant-wide
view of what each app's data model actually contains and where it came from.
This tool pulls that directly from the Engine API, the same interface the
Qlik Sense client itself uses.

## Setup

1. In the Qlik Cloud Management Console, create an OAuth **machine-to-machine
   (M2M)** client (Identity & access → OAuth clients). This is the service
   identity the tool authenticates as — not a personal account, not a
   personal long-lived API key — so access stays attributable and revocable
   independently of any one person, the same governance bar the Power BI
   tool holds itself to with its service principal.
2. Copy `.env.example` to `.env` and fill in `QLIK_TENANT_URL` and
   `QLIK_OAUTH_CLIENT_ID`.
3. Configure exactly one credential source for the client secret:
   Key Vault (recommended), `QLIK_OAUTH_CLIENT_SECRET` in `.env` (local
   prototyping only), or `--interactive` at runtime.
4. `pip install -r requirements.txt`
5. `python cli.py extract`

## Command

```
qlik-lineage extract [--data-dir PATH] [--interactive]
```

Writes, under `<data-dir>/lineage/`:

- `lineage_<UTC-date>.jsonl` — one line per app: `app_id`, `app_name`,
  `script`, `lineage`, `tables`, `keys`, `tables_error`, `extracted_at`,
  `error`.
- `lineage_summary_<UTC-date>.csv` — one row per app: table/field/key
  counts, lineage statement count, script line count, and any error, for a
  fast tenant-wide scan without opening the JSONL.

## Known limitations (stated, not silently worked around)

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

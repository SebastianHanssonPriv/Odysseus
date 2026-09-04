# Bufab BI Governance Studio — How to run

One desktop app over **two products**:

- **Qlik** — extract metadata, compare master items across apps, find unused
  items, write master items from a CSV, trace field lineage, and a tenant-wide
  **Capacity** report with an in-app dashboard.
- **Power BI** — collect activity events, export the raw event log, and build
  usage analytics with an in-app **Usage dashboard**.

A left nav rail switches **Home · Qlik · Power BI**; the header, status line,
busy indicator, LOG and output folder are shared. **Settings** is at the bottom
of the nav rail.

---

## 1. Prerequisites
- **Windows** (the app and the `.exe` build target Windows).
- **Python 3.9+** on PATH — only to run from source or build the `.exe`.
- **Qlik:** a Qlik Cloud API key (read access for most features; write for
  *Apply master items*; tenant-admin for the authoritative capacity meter).
- **Power BI:** a service principal (Entra app registration) added to the Power
  BI admin security group, with one credential source:
  - a **client secret** entered in Settings (held in memory only), or
  - an **Azure Key Vault** URL + secret name (your own `az login` / managed
    identity unlocks the vault), or
  - **Managed identity** (when running where the identity itself is the SP).

Install dependencies once:
```bat
pip install -r requirements.txt
```

---

## 2. Start and set up
```bat
python studio_app.py
```
Open **Settings** (bottom of the nav rail) and fill in what you need:
- **Qlik:** Tenant host + API key.
- **Power BI:** Tenant ID, Client ID, an Auth mode, and either the client secret
  or the Key Vault URL + secret name.
- **Output folders:** a separate one for **Qlik** and for **Power BI** — the two
  products never write into the same folder. Within each, every feature gets
  its own subfolder, so nothing lands loose in one shared pile:
  - Qlik → `<Qlik output>\metadata_export\`, `\comparison_analysis\`,
    `\usage_analysis\`, `\capacity_report\`, `\apply_master_items\`,
    `\field_lineage\` (both the QVD field usage report and the interactive
    trace), `\tenant_usage\`.
  - Power BI → `<Power BI output>\powerbi_data\` (as before), with its own
    `activity_events\`, `raw\`, `analytics\`, `model_lineage\` subfolders.
  Upgrading from an older version that had one shared "Output folder"?
  Both new folders are pre-filled with that old value the first time you open
  Settings — change either one (or both) as needed.

Secrets (Qlik API key, Power BI client secret) are **never saved to disk** —
re-enter them each session. Everything else is remembered in
`~/.bufab_bi_studio.json`.

---

## 3. Qlik workspace
Click **Load apps**, tick the apps you want (picks stick across filtering), then
use a tab: **Extract metadata · Comparison analysis · Usage analysis · Apply
master items · Field lineage · Capacity report · Tenant usage**. The Capacity
tab scans the whole tenant, shows a dashboard (billed % gauge, duplicate-reclaim
and per-space charts, colour-coded action list) **and** writes
`capacity_report_*.xlsx`.

*Apply master items* is the only write path — Dry run is on by default, a backup
is exported first, and a confirmation dialog appears before any real write.

The **Field lineage** tab has two independent tools:
- **QVD field usage report** — batch mode: for every selected app, scans the
  load script's QVD-sourced LOAD statements and cross-checks every field
  against the live data model, so you can see, per app, which source QVDs
  and which fields in them are actually confirmed used in the final model
  (vs. loaded but dropped, joined away, or not found under that name). Writes
  one combined `qvd_field_usage_*.xlsx` and shows a summary in the panel
  below — treat "not found" fields as a prioritized worklist, not a verdict,
  since this is a text-level script scan (see the workbook's warning sheet
  for exactly what it does not evaluate). Tick "Also trace upstream to the
  true source" to additionally resolve each confirmed QVD's real origin —
  a database table/view, or wherever the chain of Qlik apps producing that
  QVD ultimately stops — mirroring the Power BI Model lineage tab's goal
  (source, all the way to the final model) but for Qlik. This is slower: it
  opens every QVD's producing app via Qlik's own lineage graph, one time per
  distinct QVD across the whole scan (not per field).
- **Field lineage (trace)** — interactive mode: pick one app and one field to
  see the pipeline that field took *into* this app, optionally extended
  upstream across apps via Qlik's own lineage graph.

The **Tenant usage** tab answers the same two questions as the QVD field usage
report, but tenant-wide, for the *entire* lineage, and for exactly what
matters for governance: no app selection needed — it starts from every
**published** app (unpublished/personal apps are not scan roots, since nobody
consumes reports from them directly) and walks backward through Qlik's own
lineage graph, one producing app at a time, to find every **upstream/
supporting app** that feeds a published app's data — an ETL or staging app
that is itself unpublished still gets fully scanned if a published app's data
passes through it. For every app in that chain (root and upstream alike) it
finds (1) which QVDs it reads (an entry in the tenant's data-file inventory
that nothing in the whole chain reads is a cleanup candidate), and (2) every
field it loads from those QVDs — including ones dropped or renamed before
reaching that app's own final model (a "supporting" field) — with, for a
published app, whether the field is actually **used in a report**: placed on
a measure, dimension or visual, not merely present in the data model. And (3)
once every reachable app has been scanned, every field's **true origin** —
traced back through however many producer-app hops it takes, to a database
table (shown with its connection/database name, e.g. `SalesDW  ·
dbo.FactSales`) or a file wherever the chain of Qlik apps ultimately bottoms
out — for free, reusing the scripts already fetched for the lineage walk
itself (no extra API calls, no separate step or checkbox).

Writes one `tenant_qvd_usage_*.xlsx` — **Summary**, **Apps** (every app
touched, its **Space** and **Space type**, and whether it's a Root/published
app or an Upstream/supporting one — the security-relevant view: a published
report's data can pass through an app sitting in a much less restricted space
than the report itself), **QVD inventory**, and **Field usage** (now with
**True origin** and **Chain** columns showing exactly how far back each field
was traced and why it stopped where it did) — and shows a summary in the
panel below. This walks and fully re-scans every app in the lineage, not just
the published ones, so it is meaningfully slower than the per-app QVD field
usage report — expect it to take a while on a large tenant, and note that a
very deep or branching pipeline is capped at 6 hops back from each published
app: a row still showing "QVD" as its true origin means the chain stopped
there (no producing app found, that app's own source wasn't resolvable from
its script, or the hop cap was hit) — a lead to verify by hand, not a dead
end. Same caveat as Usage analysis: "used in a report" is detected by
text-matching expressions, so a field referenced only through a dynamic
`$(...)` expression can be wrongly marked unused — verify before deleting.
Space type is read from Qlik Cloud's Spaces API as-is; confirm actual access
level in the Qlik admin console before relying on it.

## 4. Power BI workspace
- **Collect** — pick a UTC **date range** (From / To, defaults to the last 7 days
  up to yesterday) and pull each day. Days already collected are skipped, so
  re-running is safe. **Catch up (last 28 days)** backfills everything still in
  Power BI's ~28-day retention window in one click.
- **Raw export** — flatten every collected event into Parquet/CSV + a key map.
- **Usage analytics** — aggregate view events into the usage tables (CSV) and
  show the dashboard (top reports, top users, views-per-day, least-viewed
  reports).
- **Model lineage** — tenant-wide (no selection needed): for every table in
  every semantic model, resolves its warehouse source — direct, or chased
  through a Gen1 dataflow — from each table's actual Power Query M code, and
  the specific fields kept where the M code makes that explicit (a native
  SQL `Query=` passthrough or an explicit `Table.SelectColumns`). A table can
  have **more than one source**, and both are resolved: two calls to the same
  connector in one table's own M code (e.g. two tables pulled from the same
  SQL server to fix/enrich each other), and a source brought in via a
  merge/join onto another query in the same dataset or dataflow (Power
  Query's "Merge queries"/"Append queries") — shown tagged `(via
  <QueryName>)` so it's clear it isn't the table's own primary source. A
  merge partner that is itself a Gen1 dataflow reference, or that couldn't be
  resolved, is still listed but not chased further; a merge partner that's a
  Power Query helper query not loaded as an actual model table is invisible
  to this scan for a **dataset** (a Gen1 **dataflow**'s full document is
  exported regardless of load state, so that limitation doesn't apply there).
  Also, for every column the Scanner API lists on the model (independent of
  whether the M code above was resolvable), whether it's referenced by a
  measure or calculated column's DAX expression anywhere in its dataset — the
  closest proxy to "used in a report" available: Power BI's Admin APIs expose
  no report/visual content at all (unlike Qlik's Engine API), so a raw column
  placed directly on a visual with no calculation involved cannot be
  detected this way, and a "No" is a candidate to verify by hand, not a
  verdict. Writes one `model_lineage_*.xlsx` (Summary, Model lineage, Column
  usage, and **Sources** — the reverse view: for each resolved source, how
  many tables across the tenant actually pull from it, highest first) and
  shows a status summary in the panel below. **Requires** the tenant admin
  setting "Enhance admin APIs responses with DAX and mashup expressions"
  (Admin portal → Tenant settings) — without it every table comes back
  `no_expression_available`. Gen1 dataflows only, matching the current
  environment — a baseline ahead of the move to Fabric and Gen2 dataflows;
  see model_lineage.py if that adds Gen2 support later.

### Run it daily (unattended) — until Fabric takes over
There is no automatic collection yet. Two ways to keep the daily history flowing:

1. **Manual / catch-up (no setup):** open **Power BI -> Collect** and either pull
   a range or click **Catch up (last 28 days)**. Skipped days mean you only ever
   pull what's missing, so doing it whenever you remember is fine.
2. **Scheduled service (set once, runs itself):** `collect_daily.bat` collects
   *yesterday* headlessly. Configure it once, then register a Windows Scheduled
   Task:
   - Copy `.env.example` -> `.env`; fill in `PBI_TENANT_ID`, `PBI_CLIENT_ID`, and
     a **Key Vault** (recommended) or managed-identity credential — an unattended
     task can't use a secret typed into the app each session.
   - Set `OUTPUT_DIR` in `.env` to your app's `<Output folder>\powerbi_data` so
     the in-app dashboard reads the scheduled collections too.
   - **Task Scheduler -> Create Task** -> Trigger: Daily, ~06:00 local -> Action:
     *Start a program* -> Program: `collect_daily.bat`, "Start in" = this folder.
     Tick *Run whether the user is logged on or not*.
   Each run appends one day; **Usage analytics** then visualises the accumulated
   history. This feeds a Power BI semantic model for usage reporting until the
   move to Fabric in winter-26 / spring-27.

## 5. Home
Opens on a cross-product overview: Qlik billed-capacity % + reclaim, and Power BI
views/users — populated from the latest scan in each workspace.

---

## 6. Build the standalone `.exe`
On a Windows machine with Python 3.9+:
```bat
build.bat
```
→ `dist\BufabBIGovernanceStudio.exe` — a single file that runs without Python.
(A fresh unsigned `.exe` may trip Windows SmartScreen → *More info → Run anyway*,
or code-sign it.) The build bundles QtCharts and the Azure Key Vault auth path.

---

## 7. Good to know
- **Verify before deleting.** Usage, lineage, capacity and orphan results are
  read from scripts and name-matching — treat them as a prioritized worklist.
- **Secrets** are never written to disk and are scrubbed from the Qlik log.
- The headless CLIs still work: `python cli.py collect|raw-export|analytics`
  (Power BI) and `python qlik_export_cli.py` / `python qlik_capacity.py` (Qlik).

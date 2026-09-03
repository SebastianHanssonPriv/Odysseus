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
- **Shared:** an Output folder (everything is written under it; Power BI data
  lands in `<output>\powerbi_data\`).

Secrets (Qlik API key, Power BI client secret) are **never saved to disk** —
re-enter them each session. Everything else is remembered in
`~/.bufab_bi_studio.json`.

---

## 3. Qlik workspace
Click **Load apps**, tick the apps you want (picks stick across filtering), then
use a tab: **Extract metadata · Comparison analysis · Usage analysis · Apply
master items · Field lineage · Capacity report**. The Capacity tab scans the
whole tenant, shows a dashboard (billed % gauge, duplicate-reclaim and per-space
charts, colour-coded action list) **and** writes `capacity_report_*.xlsx`.

*Apply master items* is the only write path — Dry run is on by default, a backup
is exported first, and a confirmation dialog appears before any real write.

## 4. Power BI workspace
- **Collect** — pick a UTC **date range** (From / To, defaults to the last 7 days
  up to yesterday) and pull each day. Days already collected are skipped, so
  re-running is safe. **Catch up (last 28 days)** backfills everything still in
  Power BI's ~28-day retention window in one click.
- **Raw export** — flatten every collected event into Parquet/CSV + a key map.
- **Usage analytics** — aggregate view events into the usage tables (CSV) and
  show the dashboard (top reports, top users, views-per-day, least-viewed
  reports).

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

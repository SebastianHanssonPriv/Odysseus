# Bufab BI Governance Studio — How to run

One desktop app over **two products**:

- **Qlik** — extract metadata, compare master items across apps, find unused
  items, write master items from a CSV, trace field lineage, and a tenant-wide
  **Capacity** report with an in-app dashboard.
- **Power BI** — collect activity events, export the raw event log, and build
  usage analytics with an in-app **Usage dashboard**.

A left nav rail switches **Home · Qlik · Power BI · Reports · Settings**; the
header, status line, run card, LOG and library folder are shared.

While something is running, a **run card** appears above the workspace: what is
running, how long it has been going, a progress bar once the worker knows its
total, and the run's named steps with the result of each as it finishes. It
lives on the shell, so you can leave the page and the job keeps going. The LOG
panel stays collapsed until you want it.

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
Open **Settings** (bottom of the nav rail; it is a page, not a dialog) and fill
in what you need. It has three sections:

**Connections**
- **Qlik:** Tenant host + API key, with a **Test** button that asks the tenant
  for its space list — a cheap read-only check that the host and key work
  before you start a scan.
- **Power BI:** Tenant ID, Client ID, an Auth mode. Only the credential fields
  that mode needs are shown: a client secret for in-memory mode, the Key Vault
  URL + secret name for Key Vault, nothing at all for managed identity.

**About** reports where the settings file is, which library is configured, and
whether the Barlow fonts were found.

Scheduling and appearance are deliberately not settings: unattended collection
is a Windows Scheduled Task (see "Run it daily" below) and the look is fixed.

- **Library folder:** one folder for everything both products write. Every
  feature still gets its own subfolder, so nothing lands loose in one pile:
  - Qlik → `<library>\Qlik\metadata_export\`, `\comparison_analysis\`,
    `\usage_analysis\`, `\capacity_report\`, `\apply_master_items\`,
    `\field_lineage\` (both the QVD field usage report and the interactive
    trace), `\tenant_usage\`.
  - Power BI → `<library>\powerbi_data\`, with its own `activity_events\`,
    `raw\`, `analytics\`, `model_lineage\` subfolders. This one keeps its
    original name on purpose: `collect_daily.bat` and `.env` point `OUTPUT_DIR`
    at it, so renaming it would break the scheduled daily collection.

  **Pointing it at SharePoint.** Studio writes ordinary files, so the setting
  has to be a path on disk — an `https://` address is not something it can write
  to. What makes the library shared is that the path sits inside a folder the
  OneDrive client syncs.

  Bufab's library is here:

  ```
  https://bufabcom.sharepoint.com/sites/BU-NordicAnalyticsKeyUsers-Testchannel/
    Shared Documents/Global BI Internal/Other BI Solutions/BIGovLib
  ```

  To use it:
  1. Open that library in the browser and click **Sync**. Wait for OneDrive to
     finish.
  2. In Studio: **Settings → Library folder → SharePoint URL**, paste the
     address, click **Find synced folder**.

  Studio reads the OneDrive client's own registry of synced libraries, and
  falls back to searching your user folder for the matching folder chain, so
  you never type the local path. It differs on every machine — something like
  `C:\Users\you\Bufab\BU-Nordic Analytics Key Users - Testchannel\Global BI
  Internal\Other BI Solutions\BIGovLib` — which is exactly why it is looked up
  rather than shared around. If it cannot be found, the library is not synced
  on that PC yet; **Browse...** always works as the manual route.

  Saving a library folder creates the feature subfolders and drops a
  `README.txt` in it explaining the layout, so the SharePoint folder reads as
  an organised place from the start. Everyone who runs Studio against the same
  synced library lands their runs in it, and colleagues without Studio just
  open the workbooks from SharePoint.

  Upgrading from a version with separate **Qlik** and **Power BI** output
  folders? The library is pre-filled from whichever of those was set, so there
  is nothing to re-pick. Qlik output now sits one level deeper, under
  `Qlik\`; earlier exports stay where they are.

Secrets (Qlik API key, Power BI client secret) are **never saved to disk** —
re-enter them each session. Everything else is remembered in
`~/.bufab_bi_studio.json`.

---

## 3. Qlik workspace
Click **Load apps**, then **Change scope** to pick the apps you want. The scope
is one global selection: every task reads the same one, it survives closing the
app, and the bar at the top of the workspace always says what is in it. Apps
that have since been deleted from the tenant drop out of the scope on the next
load rather than lingering.

In the picker, **Published only** and **Reloaded < 30 d** appear only when the
tenant's Items API actually returned those fields; when it does not, they are
switched off rather than silently matching nothing. **Cancel** leaves the scope
exactly as it was, so you can open the picker to look around without losing a
selection.

Then open a task from the hub: **Extract metadata · Comparison analysis · Usage &
leanness · Apply master items · QVD field usage · Trace a field · Capacity
report · Tenant QVD usage · Landed QVD impact · Diagnose visibility**. One task fills the page at a
time; **← All tasks** in the breadcrumb goes back to the hub. Hover a button or
checkbox for the full explanation of what it does.

Every task page ends in a pinned **action bar**: one main button bottom-right,
anything secondary to its left, and a status line saying what the run will
cover. When the main button is greyed out the bar says why — "Needs at least
two apps in scope", "Choose a measures and/or dimensions CSV" — so you are not
clicking to find out. The Capacity report scans the whole
tenant, shows a dashboard (billed % gauge, duplicate-reclaim and per-space
charts, colour-coded action list) **and** writes `capacity_report_*.xlsx`.

*Apply master items* is the only write path. Its bar has **Dry run** and
**Apply for real** side by side: dry run reports every change and writes
nothing, and the real one exports a backup first and asks for confirmation.

The two field tasks are independent:
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
  QVD ultimately stops — mirroring the Power BI Model lineage task's goal
  (source, all the way to the final model) but for Qlik. This is slower: it
  opens every QVD's producing app via Qlik's own lineage graph, one time per
  distinct QVD across the whole scan (not per field).
- **Trace a field** — pick one app and one field to
  see the pipeline that field took *into* this app, optionally extended
  upstream across apps via Qlik's own lineage graph.

The **Tenant QVD usage** task answers the same two questions as the QVD field usage
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

What "used in a report" now reads. Every expression-bearing property of every
object on every sheet, found by walking the object's whole property tree rather
than four known paths. That means colour-by-expression, segment colours,
dynamic labels, show/hide and calculation conditions, sort-by expressions,
subtitles and footnotes, and properties invented by extension objects all count
as usage. It used to read only a chart's inline dimension and measure
definitions plus its title, so a field used solely to colour a bar, or to
decide whether a chart appears, was reported as an unused candidate. A dynamic
`$(...)` expression is still opaque — that caveat is unchanged and no text
parse can close it.
Space type is read from Qlik Cloud's Spaces API as-is; confirm actual access
level in the Qlik admin console before relying on it.

**Known blind spot — apps in another user's Personal space.** Qlik Cloud's
Items API (what every app list in this tool, including this scan's root-app
discovery, is built from) is scoped by space membership, and a Personal
space has exactly one member — its owner. This API key, tenant-admin or not,
likely cannot see an app sitting in someone else's Personal space at all —
not "with less detail," just never listed — so an extractor/staging app
placed there can be invisible to every Qlik feature in this tool, with no
error, unless it happens to surface as a producer node in another (visible)
app's native lineage graph. **Diagnose app visibility**, below the Tenant
usage panel, tests one specific app GUID you already suspect against four
layers — the Items API, a direct REST lookup by GUID, an Engine API session,
and (optionally, given a downstream consumer app's GUID) whether it appears
in that consumer's native lineage graph at all — and prints a verdict. This
confirms or rules out the hypothesis for that one app; it does not (yet)
enumerate every Personal-space app tenant-wide. If confirmed, the real fix is
organizational: move the app into a shared/managed space with proper
delegated ownership, since a business-critical extractor tied to one
person's account lifecycle is a continuity risk independent of what this
tool can see.

### Comparison analysis

Two things are reported: the same **name** carrying different calculations
across apps (a consistency risk), and the same **calculation** carrying
different names (a consolidation opportunity). Both rest on one judgement —
when are two definitions the same calculation — so the matching rule is worth
stating.

Treated as the **same**: whitespace and line breaks anywhere outside a string
literal, letter case, `//` and `/* */` comments, and optional brackets round a
plain identifier, since `Sum([Sales])` and `Sum(Sales)` are the same expression
in Qlik.

Treated as **different**: whitespace inside a string literal, because
`'United Kingdom'` and `'UnitedKingdom'` are not the same value; a bracketed
name that contains a space, where the brackets are required rather than
optional; and single versus double quotes, because in Qlik `"X"` is a field
reference while `'X'` is a string.

That last group matters. The rule used to strip every space including those
inside literals, so two measures that genuinely differed by a country name were
reported as identical — the report hid the very inconsistency it exists to find.
Brackets and comments went the other way and turned one calculation written
three ways into a name conflict to chase.

Variables are expanded first where the definition is a plain literal; a
parameterised `$(f(x))` or an active `$(=…)` reference is left as written and
flagged in the **Unexpanded vars?** column, because substituting it would be a
guess.


### Landed QVD impact
A "landed" QVD is one written by an **extractor**: an app whose load script both
`STORE`s a QVD and pulls its data from outside Qlik. Those are the apps that
bring data onto the platform, and the classification comes from the script, not
from the app's name — the extracting apps on this tenant are called things like
"ABC Inventory QVD creator", and a name rule missed them.

The evidence is in the report. The **Extractor apps** sheet lists every app the
scan classified as an extractor and why, for example `external DB (SQL), stores
4 QVDs`, so a wrong call can be spotted instead of quietly shaping every other
sheet. Three script shapes are deliberately *not* extractors: an app that reads
only QVDs already in Qlik (a transform layer), a `BINARY` load of another app,
and an app that stores nothing.

This task answers, for every field in every landed QVD, what actually depends on
it. Tenant-wide, no scope needed, but it opens every app, so allow time.

Each field gets one of five states **per consuming app**:

| State | Meaning |
|---|---|
| `in model, referenced` | Reaches the app's data model **and** a measure, dimension or visual references it. This is what "has impact" means. |
| `in model, unused` | Reaches the model, nothing references it. Loaded and carried, paying storage and reload time for nothing. |
| `script only` | The load script reads it but it never reaches the final model: dropped, joined away, aggregated into something else, or renamed past recognition. |
| `in model, other table` | Present under a different table than the script suggested. Check it; not a verdict either way. |
| `wildcard` | A wildcard folder load, whose fields no script parse can enumerate. The QVD's fields are simply unknown from the script. |

Five sheets: **Field impact** (one row per QVD field, rolled up across every app
that reads it, with `Has impact`, whether it is renamed anywhere, and whether it
is computed rather than carried), **Field reach** (one row per field per app),
**Landed QVDs** (including any nothing reads at all — usually the most
actionable finding), **Criticality**, and a **Summary** that states the limits.

**Criticality is structural, not usage.** Qlik Cloud exposes no per-app
opened-by-user telemetry through any API — not the Audits API, not the
monitoring apps. That was verified against this tenant over 500 real audit
events and against Qlik's own Consumption Monitor: only reload/ETL activity and
capacity billing are available. So there is **no DAU/MAU/QAU/YAU to report**,
and a figure claiming to be one would be invented. The tier is built instead
from how much the estate depends on the app: published (+2), feeds other apps
with its own QVDs (+2, or +3 for three or more), 15+ visual objects (+1) or 40+
(+2), references 50+ landed fields (+1), reloaded within 7 days (+1), no reload
in 90 days (-1). High from 5, Medium from 3. The Criticality sheet shows every
component per app, so a tier can be argued with.

**A missing input scores 0, not a penalty.** `published` and the last reload
time come from the Items API and are best-effort: absent means the tenant did
not report it, not "no" and not "never". Both used to be scored as negatives —
an unreported publish state counted as unpublished, and an unreported reload
cost a point — so absent telemetry could push a live app down a tier, and in
testing it did exactly that in both directions (Low↔Medium and
Medium↔High). Those rows now carry an **Evidence** column reading
`provisional, missing last reload`, the Published column reads `not reported`
rather than `No`, and the summary counts how many tiers rest on an incomplete
input. A provisional tier can only rise once the missing input arrives.

One structural bias to know: the +3 for feeding other apps can only be earned
by an app that writes QVDs, so a pure reporting app is scored on a shorter
scale than a transform app. That is inherent to measuring dependency rather
than use, and it is why the components are all shown rather than just the tier.

Two limits worth knowing before acting on it. A field marked without impact is
**not** automatically safe to drop: check its state, and check the wildcard and
other-table rows by hand. And any app this API key cannot open appears on no
sheet, so its dependencies are invisible here — personal-space apps owned by
other people are the usual cause, and **Diagnose visibility** confirms it for a
given app.

An extracting app that does not appear at all is one whose script could not be
read — it is listed as skipped in the LOG rather than treated as "not an
extractor", because an unreadable script is unknown, not a no.

## 3b. Reports
**Reports** in the nav rail is the library. Every scan, export and analysis
files a small `.bbgs.json` record next to its workbook, and this page reads the
library folder to list them, newest first, with each run's headline numbers and
how they moved since the previous run of the same type.

- **All / Qlik / Power BI** filters the list.
- **Open workbook** opens the .xlsx; **Open folder** opens where it sits.
- **Refresh** re-reads the folder. Use it to pick up runs a colleague has
  written into the same synced library since you opened the page.
- **Clean up old** lists everything older than 12 months and asks before
  deleting. Nothing is removed automatically: the library is shared, so a run
  you delete is gone for everyone.

Because the index is nothing but the folder, **two people pointing Studio at
the same synced library see each other's runs** with no server involved. A
colleague without Studio just opens the workbooks from SharePoint.

The **Home** overview shows the three newest runs and a link into the library.

## 4. Power BI workspace
Same shape as the Qlik workspace: a hub of five tasks, one page each, with
**← All tasks** to get back and the detail on hover.

- **Collect activity events** — pick a UTC **date range** (From / To, defaults to the last 7 days
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

**Why there is a third state.** With that tenant setting off, every DAX
expression comes back empty. The reference check then finds nothing, and
*every column in every dataset* used to be reported as having no DAX
reference — a tenant-wide claim that nothing is used, built entirely on the
fact that nothing was returned. Model lineage and Dataflow field impact now
count the DAX expressions a dataset actually returned: zero means the check
could not run, and those columns read `not known` rather than `No`. The log
says which datasets those were while the scan runs.


### Dataflow field impact
The Power BI counterpart of **Landed QVD impact**. A Gen1 dataflow entity is
Power BI's answer to a QVD — a table staged by a separate artifact and then
read by semantic models — so "landed" here means a model table whose source
resolves through at least one dataflow hop. One tenant-wide Scanner walk, the
same one Model lineage uses.

Each field gets one of five states **per model table**:

| State | Meaning |
|---|---|
| `in model, referenced by DAX` | A column of the model table, and a measure or calculated column's DAX references it. This is "has impact". |
| `in model, no DAX reference` | A column of the model table with no DAX reference anywhere in the dataset. **A shortlist to check, not a finding** — see the limit below. |
| `in model, DAX usage not known` | A column of the model table in a dataset that returned **no DAX at all**, so there was nothing to check it against. Either the dataset holds no measures and no calculated columns, or the tenant setting *Enhance admin APIs responses with DAX and mashup expressions* is off. A gap in the evidence, never a reason to drop a column. |
| `in the dataflow, not in the model` | The dataflow's M code selects the field but no model table exposes a column by that name: dropped, renamed, or folded into something else. |
| `dataflow columns not narrowed` | The dataflow's M code passes everything through, so what it carries cannot be read from it. The model's own columns are still listed. |
| `source not resolved` | The table's source could not be chased far enough to say. |

**Two differences from the Qlik report, both in the labels.**

*The "used" signal is weaker.* Qlik's Engine API exposes every measure,
dimension and visual expression, so "referenced" there means referenced by
something a user sees. Power BI's Admin APIs expose **no visual or report-page
content at all**, so the strongest signal available is whether a DAX expression
references the column. A column dropped straight onto a table, chart or slicer
with no calculation involved is invisible to this scan and to every other
API-based tool. That is why every label here says "referenced by DAX" and the
Qlik one says "referenced".

*The usage signal is real, but short.* Unlike Qlik, Power BI does expose
per-report view events, and Studio collects them — so model criticality here
uses **actual views and actual distinct users**, rolled up from each report to
the semantic model it is built on. The ceiling is retention: Microsoft keeps
activity events about 28 days, so the window is only as wide as the history you
have collected, and the report states how many days that is rather than
implying more. There is no quarterly or yearly figure until that much history
exists. With no events collected the scan scores on structure alone and says
so — a model with no events is **unmeasured, not unused**.

**Two things about the daily tables.** Days are **local calendar days** in
`Europe/Stockholm`, not UTC days. Activity events are timestamped in UTC, and
bucketing them by their UTC date pushes the last hour or two of every local
evening onto the previous day — a view at 00:30 Stockholm time on Tuesday is
23:30 UTC Monday. Set `BIGOV_TZ` to any IANA zone name to change it; an unknown
or unavailable zone falls back to UTC and says which it used.

And **a day nobody collected is not a day nobody used**. The accumulated files
*are* the dataset, so a day the scheduled task missed is simply absent, and it
shows in a daily table as zero views. Usage analytics now names those days:
which are missing from the collected range, which were collected but hold no
events, and how many view events could not be attributed to a user or a time.
Because retention is about 28 days, a gap older than that cannot be backfilled
— which is the reason the gap is worth telling you about on the day it appears.

The tier: 3+ reports built on it (+3) or 1–2 (+2); 100+ views in the window
(+3), some views (+2), no views (−1); 5+ distinct users (+1); 10+ tables (+1).
High from 5, Medium from 3. The Criticality sheet shows every component per
model, including whether usage was measured at all.

Five sheets, mirroring the Qlik report: **Field impact**, **Field reach**,
**Dataflow entities**, **Criticality**, **Summary**. A field that reaches no
model shows a blank criticality rather than inheriting the tier of the model
that dropped it.

Everything depends on the tenant setting **Enhance admin APIs responses with
DAX and mashup expressions**. Without it every table comes back as
`no_expression_available` and nothing resolves. Gen2 (Fabric) dataflows and
lakehouse shortcuts are not Gen1 dataflow hops and will not appear here.

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
   - Set `OUTPUT_DIR` in `.env` to your app's `<library folder>\powerbi_data`
     so the in-app dashboard reads the scheduled collections too.
   - **Task Scheduler -> Create Task** -> Trigger: Daily, ~06:00 local -> Action:
     *Start a program* -> Program: `collect_daily.bat`, "Start in" = this folder.
     Tick *Run whether the user is logged on or not*.
   Each run appends one day; **Usage analytics** then visualises the accumulated
   history. This feeds a Power BI semantic model for usage reporting until the
   move to Fabric in winter-26 / spring-27.

### Why a second tenant-wide scan is fast
Three Qlik tasks need every app's load script: the capacity report (twice over,
once for the external-load profile and once for orphan detection), Tenant QVD
usage, and Landed QVD impact. Reading one is a separate engine session, so doing
them one at a time on a couple of thousand apps is minutes of pure network wait.

Two things fix that, and neither needs any setup:

- **The scripts are read concurrently**, six sessions at a time. The limit is
  the tenant's own concurrent-session and rate limits rather than your PC, which
  is why it is six and not sixty; `SCRIPT_WORKERS` in `qlik_core.py` if that
  ever needs tuning.
- **What each script says is remembered for the session** — which QVDs it writes
  and reads, and whether it loads external data. Each entry is keyed on the
  app's last reload time, so an app that has reloaded since is re-read and one
  that has not is not. Within a single capacity run that halves the work
  outright; run the capacity report and then Landed QVD impact and the second
  one re-reads only what changed.

The cache holds the parsed facts, not the scripts: roughly 1 MB for a whole
tenant rather than tens of MB, and it contains QVD names rather than the
`LIB CONNECT TO` lines a script carries. It lives in memory only, disappears
when you close Studio, and is dropped if you point Studio at a different
tenant. Nothing is written to disk, so there is nothing to clear or govern.

It buys nothing for a single scheduled scan once a day — anything that reloads
nightly will be re-read anyway. It buys a lot for running two scans in a row,
or re-running one after a failure.

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
or code-sign it.) The build bundles QtCharts, the Azure Key Vault auth path, and
the `fonts\` folder. The UI uses **Barlow** / **Barlow Condensed**; those `.ttf`
files are not committed — see `fonts\README.md` for the three files to drop in.
Without them the app still runs and just falls back to Segoe UI, logging one line
at startup.

---

## 7. Good to know
- **Verify before deleting.** Usage, lineage, capacity and orphan results are
  read from scripts and name-matching — treat them as a prioritized worklist.
- **Secrets** are never written to disk and are scrubbed from the Qlik log.
- The headless CLIs still work: `python cli.py collect|raw-export|analytics`
  (Power BI) and `python qlik_export_cli.py` / `python qlik_capacity.py` (Qlik).

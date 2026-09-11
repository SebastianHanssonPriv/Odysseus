# Redesign spec

Source of truth for the UI redesign. Lifted from the design canvas
(13 screens, `1a`-`1m`) and its "Handoff notes for Claude Code" so the plan
survives outside the chat that produced it.

Screens, for reference: `1a` estate overview · `1b` Qlik hub · `1c` task page ·
`1d` running state · `1e` report viewer · `1f` scope sheet · `1g` report library +
share sheet · `1h` the other four Qlik tasks · `1i` Power BI collect ·
`1j` Power BI usage analytics · `1k` settings · `1l` empty/first-run/error states ·
`1m` adaptive (900 / 1920).

## Tokens

Live in `widgets.py`. Applied in commit "Apply the redesign's visual layer".

| Token | Value | Used for |
|---|---|---|
| `BG` | `#F2F2F3` | ground |
| `SURFACE` | `#E9E9EA` | inputs, inset panels |
| `BAR` | `#EBEBEC` | action bars, scope bar |
| `RAIL` | `#1D2D3D` | nav rail, title bar |
| `TEXT` | `#1D1F20` | body text |
| `MUTED` | `rgba(29,31,32,.55)` | secondary text |
| `LINE` / `FAINT` | `rgba(29,31,32,.16)` / `.08` | borders, hairlines |
| `ACCENT` / hover | `#5980A6` / `#416180` | primary action |
| `GOOD` / `WARN` / `BAD` | `#4F7F63` / `#8C6A34` / `#9D5148` | status |

Radius 0 everywhere. Type: Barlow Condensed 600 for headings, section labels,
buttons and numbers; Barlow for body. Both ship as TTF, registered via
`QFontDatabase.addApplicationFont`, Segoe UI as fallback (see `fonts/README.md`).

## Breakpoints

One `resizeEvent` on `MainWindow`. Content max-width 1600 so an ultrawide does
not stretch tables.

| Width | Layout |
|---|---|
| `< 1040` | rail 56px, icons only (tooltips); task grid 2 columns; scope bar collapses to a count + Change; detail panes become overlay sheets; action bar stays pinned |
| `1040-1439` | rail 220px labelled; task grid 2-3 columns; task pages single column, side column drops below the form |
| `1440-1699` | rail + content; task grid 3 columns; task pages get their 300px side column |
| `>= 1700` | a third 380px column appears (activity / schedule / preview) |

## Structural changes, in build order

1. **Router, not a QTabWidget.** Keep the `QStackedWidget` but add a page per
   task and a small history stack so the back arrow works. `qlik_view.py`
   splits into a hub widget plus one widget per task.
2. **Shared scope object.** Selected apps move out of the Qlik view onto the
   shell, so every task reads the same selection and the picker becomes one
   reusable sheet (`1f`) instead of a permanent table.
3. **Report records.** Each run writes a JSON manifest next to its workbook
   (type, scope, timestamp, headline numbers, path). The library reads the
   folder; the viewer renders from the manifest. This is what makes results
   persist, diff and share.
4. **Log becomes a drawer.** Keep the existing `sig_log` plumbing, but the
   panel is collapsed by default and the run card carries named steps instead.
   Nothing about the worker threads changes.
5. **Sticky action bar.** One primary action per page, bottom-right, 40px tall,
   always visible. Secondary actions sit to its left.
6. **Settings as a page.** The modal becomes a rail destination with its own
   sub-nav, and only the credential fields the chosen auth mode needs are shown.

## Settled decisions

- Sharing publishes into the department's synced SharePoint library: the
  workbook plus its manifest, so another Studio install shows it in the library
  and anyone else opens the workbook in the browser.
- Studio has no user accounts, so reports carry no author.
- Comparison between runs is headline numbers only.
- Scope is global and persistent: apps for Qlik, workspace + report for Power BI.
- Retention is 12 months and is a setting.
- Scheduling assumes Windows Task Scheduler, which `HOW_TO_RUN.md` already
  documents for Power BI collection.

## One library folder

Settings has a single **Library folder**. Everything both products write goes
under it, each feature in its own subfolder:

```
<library>/
  Qlik/<feature>/          metadata_export, capacity_report, field_lineage, ...
  powerbi_data/<feature>/  activity_events, raw, analytics, model_lineage
```

`powerbi_data` deliberately keeps its original name and its place at the library
root rather than moving under a `Power BI/` folder: `collect_daily.bat` and
`.env` point `OUTPUT_DIR` at `<library>\powerbi_data`, so renaming it would
break the scheduled daily collection and orphan the accumulated event history.

Point the library at a synced OneDrive or SharePoint path and it doubles as the
shared library of the settled decisions above. The per-product `output_dir_qlik`
and `output_dir_powerbi` settings are gone; `_load_settings` migrates from
either one.

Bufab's library is
`.../sites/BU-NordicAnalyticsKeyUsers-Testchannel/Shared Documents/Global BI
Internal/Other BI Solutions/BIGovLib`. `sharepoint.py` turns that URL into the
local synced path: the OneDrive client records every synced library in
`HKCU\Software\Microsoft\OneDrive\Accounts\*\Tenants\*` as local path ->
server URL, and failing that the decoded folder chain is searched for under the
user's profile. The local path differs on every machine, so it is never typed
or shared. Saving a library also lays out the feature folders and writes a
README.txt describing them, because the folder is something colleagues open in
SharePoint.

## The running run

`widgets.RunCard` lives on the shell, above the workspace stack, hidden until
something runs. `busy_begin(title, steps)` starts it from the GUI thread;
workers then call `run_step`, `run_progress`, `run_detail` and `run_finish`,
all of which only emit `sig_run` because they are called from worker threads.
The meter stays indeterminate until a worker reports a total, since a
made-up percentage is worse than an honest spinner.

## Action bars

Each task page ends in an `ActionBar` pinned outside its scroll area, so the
button you came to press never scrolls away. The status line is not decoration:
it carries the run's scope, and when the primary is disabled it says what is
missing instead of letting someone click and collect a warning dialog.

| Task | Needs | Says when it cannot run |
|---|---|---|
| Extract metadata | 1+ app, 1+ item type | nothing in scope / tick an item type |
| Comparison analysis | 2+ apps | needs at least two apps in scope |
| Usage & leanness | 1+ app | nothing in scope |
| Apply master items | 1+ app, a CSV | nothing in scope / choose a CSV |
| QVD field usage | 1+ app | nothing in scope |
| Trace a field | exactly 1 app, a field | needs exactly one app / pick a field |
| Capacity report, Tenant QVD usage | nothing | (always ready, tenant-wide) |
| Diagnose visibility | an app GUID | paste the app GUID to test |
| Collect activity events | From <= To | From is after To |
| Raw export | Parquet and/or CSV | tick Parquet and/or CSV |
| Usage analytics, Model lineage | nothing | (always ready) |

Two consequences worth noting. **Apply master items** lost its "Dry run"
checkbox: the safe path and the real one are now two buttons side by side, so
which one you are about to take is visible rather than folded into a tick you
may have left off. And **Field lineage** became two tasks, *QVD field usage*
and *Trace a field*, because they need different scopes (any number of apps vs
exactly one) and so cannot share one primary action. The spec drew them as a
segmented control on one page; two hub cards get the same result with fewer
moving parts.

## Scope

`shell.scope` is a set of app GUIDs and `shell.apps` the loaded list; every
Qlik task reads `shell.scope_targets()`. The scope is saved to settings, so it
survives a restart, and `set_apps` drops any GUID the tenant no longer returns.
Only `ScopeSheet` writes it, and only on accept.

`qlik_core.list_apps` now also keeps `reloaded` and `published` from the Items
payload it was already fetching. Both are best-effort: Qlik has moved these
fields between API versions, so a filter that depends on one is hidden when no
app in the list has it, rather than matching nothing.

## Report records

`<workbook>.bbgs.json` sits beside each workbook:

```json
{ "schema": 1, "product": "Qlik", "type": "capacity_report",
  "title": "Capacity report - full tenant", "created": "2026-09-10T06:00:00",
  "scope": "214 apps", "duration_s": 401,
  "file": "Qlik/capacity_report/capacity_report_20260910_060000.xlsx",
  "headline": [{"label": "Billable app data", "value": 1840000000000,
                "unit": "bytes", "display": "1.7 TB"}] }
```

`file` is relative to the library root, never absolute: the same synced library
is mounted at a different local path on every machine, and an absolute path
would break the moment a colleague opened it.

The library index is the folder. There is no database, no server and no state
outside the manifests, so two people pointing Studio at the same synced folder
see each other's runs. That also means the settled "publish to SharePoint"
sharing needs no share sheet: writing into the library IS publishing.

Deletion and retention are never automatic. `Clean up old` lists what is older
than 12 months and asks first, because the library is shared and a run deleted
here is gone for everyone.

## Status

- [x] Tokens, QSS and widget set
- [x] 1. Router: both workspaces are a task hub plus one page per task, with a
      back arrow. `widgets.TaskHub` is the shared implementation.
- [x] 2. Shared scope object + scope sheet: `shell.scope` is the single
      selection, persisted in settings; `scope_sheet.py` holds the picker
      (`ScopeSheet`, screen 1f) and the one-line summary that replaced the
      permanent table (`ScopeBar`). Power BI has nothing to scope yet - all
      four of its tasks are tenant-wide or date-ranged - so the settled
      "workspace + report" scope waits until a task needs it.
- [x] 3. Report records: every run writes a JSON manifest beside its workbook
      (`reports.py`), and the Reports rail page lists the library with each
      run's headline numbers and how they moved since the previous run of the
      same type (`reports_view.py`). Home shows the three newest. No separate
      report viewer: the detail already lives in the workbook, and the manifest
      carries only the headline numbers the spec says a diff compares.
- [x] 4. Log drawer + named run steps: the log panel is collapsed by default,
      and `widgets.RunCard` replaced the indeterminate bar with what is
      running, how long it has been going, a determinate meter once a worker
      reports counts, and the run's named steps with each one's result. One
      card on the shell, because the design's own note says you can leave the
      page and the job keeps running.
- [x] 5. Sticky action bar: `widgets.ActionBar`, pinned below each task page
      outside the scroll area. One primary bottom-right at 40px, secondaries to
      its left, and a status line that says what the run will cover - or why
      the primary is greyed out. `QlikView.refresh_ready()` and
      `PowerBIView._refresh_bars()` keep all thirteen honest.
- [x] 6. Settings as a rail page: `settings_view.py`, a rail destination with
      a Connections / Library / About sub-nav and a Save bar. Only the
      credential fields the chosen Power BI auth mode needs are shown, and the
      Qlik key has a Test button. The modal is gone. The spec also drew
      Schedules and Appearance; neither exists, because scheduling is Windows
      Task Scheduler (nothing for Studio to configure) and there is no theme
      to switch.
- [~] Breakpoints: rail width and both hubs' column counts react to window
      width. The `< 1040` rule is written but still not reachable; see the
      measured minimum window width below.

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
- [ ] 2. Shared scope object + scope sheet
- [x] 3. Report records: every run writes a JSON manifest beside its workbook
      (`reports.py`), and the Reports rail page lists the library with each
      run's headline numbers and how they moved since the previous run of the
      same type (`reports_view.py`). Home shows the three newest. No separate
      report viewer: the detail already lives in the workbook, and the manifest
      carries only the headline numbers the spec says a diff compares.
- [~] 4. Log drawer: the panel is collapsed by default. Named run steps still
      to do.
- [ ] 5. Sticky action bar
- [ ] 6. Settings as a rail page
- [~] Breakpoints: rail width and both hubs' column counts react to window
      width. The `< 1040` rule is written but still not reachable; see the
      measured minimum window width below.

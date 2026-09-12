# Bufab BI Governance Studio — developer notes

Read this before changing anything analytical. It is not a codebase tour
(`HOW_TO_RUN.md` is the feature documentation); it is the short list of rules
this code is built on, each of which exists because breaking it produced a
wrong report.

```bat
python tests\run_all.py
```

Twenty suites, no framework, no extra dependency. Run them after touching a
parser, a classifier, a score or a total. `tests\README.md` says what each one
protects and which defect it pins.

---

## 1. Absence of evidence is never evidence

This is the rule. Sixteen review cycles found the same mistake eleven times
wearing different clothes, and every one of them made a report state something
false rather than state nothing.

| Where | What absence meant | What it was read as |
|---|---|---|
| `extractor_reason` | app not named "extractor" | not an extractor |
| script parsers | a name in a comment | live code |
| `_walk_struct` | a property path we did not know | field not used |
| `score_app` | tenant did not report publish state | not published |
| `dax_referenced_fields` | tenant setting off, no DAX returned | nothing is used |
| `list_data_files` | no connection named in the request | 8 files exist |
| `detect_orphans` | reader builds the filename at run time | nothing reads it |
| `scan_model_lineage` | a Scanner batch failed | those workspaces hold nothing |
| `apply_master` | title index collapsed a duplicate | one item with that name |
| `attach_report_usage` | case-folded names collided | last one wins |

So, concretely:

- **Three states, not two.** `None` means "not known", and it must survive into
  the report as its own state with its own explanation. `STATE_UNKNOWN`,
  `STATE_DAX_UNKNOWN`, `used_in_dax is None`, `basis="provisional"` are all
  this. Never `bool(x.get("published"))` on a field the tenant may not report.
- **Say what you did not measure.** Every scan that can be partial reports its
  coverage *in the output*, not only the log: `coverage`/`low_confidence` in
  orphan detection, `SCAN COVERAGE` at the top of the Model lineage summary,
  `days_missing` in Power BI analytics, the `Evidence` column on Criticality,
  the `Extractor apps` sheet.
- **A gap never becomes a penalty.** An unreported reload scores 0, not −1.

## 2. Classify on behaviour, not on names

An extractor is an app whose script stores a QVD and loads from outside Qlik.
It is not an app with a word in its title. If a feature needs a convention to
work, it will not work: the real tenant's extractors are called "ABC Inventory
QVD creator". Where a name *is* the only signal available (`_norm_name` for
duplicate app clusters), say "candidates" and show the evidence.

## 3. Comments are not code, and strings are not code

Three separate parsers had to learn this. Before any text parsing:

- Qlik load scripts → `_strip_script_comments` (keeps the `//` in `lib://`)
- Qlik expressions → `_split_expression` (drops comments and literals, but
  keeps literals when the expression contains `$(`, because a literal can
  become code)
- Power Query M → `strip_m_comments` (respects `""` escaping, keeps
  `"https://…"`)
- Comparison keys → `_tight` (whitespace outside literals only; whitespace
  *inside* `'United Kingdom'` is a real difference)

And resolve `$(vVar)` before matching filenames, or the app that writes a QVD
and the apps that read it end up discussing different names. A name still
holding `$(` after resolution is **unresolved**, reported as such — never
invented into a filename.

## 4. Compute totals where the full data is

Display lists are truncated to `top_n`. Aggregates must be computed in the
analysis function over everything, then returned
(`dedupe_savings_total_bytes`). Summing a truncated list at the display layer
silently understated a headline figure people budget against.

## 5. A write never guesses

`apply_master` is the only thing that writes to a production app.

- Match on the **exact** title. A near miss (case or padding) is `AMBIGUOUS`:
  write nothing and name both spellings.
- A title carried by several items is acted on **in full**, and the log says so.
- Dry run must stay exact: report everything, call nothing.
- Back up before writing; save only when something changed.

## 6. Say which unit, and which day

- The billed meter carries a `unit` field. Read it (`meter_is_bytes`,
  `meter_amount`). A percentage is a ratio and safe; an absolute figure is not.
- Power BI activity events are UTC. Days are **local** calendar days
  (`REPORTING_TZ`, `BIGOV_TZ`), or the last hour of every local evening lands
  on the previous day.

## 7. Cost, and what it is allowed to buy

Quality first, but exhaustive is a cost, not a goal: **99.99% in 20 minutes
beats 100% in 3 hours** — provided the missing 0.01% is labelled. What is not
tradeable is determinism. The concurrent data-file walk merges results in
connection order, not completion order, so the same tenant gives the same
answer every run.

Everything tenant-wide goes through `qlik_core.request_json`: it asks for gzip
(86% less on the wire, measured) and retries 429/503 on `Retry-After`. A 404 is
**not** retried — most apps have no lineage graph, so a miss must cost one
call. Parsed script facts are cached per session in `script_cache` keyed on
`(guid, reloaded, FACTS_VERSION)`; **bump `FACTS_VERSION` when you change a
parser.**

## 8. Layers

`qlik_core`, `qlik_capacity`, `qlik_landed`, `script_cache`, `mashup_parser`,
`model_lineage`, `pbi_landed`, `analytics` hold the logic and import no GUI.
The views (`*_view.py`, `widgets.py`, `studio_app.py`) hold no analysis.

Anything that only needs a client for a type annotation imports it under
`TYPE_CHECKING`. Importing `powerbi_client` for real pulls in
`auth → azure.identity → cryptography`, which made pure parsing modules
unloadable and untestable wherever that SDK was incomplete.

One more, learned the hard way: **module-level code placed inside a class body
silently ends the class.** It parses, it imports, and every method after it
stops being a method. `tests/test_objects.py` asserts `QlikExporter` still has
its twelve.

## 9. Reports are worklists

Every output says it is a prioritised worklist to verify, not a verdict —
because no text parser can see inside a dynamic `$(…)` expression, and Power
BI's Admin APIs expose no visual content at all, so a raw column on a chart is
undetectable. Keep that framing. A tool people trust to be certain is more
dangerous than one they trust to be useful.

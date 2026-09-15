# tests

```bat
python tests\run_all.py            all suites
python tests\run_all.py parser     suites whose name contains "parser"
python tests\run_all.py -v         show each suite's own output
```

Exit code is 0 when everything passes and 1 when anything fails, so this
works as a pre-commit or CI gate.

## Why these exist, and why they look like this

Every suite here was written because a **real defect was found in this app**,
not to reach a coverage number. Each one pins the behaviour that was wrong, so
the same mistake cannot come back quietly.

There is no test framework and no extra dependency. A suite is an ordinary
script that asserts, prints what it checked, and exits non-zero on failure.
That is deliberate: the printed output states what the defect *was*, which is
worth more to whoever reads it next than a dot on a progress line. Run one
directly to see it.

```bat
python tests\test_parser.py
```

## What each suite protects

| Suite | The defect it pins |
|---|---|
| `test_parser` | Load-script parsing read comments as live code and left `$(vVar)` paths unexpanded, so `stores` held QVDs from commented-out lines and missed the one real `STORE`. |
| `test_extractor` | Extractors were identified by `"extractor" in app_name`. The real tenant's extractors are called "QVD creator", so every QVD they land was missing from the report. |
| `test_objects` | Usage read four property paths on a sheet object, so a field used only in a colour expression, calc condition or sort was an "unused candidate". Also guards that `QlikExporter` still has its methods, after module-level code once landed inside the class body and silently ended it. |
| `test_refs` | A field named in a `//` comment counted as referenced, so a stale comment could keep a genuinely unused field off the report. |
| `test_compare` | The comparison key stripped whitespace inside string literals, so `'United Kingdom'` and `'UnitedKingdom'` compared identical - the report hid the inconsistency it exists to find. |
| `test_score` | Criticality scored an unreported publish state as "not published" and a missing reload as −1, so absent telemetry pushed live apps down a tier. |
| `test_apply` | The write path indexed master items by title, and Qlik allows duplicates: delete removed one copy and reported success, update left the other stale, and one name twice in a CSV was created twice. Also pins that a near-miss name is refused rather than guessed, and that dry run writes nothing. |
| `test_capacity` | The duplicate-reclaim headline was summed from a list truncated to the top 25 clusters, understating a number people budget against. |
| `test_orphan` | A QVD whose only reader builds its name at run time appeared on a list headed "no app reads or writes them". |
| `test_mashup` | A commented-out `Sql.Database(...)` in Power Query M was parsed as a live source, so lineage named a decommissioned server as in use. Pins that a URL is not mistaken for a comment. |
| `test_dax` | With the DAX tenant setting off, every expression returns empty, so every column in every dataset read as "no DAX reference" - a tenant-wide claim built on nothing being returned. |
| `test_analytics` | Power BI usage was bucketed by UTC day, shifting the last hour of every local evening onto the previous day; and a day nobody collected read as a day nobody used. |
| `test_df` | `list_data_files` asked for data files without a connection, which returns only the caller's personal space: 8 files on a tenant holding thousands. |
| `test_df_parallel` | That walk is now concurrent, and must stay deterministic - results merge in connection order, not completion order, so a duplicate basename resolves the same way on every run. |
| `test_gzip` | No REST response was ever compressed, because urllib sends no `Accept-Encoding`; and `collapse=true` silently dropped QVD nodes from lineage graphs. |
| `test_429` | The lineage call had no retry, and the tenant-usage scan calls it once per published app - so one 429 silently dropped lineage for every app after it. A 404 must *not* be retried: most apps have no lineage graph at all. |
| `test_meter` | The billed-capacity gauge ran usage and limit through a byte formatter while the `unit` the tenant reported was captured and never read. Also pins that the check is an exact match, since "gigabytes" contains "byte". |
| `test_scan_coverage` | A failed Scanner batch drops up to 100 workspaces, and the count went to the log only - so a workbook built on 85% of the tenant looked exactly like one built on all of it. Coverage now leads the Summary sheet. |
| `test_case_collision` | Usage matching folds field names to lowercase, so `Region` and `region` share a key and whichever came last decided the answer. The answers are now OR-ed: if any spelling is referenced, the name is. |
| `test_docs` | CLAUDE.md is the rule sheet future work reads first, so a rule naming a renamed or deleted function sends the reader after something that is not there. Caught a real drift the day it was written. |
| `test_tenant_walk` | The tenant walk opened one app at a time: 2-3 hours on the real tenant. It is now concurrent per hop, and this compares it against the old sequential implementation run verbatim over the same 12-app, three-hop estate - including the first-writer-wins case where two staging apps produce the same QVD. |
| `test_fonts` | A file loading is not evidence that the family the stylesheets need exists: family names live inside the font, so a renamed weight registers under the wrong family and every heading falls back silently. Reads both name IDs (1 and 16 - Google Fonts puts the real family in 16) and cross-checks against Qt. Exits 2 (**warn**) when a family is absent, because that is actionable but not a code regression. |
| `test_denied` | An API key that can open apps but not read their scripts made a real run print ~1,900 near-identical "Access denied" lines across 3,816 engine sessions, then conclude "no app stores a QVD". It now stops after 30, groups the failures, names the cause, and says a script could not be read rather than that nothing is landed. |
| `test_shell_cache` | The tenant data-file inventory costs one call per space now, so it is fetched once per session and dropped when the tenant changes. |

## Three outcomes, not two

`ok` and `FAIL` mean what they say. A suite exiting **2** is a `warn`: something
is actionable but no commit can fix it — a font family nobody has vendored, for
instance. Warnings print their output on every run and never fail the gate,
because a gate that stays red for something unfixable is one people learn to
ignore.

## Optional dependencies

`test_analytics` needs `pandas` and `test_capacity`'s workbook check needs
`openpyxl`. Both are in `requirements.txt`; without them those suites are
**skipped**, not failed, and the runner says which package was missing.

## Adding one

Copy the shape of an existing suite. What makes these useful is not the
assertion, it is that the file says what went wrong in plain words. If a new
suite does not explain a real failure, it is probably not worth having.

"""Dataflow field impact: the Power BI counterpart of the landed-QVD report.

A Gen1 dataflow entity is Power BI's answer to a QVD - a table staged by a
separate artifact and then read by semantic models. So "landed" here means a
model table whose source resolution went through at least one dataflow hop,
and the question is the same one: for every field a dataflow lands, what
actually depends on it?

## Where this differs from the Qlik report, and why

**The "used" signal is weaker.** Qlik's Engine API exposes every measure,
dimension and visual expression, so "referenced" there means referenced by
something a user sees. Power BI's Admin APIs expose no visual or report-page
content at all, so the strongest available signal is whether a measure or
calculated column's DAX references the column. A raw column dropped straight
onto a visual with no calculation involved is invisible to any API. Every
label here therefore says "referenced by DAX", not "referenced" - a column
with no DAX reference is a candidate to check, never a verdict.

**The usage signal is real, but short.** Unlike Qlik, Power BI does expose
per-report view events, and Studio collects them. So dataset criticality here
can use actual views and actual distinct users, which the Qlik side cannot.
The ceiling is retention: Microsoft keeps activity events about 28 days, so
the window is only as long as the history that has been collected. There is no
quarterly or yearly figure until a year of collection exists, and this report
says what window its numbers cover rather than implying more.

**"Dropped on the way in" is only sometimes knowable.** A dataflow entity's M
code narrows to explicit columns often enough to be useful, and where it does,
a field the dataflow selected but the model never exposed can be reported.
Where the M code just passes everything through, the fields are unknown and
the row says so - the same honesty the Qlik report applies to a wildcard load.

Pure analysis: no GUI, no network.
"""
from __future__ import annotations

import datetime
import os

import qlik_core as core          # reused only for the shared header styling

# States for one (dataflow field, model table) pair, weakest to strongest
# evidence that something depends on the field.
STATE_UNRESOLVED = "source not resolved"
STATE_NOT_NARROWED = "dataflow columns not narrowed"
STATE_DROPPED = "in the dataflow, not in the model"
STATE_IN_MODEL_UNUSED = "in model, no DAX reference"
STATE_IN_MODEL_USED = "in model, referenced by DAX"
# The dataset returned no DAX at all, so "no DAX reference" cannot be
# distinguished from "no DAX to check against". Ranked above UNUSED because it
# is a gap in the evidence, not a finding, and must never read as a candidate
# to drop.
STATE_DAX_UNKNOWN = "in model, DAX usage not known"

STATE_ORDER = [STATE_UNRESOLVED, STATE_NOT_NARROWED, STATE_DROPPED,
               STATE_IN_MODEL_UNUSED, STATE_DAX_UNKNOWN, STATE_IN_MODEL_USED]
_RANK = {s: i for i, s in enumerate(STATE_ORDER)}

# The states that mean "this field is a column of the model table". Whether the
# DAX check could run is a separate question from whether the column is there,
# and conflating them made a column in a DAX-less dataset read as not reaching
# a model at all.
_IN_THE_MODEL = (STATE_IN_MODEL_USED, STATE_IN_MODEL_UNUSED, STATE_DAX_UNKNOWN)

STATE_HELP = {
    STATE_IN_MODEL_USED: "A column of the model table, and a measure or calculated column's DAX "
                         "references it.",
    STATE_IN_MODEL_UNUSED: "A column of the model table, with no DAX reference anywhere in the "
                           "dataset. NOT proof it is unused: Power BI's Admin APIs expose no "
                           "visual content, so a column placed straight onto a visual cannot be "
                           "detected. A candidate to check.",
    STATE_DROPPED: "The dataflow's M code selects this field, but the model table has no column "
                   "by that name - dropped, renamed, or folded into something else between the "
                   "dataflow and the dataset.",
    STATE_NOT_NARROWED: "The dataflow's M code does not narrow to explicit columns, so which "
                        "fields it carries cannot be read from it. The model's own columns are "
                        "still listed; anything the dataflow carried and the model dropped is "
                        "simply unknown.",
    STATE_DAX_UNKNOWN: "A column of the model table, in a dataset that returned no DAX "
                       "expressions at all - so there was nothing to check it against. Either "
                       "the dataset holds no measures and no calculated columns, or the tenant "
                       "setting 'Enhance admin APIs responses with DAX and mashup expressions' "
                       "is off. A gap in the evidence, not a finding, and never a reason to "
                       "drop a column.",
    STATE_UNRESOLVED: "The table's source could not be resolved far enough to say - see the "
                      "status column and the Model lineage report's warning sheet.",
}

# scan_model_lineage statuses that mean the resolution did not complete.
_BAD_STATUS = {"unresolved", "no_expression_available", "dataflow_export_failed",
               "dataflow_entity_not_found", "dataflow_reference_incomplete",
               "max_hops_exceeded", "dataset_has_no_tables"}


def best_state(states):
    known = [s for s in states if s in _RANK]
    return max(known, key=lambda s: _RANK[s]) if known else STATE_UNRESOLVED


def entity_of(row):
    """The dataflow entity a model table landed from, as "dataflow_id.entity",
    or "" when the table is not dataflow-sourced. The last hop is the one the
    dataset actually reads; earlier hops are dataflow-to-dataflow."""
    hops = row.get("hops") or []
    if not hops:
        return ""
    last = hops[-1]
    df = last.get("dataflow_id") or ""
    ent = last.get("entity") or ""
    return f"{df}.{ent}" if (df or ent) else ""


def _dataflow_fields(row):
    """Fields the dataflow's M code explicitly selected, or None when it did
    not narrow. Several direct sources can contribute; None from any of them
    means the set is incomplete, so the whole thing is unknown."""
    sources = row.get("direct_sources") or []
    if not sources:
        return None
    out = set()
    for s in sources:
        f = s.get("fields")
        if f is None:
            return None
        out |= {str(x) for x in f}
    return out


# --------------------------------------------------------------- criticality
def score_dataset(ds):
    """Criticality of one semantic model.

    Uses real usage when collected activity events cover it, and says so when
    they do not - a dataset with no events is not "unused", it is unmeasured.

    ds needs: reports (int), views (int|None), users (int|None),
    tables (int), window_days (int|None).
    """
    reports = int(ds.get("reports") or 0)
    tables = int(ds.get("tables") or 0)
    views = ds.get("views")
    users = ds.get("users")
    measured = views is not None

    score = 0
    parts = []
    if reports >= 3:
        score += 3
        parts.append(f"{reports} reports built on it (+3)")
    elif reports >= 1:
        score += 2
        parts.append(f"{reports} report(s) built on it (+2)")
    else:
        parts.append("no report built on it (0)")
    if not measured:
        parts.append("no collected activity events - usage not counted")
    else:
        w = ds.get("window_days")
        win = f" in {w} collected day(s)" if w else " in the collected window"
        if views >= 100:
            score += 3
            parts.append(f"{views:,} views{win} (+3)")
        elif views > 0:
            score += 2
            parts.append(f"{views:,} views{win} (+2)")
        else:
            score -= 1
            parts.append(f"no views{win} (-1)")
        if (users or 0) >= 5:
            score += 1
            parts.append(f"{users} distinct users (+1)")
        else:
            parts.append(f"{users or 0} distinct user(s) (0)")
    if tables >= 10:
        score += 1
        parts.append(f"{tables} tables (+1)")
    else:
        parts.append(f"{tables} tables (0)")

    tier = "High" if score >= 5 else ("Medium" if score >= 3 else "Low")
    return {"score": score, "tier": tier, "why": "; ".join(parts), "measured": measured}


# --------------------------------------------------------------- the scan
def scan_dataflow_impact(lineage, reports=(), views=None, window_days=None, log=None):
    """Roll a model-lineage scan up into dataflow field impact.

    `lineage`  scan_model_lineage() output.
    `reports`  the scan's report list (see its `sink` argument), used to count
               how many reports sit on each dataset and to attribute views.
    `views`    optional {report_id: {"views": int, "users": int}} built from
               collected activity events. None means no events were available,
               which is reported as unmeasured rather than as zero.
    """
    log = log or (lambda _m: None)
    landed_rows = [r for r in lineage if entity_of(r)]
    log(f"{len(landed_rows)} of {len(lineage)} model table(s) are sourced via a Gen1 dataflow.")

    reports_by_dataset = {}
    for rp in reports or ():
        if rp.get("dataset_id"):
            reports_by_dataset.setdefault(rp["dataset_id"], []).append(rp)

    reach = []
    datasets = {}
    for r in landed_rows:
        entity = entity_of(r)
        ds_id = r.get("dataset_id", "")
        cols = r.get("column_usage") or []
        col_names = {c.get("column", "") for c in cols if c.get("column")}
        selected = _dataflow_fields(r)
        unresolved = r.get("status") in _BAD_STATUS

        common = {
            "entity": entity,
            "dataflow_id": (r.get("hops") or [{}])[-1].get("dataflow_id", ""),
            "dataset": r.get("dataset_name", ""), "dataset_id": ds_id,
            "workspace": r.get("workspace_name", ""),
            "table": r.get("table_name", ""), "status": r.get("status", ""),
            "hops": len(r.get("hops") or []),
        }

        # Every column the model exposes for this table.
        for c in cols:
            name = c.get("column") or ""
            if not name:
                continue
            dax = c.get("used_in_dax")
            state = (STATE_UNRESOLVED if unresolved and not cols else
                     STATE_DAX_UNKNOWN if dax is None else
                     STATE_IN_MODEL_USED if dax else STATE_IN_MODEL_UNUSED)
            reach.append(dict(common, field=name, model_column=name, state=state))

        # Fields the dataflow selected that never became a model column.
        if selected is None:
            # Not narrowed: say so once per table rather than inventing rows.
            reach.append(dict(common, field="(not narrowed)", model_column="",
                              state=STATE_NOT_NARROWED))
        else:
            lower = {n.lower() for n in col_names}
            for f in sorted(selected):
                if f.lower() not in lower:
                    reach.append(dict(common, field=f, model_column="",
                                      state=STATE_DROPPED))

        d = datasets.setdefault(ds_id, {
            "name": r.get("dataset_name", ""), "workspace": r.get("workspace_name", ""),
            "id": ds_id, "tables": 0, "landed_tables": 0, "entities": set(),
        })
        d["landed_tables"] += 1
        d["entities"].add(entity)

    # Table counts come from the whole lineage, not just the landed tables.
    for r in lineage:
        ds_id = r.get("dataset_id", "")
        if ds_id in datasets and r.get("status") != "dataset_has_no_tables":
            datasets[ds_id]["tables"] += 1

    # --- score each dataset ---
    consumers = []
    for ds_id, d in datasets.items():
        rps = reports_by_dataset.get(ds_id, [])
        d["reports"] = len(rps)
        if views is None:
            d["views"] = d["users"] = None
        else:
            d["views"] = sum((views.get(rp["id"]) or {}).get("views", 0) for rp in rps)
            seen = set()
            for rp in rps:
                seen |= set((views.get(rp["id"]) or {}).get("user_set") or ())
            d["users"] = len(seen)
        d["window_days"] = window_days
        d["entities"] = len(d["entities"])
        d.update(score_dataset(d))
        consumers.append(d)
    consumers.sort(key=lambda d: (-d["score"], d["name"]))

    by_id = {d["id"]: d for d in consumers}
    for row in reach:
        d = by_id.get(row["dataset_id"]) or {}
        row["dataset_tier"] = d.get("tier", "")
        row["dataset_score"] = d.get("score", "")

    # --- roll up per (entity, field) ---
    grouped = {}
    for row in reach:
        grouped.setdefault((row["entity"], row["field"]), []).append(row)
    fields = []
    for (entity, fname), rows in sorted(grouped.items()):
        states = [r["state"] for r in rows]
        best = best_state(states)
        # Only a model that actually exposes the field lends it a criticality.
        # A field the dataflow carried and every model dropped reaches nothing,
        # so inheriting a High tier from the model that dropped it would read
        # as "this matters a lot" when the truth is the opposite.
        tiers = [r["dataset_tier"] for r in rows if r["dataset_tier"]
                 and r["state"] in _IN_THE_MODEL]
        top = ("High" if "High" in tiers else
               ("Medium" if "Medium" in tiers else ("Low" if tiers else "")))
        fields.append({
            "entity": entity, "field": fname,
            "has_impact": best == STATE_IN_MODEL_USED,
            "reaches_a_model": best in _IN_THE_MODEL,
            "datasets": len({r["dataset_id"] for r in rows}),
            "best_state": best, "states": ", ".join(sorted(set(states))),
            "top_tier": top,
            "dataset_names": ", ".join(sorted({r["dataset"] for r in rows})),
        })

    # --- one row per dataflow entity ---
    by_entity = {}
    for row in reach:
        by_entity.setdefault(row["entity"], []).append(row)
    entities = []
    for entity, rows in sorted(by_entity.items()):
        flds = {r["field"] for r in rows if r["field"] != "(not narrowed)"}
        used = {r["field"] for r in rows if r["state"] == STATE_IN_MODEL_USED}
        entities.append({
            "entity": entity,
            "dataflow_id": rows[0]["dataflow_id"],
            "read_by_datasets": len({r["dataset_id"] for r in rows}),
            "read_by_tables": len({(r["dataset_id"], r["table"]) for r in rows}),
            "fields_seen": len(flds),
            "fields_with_impact": len(used),
            "fields_without_impact": len(flds - used),
            "narrowed": not any(r["state"] == STATE_NOT_NARROWED for r in rows),
        })

    return {"entities": entities, "fields": fields, "reach": reach, "consumers": consumers,
            "measured": views is not None, "window_days": window_days,
            "landed_tables": len(landed_rows), "all_tables": len(lineage)}


def views_from_records(records):
    """Turn the usage records the Power BI view already builds into what
    scan_dataflow_impact wants: {report_id: {views, user_set}} plus the number
    of distinct days those records cover, which is the honest width of the
    window - not 28, and not a year."""
    if not records:
        return None, None
    out = {}
    days = set()
    for rec in records:
        rid = rec.get("report_id") or ""
        if not rid:
            continue
        e = out.setdefault(rid, {"views": 0, "user_set": set()})
        e["views"] += int(rec.get("views") or 0)
        if rec.get("user"):
            e["user_set"].add(rec["user"])
        if rec.get("date"):
            days.add(rec["date"])
    return out, len(days)


# --------------------------------------------------------------- text summary
def render_text(result):
    if not result:
        return "Scan cancelled."
    f = result["fields"]
    if not result["entities"]:
        return ("No semantic model table on this tenant resolved to a Gen1 dataflow, so there "
                "are no landed dataflow fields to report. If that is unexpected, check the "
                "Model lineage report first - a tenant where every table comes back as "
                "no_expression_available is missing the 'Enhance admin APIs responses with "
                "detailed metadata' tenant setting, and nothing can be resolved without it.")
    tiers = {}
    for d in result["consumers"]:
        tiers[d["tier"]] = tiers.get(d["tier"], 0) + 1
    impact = sum(1 for r in f if r["has_impact"])
    carried = sum(1 for r in f if r["best_state"] == STATE_IN_MODEL_UNUSED)
    dropped = sum(1 for r in f if r["best_state"] == STATE_DROPPED)
    dax_unknown = sum(1 for r in f if r["best_state"] == STATE_DAX_UNKNOWN)
    lines = [
        "DATAFLOW FIELD IMPACT",
        "",
        f"Model tables via a dataflow  {result['landed_tables']} of {result['all_tables']}",
        f"Dataflow entities            {len(result['entities'])}",
        f"Semantic models reading them {len(result['consumers'])}"
        f"   (High {tiers.get('High', 0)} / Medium {tiers.get('Medium', 0)} /"
        f" Low {tiers.get('Low', 0)})",
        f"Distinct dataflow fields     {len(f)}",
        "",
        "Of those fields, at their strongest state anywhere:",
        f"  {impact:6}  in a model and referenced by DAX",
        f"  {carried:6}  in a model with no DAX reference (check before dropping)",
        f"  {dax_unknown:6}  in a model whose dataset returned no DAX at all "
        f"(nothing to check against - not a finding)",
        f"  {dropped:6}  selected by the dataflow but absent from every model",
        "",
    ]
    unnarrowed = [e for e in result["entities"] if not e["narrowed"]]
    if unnarrowed:
        lines.append(f"{len(unnarrowed)} entity(ies) whose M code does not narrow to explicit "
                     "columns, so what they carry cannot be read from the script.")
        lines.append("")
    if result["measured"]:
        lines.append(f"Views come from {result['window_days']} collected day(s) of activity "
                     "events. That is the whole window - Microsoft keeps about 28 days, so "
                     "there is no quarterly or yearly figure until that much history has "
                     "accumulated.")
    else:
        lines.append("No collected activity events, so criticality is structural only: reports "
                     "built on the dataset and its size. Collect activity events and re-run to "
                     "bring real views and users into the tier.")
    lines += [
        "",
        '"Referenced by DAX" is the strongest signal Power BI offers: the Admin APIs expose no '
        "visual or report-page content, so a column dropped straight onto a visual with no "
        "calculation involved cannot be detected by any API. A field with no DAX reference is "
        "a candidate to check, not a verdict.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------- workbook
_SHEETS = (
    ("Field impact", ["Dataflow entity", "Field", "Has impact", "Reaches a model",
                      "Models reading it", "Best state", "All states",
                      "Highest model criticality", "Which models"],
     lambda r: [r["entity"], r["field"], r["has_impact"], r["reaches_a_model"],
                r["datasets"], r["best_state"], r["states"], r["top_tier"],
                r["dataset_names"]], "fields"),
    ("Field reach", ["Dataflow entity", "Field", "Model column", "Semantic model",
                     "Workspace", "Model table", "State", "Dataflow hops",
                     "Resolution status", "Model criticality", "Model score"],
     lambda r: [r["entity"], r["field"], r["model_column"], r["dataset"], r["workspace"],
                r["table"], r["state"], r["hops"], r["status"], r["dataset_tier"],
                r["dataset_score"]], "reach"),
    ("Dataflow entities", ["Dataflow entity", "Dataflow id", "Read by models",
                           "Read by model tables", "Fields seen", "Fields with impact",
                           "Fields without impact", "M code narrows columns"],
     lambda r: [r["entity"], r["dataflow_id"], r["read_by_datasets"], r["read_by_tables"],
                r["fields_seen"], r["fields_with_impact"], r["fields_without_impact"],
                r["narrowed"]], "entities"),
    ("Criticality", ["Semantic model", "Workspace", "Criticality", "Score", "Reports on it",
                     "Views in window", "Distinct users", "Tables", "Tables via a dataflow",
                     "Dataflow entities read", "Usage measured", "How the score was built"],
     lambda r: [r["name"], r["workspace"], r["tier"], r["score"], r.get("reports", 0),
                r.get("views"), r.get("users"), r.get("tables", 0),
                r.get("landed_tables", 0), r.get("entities", 0), r.get("measured", False),
                r["why"]], "consumers"),
)


def write_report(result, out_dir, log=print):
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"dataflow_field_impact_{stamp}"
    summary = _summary_rows(result)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        import csv
        path = os.path.join(out_dir, base + "_summary.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(summary)
        for name, headers, row_of, key in _SHEETS:
            p = os.path.join(out_dir, f"{base}_{name.lower().replace(' ', '_')}.csv")
            with open(p, "w", newline="", encoding="utf-8-sig") as fh:
                wr = csv.writer(fh)
                wr.writerow(headers)
                wr.writerows(row_of(r) for r in result[key])
        log("openpyxl not installed - wrote one CSV per sheet instead.")
        return path

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for row in summary:
        ws.append(row)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 96
    for cell in ws["B"]:
        cell.alignment = cell.alignment.copy(wrapText=True, vertical="top")

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1D2D3D")
    for name, headers, row_of, key in _SHEETS:
        sh = wb.create_sheet(name)
        sh.append(headers)
        core._style_header_row(sh, 1, len(headers), head_font, head_fill)
        for r in result[key]:
            sh.append(row_of(r))
        sh.freeze_panes = "A2"
        sh.auto_filter.ref = sh.dimensions
        for i, h in enumerate(headers, start=1):
            sh.column_dimensions[sh.cell(row=1, column=i).column_letter].width = \
                min(60, max(12, len(h) + 4))

    path = os.path.join(out_dir, base + ".xlsx")
    wb.save(path)
    log(f"Dataflow field impact report -> {os.path.basename(path)}")
    return path


def _summary_rows(result):
    f = result["fields"]
    tiers = {}
    for d in result["consumers"]:
        tiers[d["tier"]] = tiers.get(d["tier"], 0) + 1
    rows = [
        ["Dataflow field impact", ""],
        ["Built", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["", ""],
        ["Model tables sourced via a dataflow", f"{result['landed_tables']} of "
                                               f"{result['all_tables']}"],
        ["Dataflow entities", len(result["entities"])],
        ["Entities whose M code does not narrow columns",
         sum(1 for e in result["entities"] if not e["narrowed"])],
        ["Semantic models reading them", len(result["consumers"])],
        ["  High criticality", tiers.get("High", 0)],
        ["  Medium criticality", tiers.get("Medium", 0)],
        ["  Low criticality", tiers.get("Low", 0)],
        ["Distinct dataflow fields", len(f)],
        ["  in a model, referenced by DAX", sum(1 for r in f if r["has_impact"])],
        ["  in a model, no DAX reference",
         sum(1 for r in f if r["best_state"] == STATE_IN_MODEL_UNUSED)],
        ["  selected by the dataflow, in no model",
         sum(1 for r in f if r["best_state"] == STATE_DROPPED)],
        ["", ""],
        ["WHAT \"HAS IMPACT\" MEANS", ""],
        ["Has impact = TRUE",
         "At least one semantic model exposes this field as a column AND a measure or "
         "calculated column's DAX references it."],
        ["Has impact = FALSE",
         "No model both exposes it and references it in DAX. This is a weaker statement than "
         "the Qlik equivalent - see the limits below - and is not permission to drop the "
         "field."],
        ["", ""],
        ["THE STATES", ""],
    ]
    for state in reversed(STATE_ORDER):
        rows.append([state, STATE_HELP[state]])
    rows += [
        ["", ""],
        ["THE BIG LIMIT: NO VISUAL CONTENT", ""],
        ["What is missing",
         "Power BI's Admin APIs expose no visual or report-page content. A column placed "
         "straight onto a table, chart or slicer with no calculation involved is referenced "
         "by no DAX and is therefore invisible to this scan and to every other API-based "
         "tool. Qlik's Engine API does expose visual expressions, which is why the Qlik "
         "landed-QVD report can say \"referenced\" and this one only says \"referenced by "
         "DAX\"."],
        ["What follows", "Treat \"no DAX reference\" as a shortlist to check in the report "
                         "itself, never as a finding that the column is unused."],
        ["", ""],
        ["USAGE AND ITS WINDOW", ""],
    ]
    if result["measured"]:
        rows += [
            ["Views", f"From {result['window_days']} distinct collected day(s) of activity "
                      "events. Microsoft keeps activity events about 28 days, so this window "
                      "is only as wide as the history Studio has collected. There is no "
                      "quarterly or yearly active-user figure until that much history exists."],
            ["Attribution", "Views are counted per report and rolled up to the semantic model "
                            "each report is built on, from the Scanner API's report-to-dataset "
                            "link. A dataset with no reports therefore shows no views even if "
                            "it is queried directly."],
        ]
    else:
        rows += [
            ["No events collected",
             "Criticality on this run is structural only - reports built on the dataset and "
             "its size. A dataset with no events is unmeasured, not unused. Collect activity "
             "events in the Power BI workspace and re-run to bring real views and users in."],
        ]
    rows += [
        ["", ""],
        ["HOW THE TIER IS BUILT", ""],
        ["Components", "3+ reports built on it (+3) or 1-2 (+2); 100+ views in the collected "
                       "window (+3), some views (+2), no views (-1); 5+ distinct users (+1); "
                       "10+ tables (+1). High from 5, Medium from 3, Low below that. With no "
                       "collected events the view and user components are skipped entirely "
                       "rather than scored as zero."],
        ["How to check it", "The Criticality sheet shows every component per model, including "
                            "whether usage was measured at all."],
        ["", ""],
        ["OTHER LIMITS", ""],
        ["Dataflow columns", "A field the dataflow carried and every model dropped can only be "
                             "reported where the dataflow's M code narrows to explicit columns. "
                             "Where it does not, the entity is flagged and what it carries is "
                             "unknown."],
        ["Gen2 dataflows", "This follows Gen1 dataflow references, which is what "
                           "resolve_table_source chases. A Fabric Gen2 dataflow or a lakehouse "
                           "shortcut is not a dataflow hop and its tables will not appear here."],
        ["Tenant setting", "Everything depends on 'Enhance admin APIs responses with detailed "
                           "metadata' being enabled for the service principal's security "
                           "group. Without it every table comes back as "
                           "no_expression_available and nothing resolves."],
    ]
    return rows

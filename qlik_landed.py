"""Landed-QVD field impact: which extractor-produced fields anything depends on.

A "landed" QVD is one written by an app whose name contains "extractor" - the
apps that pull data onto the platform from outside it. This module answers, for
every field in every landed QVD:

  * Does anything downstream depend on it at all?
  * If it is loaded somewhere, is it actually used, or just carried?
  * Which apps would feel it if the field changed or went away, and how much
    would that matter?

## The three states, per consuming app

The question "is this field used" has three honest answers, and the interesting
one is the middle:

  `in model, referenced`   reaches the app's data model AND a measure,
                           dimension or visual expression references it.
  `in model, unused`       reaches the model, nothing references it. Loaded and
                           carried, paying storage and reload time for nothing.
  `script only`            the load script reads it but it does not reach the
                           final model - dropped, joined away, aggregated into
                           something else, or renamed past recognition.

Two more states exist because the script cannot always be resolved:
`in model, other table` (present under a different table than this scan
expected) and `wildcard` (a wildcard folder load, whose fields no script parse
can enumerate). Neither is a verdict; both are listed so they can be checked.

## Criticality is STRUCTURAL, not usage

Qlik Cloud exposes no per-app "opened by user" telemetry through any API - not
the Audits API, not the monitoring apps. This was verified against the live
tenant over 500 real audit events and against Qlik's own Consumption Monitor:
only reload/ETL activity and capacity billing are available. So there is no
DAU/MAU/QAU/YAU to be had, and a report claiming them would be inventing them.

What IS computable is how much the estate structurally depends on an app:
whether it is published, whether other apps read what it produces, how much is
built on it, and whether it is still being reloaded. `score_app` combines those
into a High/Medium/Low tier and reports every component alongside it, so the
tier is auditable rather than a black box.

This module is pure analysis: no GUI, no network. Callers hand it scripts and
model data.
"""
from __future__ import annotations

import datetime
import os

import fmt
import qlik_core as core

EXTRACTOR_TOKEN = "extractor"        # what marks an app as landing outside data

# The per-app classification of one field reference, worst-to-best. Ordered so
# a roll-up across several apps can take the best state any app achieved.
STATE_WILDCARD = "wildcard"
STATE_SCRIPT_ONLY = "script only"
STATE_OTHER_TABLE = "in model, other table"
STATE_IN_MODEL_UNUSED = "in model, unused"
STATE_UNKNOWN = "in model, usage unknown"
STATE_IN_MODEL_USED = "in model, referenced"

# Ranked weakest to strongest evidence that something depends on the field.
# "usage unknown" outranks "unused" deliberately: unused is a finding, unknown
# is a gap, and a roll-up across apps must not turn a gap into a delete hint.
STATE_ORDER = [STATE_WILDCARD, STATE_SCRIPT_ONLY, STATE_OTHER_TABLE,
               STATE_IN_MODEL_UNUSED, STATE_UNKNOWN, STATE_IN_MODEL_USED]
_STATE_RANK = {s: i for i, s in enumerate(STATE_ORDER)}

STATE_HELP = {
    STATE_IN_MODEL_USED: "Reaches the data model and a measure, dimension or visual references it.",
    STATE_IN_MODEL_UNUSED: "Reaches the data model but nothing references it - loaded and carried "
                           "for nothing.",
    STATE_SCRIPT_ONLY: "The load script reads it but it does not reach the final model: dropped, "
                       "joined away, aggregated into something else, or renamed past recognition.",
    STATE_OTHER_TABLE: "Present in the model under a different table than this scan expected. "
                       "Check it rather than trusting either answer.",
    STATE_WILDCARD: "A wildcard folder load - no script parse can enumerate its fields. Not a "
                    "verdict; the QVD's fields are simply unknown from the script.",
    STATE_UNKNOWN: "In the data model, but this app's usage scan did not complete, so whether "
                   "anything references it is unknown. A gap in the scan, not a finding.",
}


def is_extractor(name):
    return EXTRACTOR_TOKEN in (name or "").lower()


def classify(row):
    """One analyze_qvd_field_usage row (after attach_report_usage) -> a state."""
    status = row.get("status")
    if status == "wildcard_unresolved":
        return STATE_WILDCARD
    if status == "not_found_in_final_model":
        return STATE_SCRIPT_ONLY
    if status == "found_in_other_table":
        return STATE_OTHER_TABLE
    # confirmed / confirmed_case_mismatch: in the model. Referenced or not?
    # used_in_report is None when analyze_usage did not see the field at all,
    # which should not happen for a confirmed field; treat it as unknown rather
    # than as "unused", so a gap in the scan is never read as a delete hint.
    used = row.get("used_in_report")
    if used is None:
        return STATE_UNKNOWN
    return STATE_IN_MODEL_USED if used else STATE_IN_MODEL_UNUSED


def best_state(states):
    """The strongest state any consuming app reached for a field."""
    known = [s for s in states if s in _STATE_RANK]
    return max(known, key=lambda s: _STATE_RANK[s]) if known else STATE_WILDCARD


_days_since = fmt.days_since          # one implementation, see fmt.py


# --------------------------------------------------------------- criticality
def score_app(app):
    """Structural criticality for one consuming app.

    NOT usage - see the module docstring. Every component is returned next to
    the score so the tier can be checked rather than trusted.

    app needs: published (bool|None), downstream_apps (int), objects (int),
    reload (ISO str), landed_fields_used (int).
    """
    published = bool(app.get("published"))
    downstream = int(app.get("downstream_apps") or 0)
    objects = int(app.get("objects") or 0)
    fields_used = int(app.get("landed_fields_used") or 0)
    age = _days_since(app.get("reload"))

    parts = []
    score = 0
    if published:
        score += 2
        parts.append("published (+2)")
    else:
        parts.append("not published (0)")
    if downstream >= 3:
        score += 3
        parts.append(f"feeds {downstream} apps (+3)")
    elif downstream >= 1:
        score += 2
        parts.append(f"feeds {downstream} app(s) (+2)")
    else:
        parts.append("feeds no other app (0)")
    if objects >= 40:
        score += 2
        parts.append(f"{objects} visual objects (+2)")
    elif objects >= 15:
        score += 1
        parts.append(f"{objects} visual objects (+1)")
    else:
        parts.append(f"{objects} visual objects (0)")
    if fields_used >= 50:
        score += 1
        parts.append(f"references {fields_used} landed fields (+1)")
    else:
        parts.append(f"references {fields_used} landed fields (0)")
    if age is None:
        parts.append("no reload recorded (-1)")
        score -= 1
    elif age <= 7:
        score += 1
        parts.append(f"reloaded {age} d ago (+1)")
    elif age > 90:
        score -= 1
        parts.append(f"last reload {age} d ago (-1)")
    else:
        parts.append(f"reloaded {age} d ago (0)")

    tier = "High" if score >= 5 else ("Medium" if score >= 3 else "Low")
    return {"score": score, "tier": tier, "why": "; ".join(parts), "reload_age_days": age}


# --------------------------------------------------------------- the scan
def scan_landed_impact(apps, scripts, read_detail, log=None, cancel_check=None):
    """Build the whole picture.

    `apps` is core.list_apps() output, each enriched with space_name and
    (best-effort) published/reloaded.

    `scripts` is {guid: script} for every app, as core.fetch_scripts returns
    it - already fetched, and fetched concurrently, because every app's script
    is needed and each one is an independent network round trip. A guid
    mapping to None means the app could not be opened; a guid mapping to ""
    is an app whose script is empty, which is a different thing.

    `read_detail(guid) -> {model_fields, objects, usage_result} | None` stays
    a callback because it is the expensive half, and is only needed for an app
    that actually reads a landed QVD. On a tenant with two thousand apps, most
    of which read none, fetching the model, every master item, every visual
    and a full usage analysis for all of them would cost hours for nothing.

    Returns {"qvds", "fields", "reach", "consumers", "skipped", "extractors"},
    or None if cancelled.
    """
    log = log or (lambda _m: None)
    cancel = cancel_check or (lambda: False)

    extractors = [a for a in apps if is_extractor(a.get("name"))]
    others = [a for a in apps if not is_extractor(a.get("name"))]
    log(f"{len(extractors)} extractor app(s), {len(others)} other app(s).")
    if not extractors:
        return {"qvds": {}, "fields": [], "reach": [], "consumers": [],
                "skipped": [], "extractors": []}

    # --- phase 1: what the extractors land ---
    landed = {}                      # qvd basename -> {"producers": [names]}
    extractor_reads = set()
    skipped = []
    for a in extractors:
        if cancel():
            return None
        script = scripts.get(a["guid"])
        if script is None:
            skipped.append({"app": a["name"], "guid": a["guid"], "why": "could not be opened"})
            continue
        stores, reads = core.parse_store_reads(script)
        # An extractor reading another extractor's QVD still counts as
        # something reading it, so keep these out of the fan-out maps but in
        # the "is anything reading this at all" set.
        extractor_reads |= reads
        for q in stores:
            landed.setdefault(q, {"producers": []})["producers"].append(a["name"])
    log(f"{len(landed)} landed QVD(s) written by the extractor apps.")
    if not landed:
        return {"qvds": landed, "fields": [], "reach": [], "consumers": [],
                "skipped": skipped, "extractors": [a["name"] for a in extractors]}

    # --- phase 2: who reads them, and what happens to each field there ---
    reach = []                       # one row per (qvd, field, consuming app)
    consumers = []
    produces = {}                    # app guid -> qvds it stores (for fan-out)
    reads_by_guid = {}
    for a in others:
        if cancel():
            return None
        script = scripts.get(a["guid"])
        if script is None:
            skipped.append({"app": a["name"], "guid": a["guid"], "why": "could not be opened"})
            continue
        stores, reads = core.parse_store_reads(script)
        produces[a["guid"]] = stores
        reads_by_guid[a["guid"]] = reads
        touched = reads & set(landed)
        if not touched:
            continue

        # Only now is the expensive half worth fetching.
        log(f"  consumer: {a['name']} ({len(touched)} landed QVD(s))")
        data = read_detail(a["guid"]) or {}
        tables = core.parse_load_tables(script)
        rows = core.analyze_qvd_field_usage(tables, data.get("model_fields") or [])
        usage = data.get("usage_result")
        if usage:
            core.attach_report_usage(rows, usage)
        else:
            skipped.append({"app": a["name"], "guid": a["guid"],
                            "why": "reads a landed QVD, but its model/usage scan failed - its "
                                   "fields are reported as usage unknown"})
            for r in rows:
                r["used_in_report"] = None

        used_here, reaching_here = set(), set()
        for r in rows:
            qvd = (r.get("qvd_file") or "").lower()
            if qvd not in landed:
                continue                   # reads a QVD, but not a landed one
            state = classify(r)
            # By field name, not by row: a field read from two landed QVDs
            # produces two rows and is still one field.
            fname = (r.get("final_field") or r.get("source_field") or "").lower()
            if state == STATE_IN_MODEL_USED:
                used_here.add(fname)
            if state in (STATE_IN_MODEL_USED, STATE_IN_MODEL_UNUSED, STATE_OTHER_TABLE,
                         STATE_UNKNOWN):
                reaching_here.add(fname)
            src = r.get("source_field") or ""
            fin = r.get("final_field") or ""
            reach.append({
                "qvd": qvd,
                "field": src,
                "final_field": fin,
                "app": a["name"], "app_guid": a["guid"],
                "space": a.get("space_name", ""),
                "table": r.get("target_table", ""),
                "state": state,
                # Two different things the user needs apart: a straight
                # `X as Y` rename, and a field the script computes rather
                # than carries. parse_load_tables' `passthrough` is the
                # latter, so a rename is passthrough too.
                "renamed": bool(src and fin and src != fin),
                "computed": not r.get("passthrough", False),
            })
        consumers.append({
            "name": a["name"], "guid": a["guid"], "space": a.get("space_name", ""),
            "published": a.get("published"), "reload": a.get("reloaded", ""),
            "objects": len(data.get("objects") or []),
            "landed_qvds": len(touched), "landed_fields_used": len(used_here),
            "landed_fields_reaching": len(reaching_here),
        })

    # --- phase 3: fan-out, then score each consumer ---
    all_reads = set(extractor_reads)
    for rs in reads_by_guid.values():
        all_reads |= rs
    for c in consumers:
        mine = produces.get(c["guid"]) or set()
        # An extractor reading this app's output is a downstream dependency
        # too, so its reads are one extra bucket rather than being ignored.
        c["downstream_apps"] = sum(
            1 for g, rs in reads_by_guid.items() if g != c["guid"] and (rs & mine))
        if mine & extractor_reads:
            c["downstream_apps"] += 1
        c.update(score_app(c))

    by_guid = {c["guid"]: c for c in consumers}
    for r in reach:
        c = by_guid.get(r["app_guid"]) or {}
        r["app_tier"] = c.get("tier", "")
        r["app_score"] = c.get("score", "")

    # --- phase 4: roll up per (qvd, field) ---
    grouped = {}
    for r in reach:
        grouped.setdefault((r["qvd"], r["field"]), []).append(r)
    fields = []
    for (qvd, field), rows in sorted(grouped.items()):
        states = [r["state"] for r in rows]
        best = best_state(states)
        # Only an app that actually gets the field into its model lends it a
        # criticality. A field that is script-only everywhere reaches nothing,
        # so inheriting a High tier from the app that drops it would read as
        # "this matters a lot" when the truth is the opposite.
        tiers = [r["app_tier"] for r in rows if r["app_tier"]
                 and r["state"] in (STATE_IN_MODEL_USED, STATE_IN_MODEL_UNUSED,
                                    STATE_OTHER_TABLE, STATE_UNKNOWN)]
        top = ("High" if "High" in tiers else
               ("Medium" if "Medium" in tiers else ("Low" if tiers else "")))
        fields.append({
            "qvd": qvd,
            "producers": ", ".join(sorted(set(landed[qvd]["producers"]))) if qvd in landed else "",
            "field": field,
            "has_impact": best == STATE_IN_MODEL_USED,
            "reaches_a_model": best in (STATE_IN_MODEL_USED, STATE_IN_MODEL_UNUSED,
                                        STATE_OTHER_TABLE, STATE_UNKNOWN),
            "apps": len({r["app_guid"] for r in rows}),
            "best_state": best,
            "states": ", ".join(sorted(set(states))),
            "renamed_anywhere": any(r["renamed"] for r in rows),
            "computed_anywhere": any(r["computed"] for r in rows),
            "top_tier": top,
            "app_names": ", ".join(sorted({r["app"] for r in rows})),
        })

    # A landed QVD nothing reads has no reach rows at all, so add its QVD row
    # explicitly: "nothing reads this" is the most actionable finding here.
    qvd_rows = []
    reach_by_qvd = {}
    for r in reach:
        reach_by_qvd.setdefault(r["qvd"], []).append(r)
    for qvd in sorted(landed):
        rows = reach_by_qvd.get(qvd, [])
        flds = {r["field"] for r in rows}
        used = {r["field"] for r in rows if r["state"] == STATE_IN_MODEL_USED}
        qvd_rows.append({
            "qvd": qvd,
            "producers": ", ".join(sorted(set(landed[qvd]["producers"]))),
            "read_by_apps": len({r["app_guid"] for r in rows}),
            "fields_seen": len(flds),
            "fields_with_impact": len(used),
            "fields_no_impact": len(flds - used),
            "read_by_anything": qvd in all_reads,
        })

    return {"qvds": qvd_rows, "fields": fields, "reach": reach, "consumers": consumers,
            "skipped": skipped, "extractors": [a["name"] for a in extractors]}


# --------------------------------------------------------------- text summary
def render_text(result):
    if not result:
        return "Scan cancelled."
    if not result["extractors"]:
        return ("No app on the tenant has \"extractor\" in its name, so there are no landed "
                "QVDs to report on. Rename the extracting apps, or change EXTRACTOR_TOKEN in "
                "qlik_landed.py if your convention differs.")
    f = result["fields"]
    impact = sum(1 for r in f if r["has_impact"])
    carried = sum(1 for r in f if r["best_state"] == STATE_IN_MODEL_UNUSED)
    script_only = sum(1 for r in f if r["best_state"] == STATE_SCRIPT_ONLY)
    dead_qvds = [q for q in result["qvds"] if not q["read_by_apps"]]
    tiers = {}
    for c in result["consumers"]:
        tiers[c["tier"]] = tiers.get(c["tier"], 0) + 1
    unknown = sum(1 for r in f if r["best_state"] == STATE_UNKNOWN)
    lines = [
        "LANDED QVD FIELD IMPACT",
        "",
        f"Extractor apps          {len(result['extractors'])}",
        f"Landed QVDs             {len(result['qvds'])}",
        f"Consuming apps          {len(result['consumers'])}"
        f"   (High {tiers.get('High', 0)} / Medium {tiers.get('Medium', 0)} /"
        f" Low {tiers.get('Low', 0)})",
        f"Distinct landed fields  {len(f)}",
        "",
        "Of those fields, at their strongest state anywhere:",
        f"  {impact:6}  have impact - in a model and referenced",
        f"  {carried:6}  in a model, referenced by nothing (carried for nothing)",
        f"  {script_only:6}  read by a script but never reach any model",
    ]
    if unknown:
        lines.append(f"  {unknown:6}  in a model, usage unknown (a scan gap, not a finding)")
    lines.append("")
    if dead_qvds:
        lines.append(f"{len(dead_qvds)} landed QVD(s) that no scanned app reads at all:")
        for q in dead_qvds[:15]:
            lines.append(f"  {q['qvd']}   (from {q['producers']})")
        if len(dead_qvds) > 15:
            lines.append(f"  ... and {len(dead_qvds) - 15} more")
        lines.append("")
    if result["skipped"]:
        lines.append(f"{len(result['skipped'])} app(s) could not be opened and are NOT covered:")
        for s in result["skipped"][:10]:
            lines.append(f"  {s['app']}")
        lines.append("")
    lines.append("Criticality is STRUCTURAL, not usage: Qlik Cloud exposes no per-app "
                 "opened-by-user telemetry through any API, so there is no DAU/MAU to report. "
                 "See the Criticality sheet for what each tier is built from.")
    return "\n".join(lines)


# --------------------------------------------------------------- workbook
_SHEETS = (
    ("Field impact", ["QVD", "Produced by", "Field", "Has impact", "Reaches a model",
                      "Apps reading it", "Best state", "All states",
                      "Renamed somewhere", "Computed somewhere",
                      "Highest app criticality", "Which apps"],
     lambda r: [r["qvd"], r["producers"], r["field"], r["has_impact"], r["reaches_a_model"],
                r["apps"], r["best_state"], r["states"], r["renamed_anywhere"],
                r["computed_anywhere"], r["top_tier"], r["app_names"]], "fields"),
    ("Field reach", ["QVD", "Field in the QVD", "Field in the model", "App", "Space",
                     "Table", "State", "Renamed", "Computed",
                     "App criticality", "App score"],
     lambda r: [r["qvd"], r["field"], r["final_field"], r["app"], r["space"], r["table"],
                r["state"], r["renamed"], r["computed"], r["app_tier"], r["app_score"]],
     "reach"),
    ("Landed QVDs", ["QVD", "Produced by", "Read by apps",
                     "Fields seen in a consumer's script", "Fields with impact",
                     "Fields without impact", "Read by anything at all"],
     lambda r: [r["qvd"], r["producers"], r["read_by_apps"], r["fields_seen"],
                r["fields_with_impact"], r["fields_no_impact"], r["read_by_anything"]], "qvds"),
    ("Criticality", ["App", "Space", "Criticality", "Score", "Published", "Feeds N apps",
                     "Visual objects", "Landed fields referenced",
                     "Landed fields reaching the model", "Landed QVDs read",
                     "Reload age (days)", "How the score was built"],
     lambda r: [r["name"], r["space"], r["tier"], r["score"], bool(r.get("published")),
                r.get("downstream_apps", 0), r.get("objects", 0),
                r.get("landed_fields_used", 0), r.get("landed_fields_reaching", 0),
                r.get("landed_qvds", 0), r.get("reload_age_days"), r["why"]], "consumers"),
)


def write_report(result, out_dir, log=print):
    """Write the workbook. Falls back to one CSV per sheet without openpyxl,
    the same way every other report in this codebase does."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"landed_qvd_impact_{stamp}"
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
    log(f"Landed QVD impact report -> {os.path.basename(path)}")
    return path


def _summary_rows(result):
    f = result["fields"]
    tiers = {}
    for c in result["consumers"]:
        tiers[c["tier"]] = tiers.get(c["tier"], 0) + 1
    rows = [
        ["Landed QVD field impact", ""],
        ["Built", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["", ""],
        ["Extractor apps", len(result["extractors"])],
        ["Landed QVDs", len(result["qvds"])],
        ["Landed QVDs nothing reads", sum(1 for q in result["qvds"] if not q["read_by_apps"])],
        ["Consuming apps", len(result["consumers"])],
        ["  High criticality", tiers.get("High", 0)],
        ["  Medium criticality", tiers.get("Medium", 0)],
        ["  Low criticality", tiers.get("Low", 0)],
        ["Distinct landed fields", len(f)],
        ["  with impact somewhere", sum(1 for r in f if r["has_impact"])],
        ["  in a model, referenced by nothing",
         sum(1 for r in f if r["best_state"] == STATE_IN_MODEL_UNUSED)],
        ["  script only, never in a model",
         sum(1 for r in f if r["best_state"] == STATE_SCRIPT_ONLY)],
        ["  in a model, usage unknown",
         sum(1 for r in f if r["best_state"] == STATE_UNKNOWN)],
        ["Apps that could not be opened", len(result["skipped"])],
        ["", ""],
        ["WHAT \"HAS IMPACT\" MEANS", ""],
        ["Has impact = TRUE",
         "At least one app has this field in its data model AND a measure, dimension or "
         "visual expression references it. Changing or dropping the field changes what "
         "somebody sees."],
        ["Has impact = FALSE",
         "No app both loads it into a model and references it. That is NOT automatically "
         "permission to drop it: see the state below, and check the wildcard and "
         "other-table rows by hand."],
        ["", ""],
        ["THE STATES", ""],
    ]
    for state in reversed(STATE_ORDER):
        rows.append([state, STATE_HELP[state]])
    rows += [
        ["", ""],
        ["CRITICALITY IS STRUCTURAL, NOT USAGE", ""],
        ["Why", "Qlik Cloud exposes no per-app opened-by-user telemetry through any API - "
                "not the Audits API, not the monitoring apps. Verified against this tenant "
                "over 500 real audit events and against Qlik's own Consumption Monitor: only "
                "reload/ETL activity and capacity billing are available. There is therefore "
                "no daily, monthly, quarterly or yearly active-user figure to report, and a "
                "number here claiming to be one would be invented."],
        ["What the tier is instead",
         "How much the estate structurally depends on the app: published (+2), feeds other "
         "apps with its own QVDs (+2, or +3 for three or more), 15+ visual objects (+1) or "
         "40+ (+2), references 50+ landed fields (+1), reloaded within 7 days (+1), no "
         "reload recorded or none in 90 days (-1). High from 5, Medium from 3, Low below "
         "that."],
        ["Referenced vs reaching",
         "\"Landed fields referenced\" counts only fields a measure, dimension or visual "
         "actually uses. \"Reaching the model\" also counts fields that are in the model "
         "but unused, and fields that resolved to a different table than the script "
         "suggested. An app can show 0 referenced and a positive reaching count when its "
         "load script leaves tables unlabelled - that is a script-parsing limit, not "
         "evidence the app uses nothing."],
        ["How to check it", "The Criticality sheet shows every component per app, so a tier "
                            "can be argued with rather than taken on trust."],
        ["", ""],
        ["LIMITS", ""],
        ["Script parsing", "Fields are read from the load script. A wildcard folder load "
                           "cannot be enumerated, and a field built by an expression is "
                           "matched on its output name. Rows marked wildcard or "
                           "other-table are for manual review, not conclusions."],
        ["Apps not covered", "Any app this API key cannot open is listed on no sheet and its "
                             "dependencies are invisible here. Personal-space apps owned by "
                             "other users are the usual cause - run Diagnose visibility on "
                             "one to confirm."],
        ["Extractor convention", 'An app counts as an extractor when its name contains '
                                 '"extractor", case-insensitive.'],
    ]
    return rows

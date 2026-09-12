"""Local-day bucketing and collection-gap detection."""
import pathlib
import datetime, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import analytics

tmp = Path(tempfile.mkdtemp())
ev = tmp / "activity_events"
ev.mkdir()

def write(day, events):
    (ev / f"activity_events_{day}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")

def view(ts, user="u1", report="Sales"):
    return {"Operation": "ViewReport", "CreationTime": ts, "UserId": user,
            "ReportName": report, "ReportId": "r1", "WorkspaceName": "WS"}

# 23:30 UTC on the 9th is 00:30 local on the 10th in Stockholm (UTC+1)
write("2026-03-09", [view("2026-03-09T23:30:00Z")])
write("2026-03-10", [view("2026-03-10T08:00:00Z")])
# a deliberate gap: no file for the 11th or 12th
write("2026-03-13", [view("2026-03-13T09:00:00Z")])
# a collected but empty day
(ev / "activity_events_2026-03-14.jsonl").write_text("", encoding="utf-8")
# an event that cannot be attributed
write("2026-03-15", [view("2026-03-15T09:00:00Z"),
                     {"Operation": "ViewReport", "CreationTime": None, "UserId": None}])

frames = analytics.compute(tmp)
print("reporting tz     :", frames["reporting_tz"])
days = sorted(str(d) for d in frames["report_usage_daily"]["date"].unique())
print("days in the table:", days)
assert "2026-03-10" in days and "2026-03-09" not in days, days
print("  ok   the 23:30 UTC view is reported on 2026-03-10, the local day it happened")

cov = frames["coverage"]
print("missing days     :", [str(d) for d in cov["days_missing"]])
print("empty days       :", [str(d) for d in cov["days_empty"]])
assert [str(d) for d in cov["days_missing"]] == ["2026-03-11", "2026-03-12"], cov
assert [str(d) for d in cov["days_empty"]] == ["2026-03-14"], cov
print("  ok   two never-collected days and one empty day identified")

print("unattributable   :", frames["unattributable_views"])
assert frames["unattributable_views"] == 1, frames["unattributable_views"]
print("  ok   the event with no user/timestamp is counted, not silently dropped")

print()
print("warning line:")
print(" ", frames["coverage_warning"][:150])
assert "never collected" in frames["coverage_warning"]
assert "not evidence of zero use" in frames["coverage_warning"]

# UTC fallback must not fail the run
analytics.REPORTING_TZ = "Not/AZone"
f2 = analytics.compute(tmp)
assert f2["reporting_tz"] == "UTC", f2["reporting_tz"]
print()
print("  ok   an unknown BIGOV_TZ falls back to UTC and labels itself, no crash")

"""Two model fields differing only in case must not cancel each other out."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

# attach_report_usage folds names to lowercase to match them, so "Region" and
# "region" land on the same key. Whichever came last used to decide the answer.
usage = {"fields": {"all": [
    {"name": "Region", "used": True},      # referenced somewhere
    {"name": "region", "used": False},     # not referenced, and listed LAST
    {"name": "Sales", "used": False},
]}}
rows = [{"final_field": "Region"}, {"final_field": "region"}, {"final_field": "Sales"},
        {"final_field": "Missing"}]
core.attach_report_usage(rows, usage)
got = {r["final_field"]: r["used_in_report"] for r in rows}
print("  used_in_report:", got)

assert got["Region"] is True, got
assert got["region"] is True, got
print("  ok   either spelling reports used, because one spelling is referenced")
print("       (last-wins would have said False and put a live field on a drop list)")
assert got["Sales"] is False, got
print("  ok   a field nothing references still reports unused")
assert got["Missing"] is None, got
print("  ok   a field the usage scan never saw is None - unknown, not unused")

# order must not change the answer
usage["fields"]["all"].reverse()
rows2 = [{"final_field": "Region"}]
core.attach_report_usage(rows2, usage)
assert rows2[0]["used_in_report"] is True, rows2
print("  ok   reversing the input order gives the same answer")

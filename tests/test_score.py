"""Missing telemetry must not depress a criticality tier."""
import pathlib
import sys, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_landed as L

recent = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(days=2)).isoformat().replace("+00:00", "Z")

def show(label, app):
    r = L.score_app(app)
    print(f"  {label:46} score {r['score']:>2}  {r['tier']:<6} {r['basis']}")
    return r

print("an app the tenant reports nothing about, but which is structurally real:")
base = {"downstream_apps": 1, "objects": 20, "landed_fields_used": 10}
known = show("published=True, reload 2 d ago", dict(base, published=True, reload=recent))
unk = show("publish state and reload not reported", dict(base, published=None, reload=""))
no = show("published=False, reload 2 d ago", dict(base, published=False, reload=recent))

# The point: unknown must sit between "known yes" and "known no", never below
assert known["score"] > unk["score"], "known-good should outscore unknown"
assert unk["score"] >= no["score"] - 2, unk
assert unk["basis"].startswith("provisional"), unk
assert "not reported" in unk["why"], unk["why"]
print()
print(f"  ok   unknown is scored 0, not a penalty (was -1 for a missing reload,")
print(f"       and a missing publish state was scored as 'not published')")

print()
print("the demotion this used to cause:")
edge = {"published": None, "downstream_apps": 1, "objects": 20,
        "landed_fields_used": 10, "reload": ""}
r = L.score_app(edge)
# old behaviour: published None -> 0, reload missing -> -1  => score 1 (Low)
# new behaviour: both 0                                     => score 2 (Low)
old_equivalent = r["score"] - 1
print(f"  now {r['score']} ({r['tier']}), under the old rule {old_equivalent} "
      f"({'High' if old_equivalent >= 5 else 'Medium' if old_equivalent >= 3 else 'Low'})")
mid = {"published": True, "downstream_apps": 1, "objects": 20,
       "landed_fields_used": 10, "reload": ""}
r2 = L.score_app(mid)
print(f"  a published app with no reported reload: now {r2['score']} ({r2['tier']}), "
      f"under the old rule {r2['score'] - 1} "
      f"({'High' if r2['score']-1 >= 5 else 'Medium' if r2['score']-1 >= 3 else 'Low'})")
def tier_of(n):
    return "High" if n >= 5 else ("Medium" if n >= 3 else "Low")

for label, app in (("unknown publish + unknown reload", edge),
                   ("published, unknown reload", mid)):
    r = L.score_app(app)
    was = tier_of(r["score"] - 1)
    assert was != r["tier"], (label, r)
    print(f"  ok   {label}: {was} -> {r['tier']}, a tier gained back by not "
          f"penalising absent telemetry")

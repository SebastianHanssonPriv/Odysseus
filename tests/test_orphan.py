"""An app that reads a runtime-named QVD must not leave the orphan list silent."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_capacity as qc
import script_cache as sc

# One extractor stores A.qvd; one consumer reads a name built at run time.
facts = {
    "g1": sc.facts_from_script(
        "T:\nSQL SELECT * FROM d.o;\nSTORE T INTO [lib://QVD/A.qvd](qvd);"),
    "g2": sc.facts_from_script(
        "LET vT = Upper('a');\nT:\nLOAD * FROM [lib://QVD/$(vT).qvd](qvd);"),
}
print("facts:")
for g, f in facts.items():
    print(f"  {g}: stores={sorted(f['stores'])} reads={sorted(f['reads'])} "
          f"unresolved={sorted(f['unresolved'])}")

profiles = {}
for g, f in facts.items():
    profiles[g] = {"loads_external": f["loads_external"], "source_kind": f["source_kind"],
                   "creates_export": bool(f["stores"]), "stores": f["stores"],
                   "reads": f["reads"], "unresolved": f.get("unresolved") or set()}

index = {"consumed": set().union(*(p["reads"] for p in profiles.values())),
         "produced": set().union(*(p["stores"] for p in profiles.values())),
         "scripts_read": 2, "scripts_total": 2, "errors": [],
         "runtime_named": [{"app": "Consumer", "guid": "g2",
                            "names": sorted(profiles["g2"]["unresolved"])}]}

imp = {"data_files": [{"name": "A.qvd", "size_bytes": 5_000_000, "space": "DW"}],
       "datasets": []}
res = qc.detect_orphans(imp, index)
print()
print("orphan files   :", [r["name"] for r in res["orphan_files"]])
print("produced_only  :", [r["name"] for r in res["produced_only_files"]])
print("low_confidence :", res["low_confidence"], "(the scan DID finish)")
rn = res["runtime_named_readers"]
print("runtime named  :", rn)
assert rn and rn[0]["guid"] == "g2", rn
assert res["low_confidence"] is False, res["low_confidence"]
print()
print("  ok   A.qvd is reported as produced-but-unread, which is what the")
print("       consumed set supports - its only reader names it at run time")
print("  ok   the runtime-named reader is carried alongside, so the report can")
print("       say 'verify this before deleting' instead of implying certainty")
print("  ok   low_confidence stays False - the scan finished; this is a")
print("       different kind of gap and is reported separately")

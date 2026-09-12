"""Extractor classification, against the naming seen on the real tenant."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_landed as L
import script_cache as sc

def facts(script):
    return sc.facts_from_script(script)

CASES = [
    # (label, script, expect_extractor)
    ("SQL source, stores a QVD - the textbook extractor",
     "SQL_TABLE:\nSQL SELECT * FROM dbo.Orders;\nSTORE SQL_TABLE INTO [lib://QVD/Orders.qvd](qvd);",
     True),
    ("named 'QVD creator', which the old rule missed entirely",
     "T:\nSQL SELECT * FROM dbo.Stock;\nSTORE T INTO [lib://QVD/ABC_Inventory.qvd](qvd);",
     True),
    ("CSV drop, stores a QVD - external data even though not SQL",
     "T:\nLOAD * FROM [lib://Drop/prices.csv] (txt, delimiter is ';');\n"
     "STORE T INTO [lib://QVD/Prices.qvd](qvd);",
     True),
    ("transform layer: reads QVDs only, stores a QVD - NOT an extractor",
     "T:\nLOAD * FROM [lib://QVD/Orders.qvd](qvd);\n"
     "STORE T INTO [lib://QVD/Orders_Agg.qvd](qvd);",
     False),
    ("binary load then store - NOT an extractor",
     "BINARY [lib://Apps/Other.qvf];\nSTORE X INTO [lib://QVD/Copy.qvd](qvd);",
     False),
    ("reporting app: SQL source but stores nothing",
     "T:\nSQL SELECT * FROM dbo.Orders;",
     False),
    ("app literally named Extractor but only reads QVDs - the old false positive",
     "T:\nLOAD * FROM [lib://QVD/Orders.qvd](qvd);",
     False),
]

print("classification:")
bad = 0
for label, script, expect in CASES:
    f = facts(script)
    reason = L.extractor_reason(f)
    got = bool(reason)
    mark = "ok " if got == expect else "FAIL"
    if got != expect:
        bad += 1
    print(f"  {mark} {label[:56]:56} {'extractor' if got else 'not':>9}"
          + (f"  [{reason}]" if reason else ""))

# an unreadable script must be "unknown", never "not an extractor"
assert L.extractor_reason(sc.UNREADABLE) == "", "unreadable must not classify"
assert L.extractor_reason(None) == "", "missing facts must not classify"
print("  ok  unreadable / missing facts classify as neither, so the scan skips them")

# and the whole scan end to end, with the real naming
apps = [
    {"guid": "g1", "name": "ABC Inventory QVD creator [to KPIs]", "space_name": "DW"},
    {"guid": "g2", "name": "Transform DW ExchangeRate", "space_name": "DW"},
    {"guid": "g3", "name": "Inventory Report v1.0", "space_name": "Reporting"},
]
F = {
    "g1": facts("T:\nSQL SELECT * FROM dbo.Stock;\n"
                "STORE T INTO [lib://QVD/ABC_Inventory.qvd](qvd);"),
    "g2": facts("T:\nLOAD * FROM [lib://QVD/ABC_Inventory.qvd](qvd);\n"
                "STORE T INTO [lib://QVD/Rate.qvd](qvd);"),
    "g3": facts("T:\nLOAD ItemNo, Qty FROM [lib://QVD/ABC_Inventory.qvd](qvd);"),
}
def read_consumer(guid):
    return {"script": "T:\nLOAD ItemNo, Qty FROM [lib://QVD/ABC_Inventory.qvd](qvd);",
            "model_fields": [{"name": "ItemNo"}, {"name": "Qty"}], "objects": [], "usage_result": None}

logs = []
res = L.scan_landed_impact(apps, F, read_consumer, logs.append)
print()
print("end to end:")
print("  " + logs[0])
names = [e["name"] for e in res["extractors"]]
assert names == ["ABC Inventory QVD creator [to KPIs]"], names
print(f"  extractors: {names}")
print(f"  why       : {res['extractors'][0]['why']}")
assert "abc_inventory.qvd" in res["qvds"][0]["qvd"], res["qvds"]
print(f"  landed QVD: {res['qvds'][0]['qvd']} produced by {res['qvds'][0]['producers']}")
consumers = sorted(c["name"] for c in res["consumers"])
assert consumers == ["Inventory Report v1.0", "Transform DW ExchangeRate"], consumers
print(f"  consumers : {consumers}")
print()
print("Under the old name rule this scan returned nothing at all: no app here")
print("has 'extractor' in its name, so there were no landed QVDs to report.")
sys.exit(1 if bad else 0)

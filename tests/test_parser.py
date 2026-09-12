"""Script parsing: comments, variable paths, and the STORE forms."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

def case(label, script, stores, reads, unresolved=()):
    st, rd, un = core.parse_store_reads(script, with_unresolved=True)
    ok = (st == set(stores) and rd == set(reads) and un == set(unresolved))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        print(f"       stores     got {sorted(st)} want {sorted(stores)}")
        print(f"       reads      got {sorted(rd)} want {sorted(reads)}")
        print(f"       unresolved got {sorted(un)} want {sorted(unresolved)}")
    return ok

print("comments must not create phantom QVDs:")
ok = []
ok.append(case("// commented STORE and LOAD are ignored",
    "// STORE X INTO [lib://QVD/Old.qvd](qvd);\n"
    "// LOAD * FROM [lib://QVD/Legacy.qvd](qvd);\n"
    "T:\nSQL SELECT 1;\nSTORE T INTO [lib://QVD/Real.qvd](qvd);",
    ["real.qvd"], []))
ok.append(case("/* block */ comment is ignored",
    "/*\nSTORE X INTO [lib://QVD/Old.qvd](qvd);\n*/\n"
    "T:\nLOAD * FROM [lib://QVD/Live.qvd](qvd);",
    [], ["live.qvd"]))
ok.append(case("REM ...; is ignored",
    "REM STORE X INTO [lib://QVD/Old.qvd](qvd);\n"
    "T:\nLOAD * FROM [lib://QVD/Live.qvd](qvd);",
    [], ["live.qvd"]))
ok.append(case("a trailing comment after a live statement keeps the statement",
    "T:\nLOAD * FROM [lib://QVD/Live.qvd](qvd);   // was Old.qvd before 2024",
    [], ["live.qvd"]))
ok.append(case("lib:// survives - its // is not a comment",
    "T:\nLOAD * FROM [lib://QVD_Prod/Live.qvd](qvd);", [], ["live.qvd"]))

print()
print("variable-built paths, the normal way Qlik scripts are written:")
ok.append(case("SET with a trailing slash",
    "SET vQVD = 'lib://QVD_Prod/';\nT:\nSQL SELECT 1;\n"
    "STORE T INTO [$(vQVD)Orders.qvd](qvd);",
    ["orders.qvd"], []))
ok.append(case("SET without a slash, joined by one in the path",
    "SET vQVD = 'lib://QVD_Prod';\nT:\nSQL SELECT 1;\n"
    "STORE T INTO [$(vQVD)/Orders.qvd](qvd);",
    ["orders.qvd"], []))
ok.append(case("a variable defined from another variable",
    "SET vRoot = 'lib://DL';\nSET vQVD = '$(vRoot)/QVD/';\nT:\nSQL SELECT 1;\n"
    "STORE T INTO [$(vQVD)Orders.qvd](qvd);",
    ["orders.qvd"], []))
ok.append(case("a name built at runtime stays UNRESOLVED, not invented",
    "LET vTbl = Upper('orders');\nT:\nSQL SELECT 1;\n"
    "STORE T INTO [lib://QVD/$(vTbl).qvd](qvd);",
    [], [], ["$(vtbl).qvd"]))

print()
print("STORE forms:")
ok.append(case("bracketed path with a (qvd) suffix",
    "STORE T INTO [lib://QVD/A.qvd](qvd);", ["a.qvd"], []))
ok.append(case("bare path, no brackets",
    "STORE T INTO lib://QVD/A.qvd(qvd);", ["a.qvd"], []))
ok.append(case("bare path, no suffix",
    "STORE T INTO lib://QVD/A.qvd;", ["a.qvd"], []))
ok.append(case("STORE of a txt is not a QVD store",
    "STORE T INTO [lib://Out/A.txt](txt);\nT2:\nLOAD * FROM [lib://QVD/B.qvd](qvd);",
    [], ["b.qvd"]))
ok.append(case("field list between STORE and INTO",
    "STORE ItemNo, Qty FROM T INTO [lib://QVD/A.qvd](qvd);", ["a.qvd"], []))
ok.append(case("an app both stores and reads - its own output is not a read",
    "A:\nLOAD * FROM [lib://QVD/In.qvd](qvd);\nSTORE A INTO [lib://QVD/Out.qvd](qvd);",
    ["out.qvd"], ["in.qvd"]))

print()
print("regression: the realistic script from the review")
SCRIPT = """
// Old pipeline, replaced 2023.
// STORE Orders INTO [lib://QVD/Orders_OLD.qvd](qvd);
// LOAD * FROM [lib://QVD/Legacy_Prices.qvd](qvd);
/*  Superseded
    STORE Stock INTO [lib://QVD/Stock_v1.qvd](qvd);
*/
SET vQVD = 'lib://QVD_Prod/';
Orders:
SQL SELECT * FROM dbo.Orders;
STORE Orders INTO [$(vQVD)Orders.qvd](qvd);
Stock:
LOAD * FROM [$(vQVD)Stock.qvd](qvd);
Prices:
LOAD * FROM [lib://QVD_Prod/Prices.qvd](qvd);
"""
ok.append(case("all three defects at once",
    SCRIPT, ["orders.qvd"], ["stock.qvd", "prices.qvd"]))

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)

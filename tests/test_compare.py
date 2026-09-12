"""The comparison key: same calculation same key, different calculation different key."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core
t = core._tight

SAME = [
    ("optional brackets round a plain identifier", "Sum([Sales])", "Sum(Sales)"),
    ("whitespace and case", "Sum( Sales )", "sum(sales)"),
    ("a trailing line comment", "Sum(Sales) // net of returns", "Sum(Sales)"),
    ("a block comment in the middle", "Sum(/* net */ Sales)", "Sum(Sales)"),
    ("brackets on both, spaced differently", "Sum( [Sales Amount] )", "Sum([Sales Amount])"),
    ("case inside a literal (Qlik compares case-insensitively)",
     "if(Country='SE',1,0)", "if(Country='se',1,0)"),
    ("newlines between arguments", "if(\n  X>0,\n  1,\n  0\n)", "if(X>0,1,0)"),
]
DIFFERENT = [
    ("whitespace INSIDE a string literal is a real difference",
     "if(Country='United Kingdom',1,0)", "if(Country='UnitedKingdom',1,0)"),
    ("a bracketed name with a space is not the same as one without",
     "Sum([Sales Amount])", "Sum([SalesAmount])"),
    ("single vs double quotes: 'X' is a string, \"X\" is a field",
     "if(Region='North',1,0)", 'if(Region="North",1,0)'),
    ("genuinely different aggregation", "Sum(Sales)", "Avg(Sales)"),
    ("different field", "Sum(Sales)", "Sum(Cost)"),
    ("a comment is stripped but the code around it is not",
     "Sum(Sales) // net", "Sum(Cost) // net"),
]

ok = []
print("must compare EQUAL:")
for label, a, b in SAME:
    good = t(a) == t(b)
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
    if not good:
        print(f"       {t(a)!r} != {t(b)!r}")

print()
print("must compare DIFFERENT:")
for label, a, b in DIFFERENT:
    good = t(a) != t(b)
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
    if not good:
        print(f"       both -> {t(a)!r}")

print()
print("edge cases that must not crash:")
for label, v in (("empty", ""), ("None", None), ("unterminated quote", "if(X='abc"),
                 ("unterminated bracket", "Sum([Sales"),
                 ("unterminated block comment", "Sum(Sales) /* oops")):
    try:
        r = t(v)
        print(f"  ok   {label:28} -> {r!r}")
        ok.append(True)
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        ok.append(False)

print()
print("effect on the report, end to end:")
measures = [
    {"name": "Net Sales", "expression": "Sum([Sales])", "app": "App A"},
    {"name": "Net Sales", "expression": "Sum(Sales)   // same thing", "app": "App B"},
    {"name": "Turnover",  "expression": "Sum(Sales)", "app": "App C"},
    {"name": "UK flag",   "expression": "if(Country='United Kingdom',1,0)", "app": "App A"},
    {"name": "UK flag",   "expression": "if(Country='UnitedKingdom',1,0)", "app": "App B"},
]
res = core.analyze_consistency(measures, [])
conf = {c["name"]: c["variant_count"] for c in res["measure_name_conflicts"]}
print("  name conflicts :", conf)
assert "Net Sales" not in conf, "three spellings of one calculation are not a conflict"
assert conf.get("UK flag") == 2, conf
print("  ok   'Net Sales' is no longer a false conflict (brackets + comment)")
print("  ok   'UK flag' IS a conflict - the literals genuinely differ")
red = [(r["names"], r["occurrences"]) for r in res["measure_redundancy"]]
print("  redundancy     :", red)
assert any(set(n) == {"Net Sales", "Turnover"} for n, _ in red), red
print("  ok   Net Sales and Turnover are correctly found to be the same calculation")

print()
print(f"{sum(ok)} of {len(ok)} key assertions pass")
sys.exit(0 if all(ok) else 1)

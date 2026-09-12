"""What counts as referencing a field."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core
r = core._referenced_names

CASES = [
    # (label, expression, field, expected_used)
    ("a bracketed field reference", "Sum([Region])", "region", True),
    ("a bare field reference", "Sum(Region)", "region", True),
    ("a set-analysis modifier is a real reference", "Sum({<Region={'North'}>} Sales)",
     "region", True),
    ("only in a line comment", "// TODO: add Region later", "region", False),
    ("only in a block comment", "Sum(Sales) /* Region split pending */", "region", False),
    ("only inside a string literal", "if(Country='Region',1,0)", "region", False),
    ("in a literal, but the expression is dynamic", "$(='if(X=' & 'Region' & ',1,0)')",
     "region", True),
    ("code survives when a comment is stripped", "Sum(Region) // per Region",
     "region", True),
    ("the field beside a literal still counts", "if(Country='SE', Sum(Region), 0)",
     "region", True),
]
ok = []
for label, expr, field, want in CASES:
    got = field in r(expr)
    ok.append(got == want)
    print(f"  {'ok  ' if got == want else 'FAIL'} {label:52} {'used' if got else 'not used'}")

# the documented looseness: a field named like a function still reads as used
print()
print("  documented and deliberate:")
print(f"    a field called 'year' in an app using Year(): "
      f"{'used' if 'year' in r('Year(OrderDate)') else 'not used'} "
      f"(under-reports rather than over-reports)")

# and the end-to-end effect on the unused list
fields = [{"name": "Region", "src_tables": ["T"], "is_system": False,
           "is_hidden": False, "is_key": False, "tags": ""},
          {"name": "Sales", "src_tables": ["T"], "is_system": False,
           "is_hidden": False, "is_key": False, "tags": ""}]
corpus = ["Sum([Sales])", "// Region is handled downstream"]
res = core.analyze_field_usage(fields, corpus)
unused = sorted(f["name"] for f in res["unused"])
print()
print("  unused candidates:", unused)
assert unused == ["Region"], unused
ok.append(True)
print("  ok   a field mentioned only in a comment is no longer hidden from the list")

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)

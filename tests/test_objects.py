"""A field used only in a colour expression, condition or sort must read as USED."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

# A barchart shaped the way Qlik actually stores one: a master measure, a
# colour-by-expression, a dynamic label, a calc condition, a sort expression,
# a footnote, and a master dimension referenced from an attribute expression.
PROP = {
    "qInfo": {"qId": "abc123", "qType": "barchart"},
    "visualization": "barchart",
    "title": {"qStringExpression": {"qExpr": "'Sales by ' & [Region Label]"}},
    "footnote": {"qStringExpression": {"qExpr": "Only $(vScope) rows"}},
    "showTitles": True,
    "qHyperCubeDef": {
        "qDimensions": [{
            "qLibraryId": "dimLib1",
            "qAttributeExpressions": [{"qExpression": "[Region Colour]"}],
        }],
        "qMeasures": [{
            "qLibraryId": "measLib1",
            "qAttributeExpressions": [
                {"qExpression": "if([Margin Pct] < 0, red(), green())", "id": "colorByExpression"}],
            "qDef": {"qLabelExpression": "'Sales in ' & [Currency Code]",
                     "qLabel": "Sales",
                     "qSortBy": {"qSortByExpression": 1},
                     "qNumFormat": {"qFmt": "#,##0"}},
        }],
        "qCalcCondition": {"qCond": {"qv": "Count([Order No]) > 0"},
                           "qMsg": {"qv": "No orders"}},
        "qInterColumnSortOrder": [0, 1],
    },
    "qLayoutExclude": {"qHyperCubeDef": {}},
    "showCondition": {"qStringExpression": {"qExpr": "[User Role] = 'Admin'"}},
    "color": {"expressionColor": {"expression": "[Status Flag]"}},
    "someExtensionProp": {"myFormulaExpression": "Sum([Extension Field])"},
}

rows = []
core.QlikExporter._walk_struct(PROP, "Sales sheet", rows)
assert len(rows) == 1, rows
row = rows[0]
exprs = " | ".join(row["expressions"])

print("captured expressions:")
for e in row["expressions"]:
    print("   ", e)
print()
print("master items referenced:", sorted(set(row["measure_libs"]) | set(row["dim_libs"])))
print()

# every field that only appears in a "hidden" place must now be detectable
corpus = core._usage_corpus([], [], [], rows)
refs = set()
for t in corpus:
    refs |= core._referenced_names(t)

MUST_BE_USED = ["Region Colour", "Margin Pct", "Currency Code", "Order No",
                "User Role", "Status Flag", "Extension Field", "Region Label"]
bad = [f for f in MUST_BE_USED if f.lower() not in refs]
for f in MUST_BE_USED:
    print(f"  {'ok  ' if f.lower() in refs else 'FAIL'} [{f}]")
assert not bad, f"still invisible: {bad}"

# and the precision rule: a plain qLabel must NOT be swept in
assert "Sales" not in row["expressions"], "qLabel was harvested as an expression"
print("  ok   a plain qLabel is not harvested (precision held)")
assert "#,##0" not in exprs, "a number format was harvested"
print("  ok   a number format is not harvested")

# both master items found, including the one only in an attribute expression
assert "measLib1" in row["measure_libs"], row
assert "dimLib1" in row["dim_libs"], row
print("  ok   both master items detected")

# Guard: module-level code placed inside the class body silently ends the
# class, and every method after it stops being a method. That happened while
# writing this change and only a runtime call would have caught it.
EXPECTED_METHODS = ("_walk_struct", "fetch_objects", "fetch_model_fields",
                    "fetch_script", "fetch_measures", "fetch_dimensions",
                    "fetch_variables", "fetch_lineage", "apply_master",
                    "connect", "close", "call")
missing = [m for m in EXPECTED_METHODS if not hasattr(core.QlikExporter, m)]
assert not missing, f"QlikExporter lost methods: {missing}"
print(f"  ok   QlikExporter still exposes all {len(EXPECTED_METHODS)} checked methods")

print()
print("Before this change only the hypercube's inline defs and the title were")
print("read, so 7 of those 8 fields were reported as unused candidates.")

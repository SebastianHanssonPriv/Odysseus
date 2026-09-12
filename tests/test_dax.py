"""A dataset with no DAX must not report every column as unused."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import model_lineage as ml
import pbi_landed as pl

WITH_DAX = [
    {"name": "Sales", "columns": [{"name": "Amount"}, {"name": "Qty"},
                                  {"name": "Margin", "expression": "[Amount] - [Cost]"}],
     "measures": [{"name": "Total", "expression": "SUM(Sales[Amount])"}]},
]
NO_DAX = [
    {"name": "Sales", "columns": [{"name": "Amount"}, {"name": "Qty"}], "measures": []},
]
DAX_SUPPRESSED = [   # the tenant setting is off: names present, expressions absent
    {"name": "Sales", "columns": [{"name": "Amount"}, {"name": "Qty"},
                                  {"name": "Margin", "expression": None}],
     "measures": [{"name": "Total", "expression": None}]},
]

for label, tables, expect_has_dax in (("dataset with real DAX", WITH_DAX, True),
                                      ("dataset with no measures at all", NO_DAX, False),
                                      ("tenant setting off: expressions empty",
                                       DAX_SUPPRESSED, False)):
    n = ml.dax_expression_count(tables)
    refs = ml.dax_referenced_fields(tables)
    usage = ml._column_usage_for_table(tables[0], refs, has_dax=bool(n))
    vals = {u["column"]: u["used_in_dax"] for u in usage}
    print(f"  {label:42} dax expressions={n}  {vals}")
    assert bool(n) == expect_has_dax, label
    if expect_has_dax:
        assert vals["Amount"] is True and vals["Qty"] is False, vals
    else:
        assert all(v is None for v in vals.values()), vals

print()
print("  ok   with DAX: Amount True (referenced), Qty False (genuinely not referenced)")
print("  ok   without DAX: every column None - unknown, never False")

# and the state it becomes downstream
def states_for(used_in_dax):
    lineage = [{"workspace_name": "WS", "dataset_name": "DS", "dataset_id": "d1",
                "table_name": "Sales", "status": "resolved",
                "hops": [{"dataflow_id": "df1", "entity": "Sales"}],
                "connectors": ["Dataflow"], "source_tables": ["Sales"],
                "selected_fields": ["Amount", "Qty"],
                "direct_sources": [{"selected_fields": ["Amount", "Qty"]}],
                "column_usage": [{"column": "Amount", "used_in_dax": used_in_dax},
                                 {"column": "Qty", "used_in_dax": used_in_dax}]}]
    res = pl.scan_dataflow_impact(lineage, log=lambda m: None)
    return sorted({r["state"] for r in res["reach"]}), res

st_none, res_none = states_for(None)
st_false, _ = states_for(False)
print()
print("  states when DAX is unavailable:", st_none)
print("  states when DAX exists, no ref:", st_false)
assert pl.STATE_DAX_UNKNOWN in st_none, st_none
assert pl.STATE_IN_MODEL_UNUSED in st_false, st_false
print("  ok   the two cases are now distinguishable in the report")

# a column in a DAX-less dataset still counts as reaching the model
reach_flags = {r["field"]: r["reaches_a_model"] for r in res_none["fields"]}
print("  reaches_a_model:", reach_flags)
# "(not narrowed)" is a placeholder row for fields the dataflow did not
# enumerate; it reaches no model by definition. The real columns must.
real = {k: v for k, v in reach_flags.items() if not k.startswith("(")}
assert real and all(real.values()), reach_flags
print("  ok   an unknown DAX check does not make the column stop existing")

txt = pl.render_text(res_none)
line = [l for l in txt.split("\n") if "no DAX at all" in l]
assert line, txt
print()
print("  summary:", line[0].strip()[:96])

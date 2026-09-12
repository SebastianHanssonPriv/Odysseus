"""A partly-scanned tenant must not read as a fully-scanned one."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import model_lineage as ml
import scanner

ok = []

# A stub Scanner: the second batch of workspaces fails outright.
class FakeClient:
    pass

WS = [f"ws{i}" for i in range(150)]      # two batches at _SCAN_BATCH_SIZE=100

def fake_list_workspace_ids(client):
    return iter(WS)

def fake_scan_workspaces(client, ids, timeout):
    batches = [ids[:100], ids[100:]]
    for n, batch in enumerate(batches):
        if n == 1:                       # the second batch fails outright
            yield {"scan_batch_error": "504 Gateway Timeout", "workspace_ids": batch}
            continue
        yield {"id": "ws0", "name": "Finance", "datasets": [
            {"id": "d1", "name": "Sales", "tables": [
                {"name": "Orders",
                 "columns": [{"name": "Amount"}, {"name": "Margin",
                                                  "expression": "[Amount]*2"}],
                 "measures": [{"name": "T", "expression": "SUM(Orders[Amount])"}],
                 "source": [{"expression": 'let S = Sql.Database("srv","DB") in S'}]}]}]}

scanner.list_workspace_ids = fake_list_workspace_ids
scanner.scan_workspaces = fake_scan_workspaces

logs = []
sink = {}
results = ml.scan_model_lineage(FakeClient(), log=logs.append, sink=sink)
cov = sink.get("coverage")
print("coverage recorded:", {k: (len(v) if isinstance(v, list) else v) for k, v in cov.items()})
ok.append(cov["workspaces_requested"] == 150)
ok.append(cov["batch_errors"] == 1)
ok.append(len(cov["workspaces_missed"]) == 50)
print(f"  {'ok  ' if all(ok) else 'FAIL'} 150 requested, 1 batch error, 50 workspaces missed")

warned = [l for l in logs if "NOT scanned" in l]
ok.append(bool(warned))
print(f"  {'ok  ' if ok[-1] else 'FAIL'} the log says the counts exclude them")
print(f"       {warned[0][:100] if warned else ''}")

text = ml.render_model_lineage_text(results, coverage=cov)
ok.append("INCOMPLETE SCAN" in text)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} the text summary leads with INCOMPLETE SCAN")
print("       " + [l for l in text.split("\n") if "INCOMPLETE" in l][0][:96])

# and without coverage, nothing changes for an older caller
plain = ml.render_model_lineage_text(results)
ok.append("INCOMPLETE SCAN" not in plain)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a caller passing no coverage sees the old output")

# a clean scan must not be labelled incomplete
clean = {"workspaces_requested": 150, "batch_errors": 0, "workspaces_missed": [],
         "cancelled": False}
ok.append("INCOMPLETE SCAN" not in ml.render_model_lineage_text(results, coverage=clean))
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a clean scan is not labelled incomplete")

# cancellation is its own note
cancelled = dict(clean, cancelled=True)
ok.append("CANCELLED" in ml.render_model_lineage_text(results, coverage=cancelled))
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a cancelled scan says so separately")

try:
    import openpyxl  # noqa: F401
    import tempfile
    out = tempfile.mkdtemp()
    path = ml.write_model_lineage_report(results, out, lambda m: None, coverage=cov)
    wb = openpyxl.load_workbook(path)
    rows = [tuple(r) for r in wb["Summary"].iter_rows(values_only=True)]
    top = [r for r in rows[:8]]
    print()
    print("  workbook Summary, first rows:")
    for r in top:
        print(f"    {r}")
    ok.append(any("SCAN COVERAGE" in str(r[0]) for r in rows))
    ok.append(any("NOT scanned" in str(r[0]) for r in rows))
    print(f"  {'ok  ' if ok[-2] and ok[-1] else 'FAIL'} the sheet leads with coverage, "
          f"before any result count")
except ImportError:
    print("  (openpyxl absent - workbook check skipped)")

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)

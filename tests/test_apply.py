"""apply_master against a stubbed engine: what happens to duplicate titles."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

class FakeEngine(core.QlikExporter):
    def __init__(self, titles):
        # no connect: only apply_master is exercised
        self.logs = []
        self.calls = []
        self._titles = titles          # [(title, id)]
        self.log = self.logs.append

    def call(self, handle, method, params):
        self.calls.append((method, params))
        if method == "CreateSessionObject":
            return {"qReturn": {"qHandle": 99}}
        if method == "GetLayout":
            return {"qLayout": {"qMeasureList": {"qItems": [
                {"qInfo": {"qId": i}, "qMeta": {"title": t}} for t, i in self._titles]}}}
        if method == "GetMeasure":
            return {"qReturn": {"qHandle": 7}}
        if method == "GetProperties":
            return {"qProp": {"qInfo": {"qId": "x"}, "qMeasure": {}, "qMetaDef": {}}}
        return {"qReturn": {"qHandle": 0}}

def run(titles, rows, mode, dry=False):
    e = FakeEngine(titles)
    counts = e.apply_master(1, "measure", rows, mode, dry)
    destroys = [p for m, p in e.calls if m == "DestroyMeasure"]
    creates = [p for m, p in e.calls if m == "CreateMeasure"]
    sets = [p for m, p in e.calls if m == "SetProperties"]
    return counts, destroys, creates, sets, e.logs

print("1. two existing master measures share the title 'Net Sales':")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1"), ("Net Sales", "id2")],
    [{"name": "Net Sales", "expression": "Sum(Sales)"}], "delete")
print(f"   counts        : {counts}")
print(f"   DestroyMeasure: {len(destroys)} call(s) -> {destroys}")
print(f"   >> the user is told {counts['deleted']} deleted; "
      f"{2 - len(destroys)} copy remains in the app")

print()
print("2. the CSV says 'net sales ' and the app has 'Net Sales':")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1")],
    [{"name": "net sales", "expression": "Sum(Sales)"}], "upsert")
print(f"   counts        : {counts}")
print(f"   CreateMeasure : {len(creates)} call(s)")
print(f"   SetProperties : {len(sets)} call(s)")
print(f"   >> a second master measure is created next to the first")

print()
print("=" * 66)
print("AFTER THE FIX")
print("=" * 66)
ok = []

print()
print("1. duplicate titles, delete:")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1"), ("Net Sales", "id2")],
    [{"name": "Net Sales"}], "delete")
print(f"   counts={counts}  destroys={destroys}")
ok.append(counts["deleted"] == 2 and len(destroys) == 2)
print(f"   {'ok  ' if ok[-1] else 'FAIL'} both copies destroyed, count says 2")

print()
print("2. duplicate titles, upsert:")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1"), ("Net Sales", "id2")],
    [{"name": "Net Sales", "expression": "Sum(Sales)"}], "upsert")
print(f"   counts={counts}  SetProperties={len(sets)}")
ok.append(counts["updated"] == 2 and len(sets) == 2)
print(f"   {'ok  ' if ok[-1] else 'FAIL'} both definitions updated, so the title means one thing")
ok.append(any("share the title" in l for l in logs))
print(f"   {'ok  ' if ok[-1] else 'FAIL'} the log says why it wrote twice")

print()
print("3. near miss: CSV 'net sales', app 'Net Sales':")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1")],
    [{"name": "net sales", "expression": "Sum(Sales)"}], "upsert")
print(f"   counts={counts}  creates={len(creates)}  sets={len(sets)}")
ok.append(counts["ambiguous"] == 1 and not creates and not sets)
print(f"   {'ok  ' if ok[-1] else 'FAIL'} nothing written - no duplicate created, no wrong item updated")
print(f"   log: {[l for l in logs if 'AMBIGUOUS' in l][0][:110]}")

print()
print("4. an exact match still works normally:")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1")], [{"name": "Net Sales", "expression": "Sum(Sales)"}], "upsert")
ok.append(counts["updated"] == 1 and counts["ambiguous"] == 0)
print(f"   counts={counts}")
print(f"   {'ok  ' if ok[-1] else 'FAIL'} one update, no ambiguity flagged")

print()
print("5. a genuinely new name is created:")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1")], [{"name": "Gross Margin", "expression": "Sum(M)"}], "upsert")
ok.append(counts["created"] == 1 and len(creates) == 1)
print(f"   counts={counts}")
print(f"   {'ok  ' if ok[-1] else 'FAIL'} created once")

print()
print("6. the same new name twice in one CSV is created once:")
counts, destroys, creates, sets, logs = run(
    [], [{"name": "New One", "expression": "Sum(A)"},
         {"name": "New One", "expression": "Sum(B)"}], "upsert")
print(f"   counts={counts}  creates={len(creates)}  sets={len(sets)}")
ok.append(counts["created"] == 1 and counts["updated"] == 1)
print(f"   {'ok  ' if ok[-1] else 'FAIL'} created then updated, not created twice")

print()
print("7. dry run writes nothing at all:")
counts, destroys, creates, sets, logs = run(
    [("Net Sales", "id1"), ("Net Sales", "id2")],
    [{"name": "Net Sales"}], "delete", dry=True)
ok.append(counts["deleted"] == 2 and not destroys)
print(f"   counts={counts}  destroys={len(destroys)}")
print(f"   {'ok  ' if ok[-1] else 'FAIL'} reports what it would do, calls nothing")

print()
print("8. a missing title in the layout does not crash:")
e = FakeEngine([])
e._titles = []
class Odd(FakeEngine):
    def call(self, h, m, p):
        if m == "GetLayout":
            return {"qLayout": {"qMeasureList": {"qItems": [
                {"qInfo": {}, "qMeta": {}}, {"qInfo": {"qId": "ok1"}}]}}}
        return FakeEngine.call(self, h, m, p)
o = Odd([])
c = o.apply_master(1, "measure", [{"name": "X", "expression": "1"}], "upsert", True)
ok.append(c["created"] == 1)
print(f"   counts={c}")
print(f"   {'ok  ' if ok[-1] else 'FAIL'} items with no id or no title are ignored")

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)

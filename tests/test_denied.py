"""A tenant-wide scan must not grind through 1,900 apps to tell you one thing.

Reproduces the real run: an API key that can open apps but cannot read their
load scripts, so every GetScript returns Access denied. The old behaviour was
one log line per failure, a retry per app (two sessions each for an error that
cannot succeed twice), no early stop, and finally a summary saying "no app
stores a QVD" - a statement about the tenant, from a run that learned nothing
about it.
"""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core
import qlik_landed as landed
import script_cache as sc

GUIDS = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(1908)]
DENIED = "{'code': 5, 'parameter': 'GetScript', 'message': 'Access denied'}"

sessions = {"n": 0}


class FakeExporter:
    def __init__(self, tenant, key, guid, out, log=None):
        self.guid = guid
    def connect(self):
        sessions["n"] += 1
    def call(self, h, method, params):
        return {"qReturn": {"qHandle": 1}}
    def fetch_script(self, h):
        raise RuntimeError(DENIED)
    def close(self):
        pass


core.QlikExporter = FakeExporter
logs, errs = [], {}
out = core.fetch_scripts("t", "k", GUIDS, log=logs.append, errors=errs)

print(f"  apps listed              : {len(GUIDS)}")
print(f"  engine sessions opened   : {sessions['n']}")
print(f"  results returned         : {len(out)}")
print(f"  log lines                : {len(logs)}")
assert sessions["n"] < 120, f"opened {sessions['n']} sessions before stopping"
print(f"  ok   stopped early - well under the 3,816 the old path would open")
assert len(logs) < 25, f"{len(logs)} log lines"
print(f"  ok   the log is readable ({len(logs)} lines, not ~1,900)")

# exactly one session per failed app, because a denial is not retried
listed = [l for l in logs if "could not read" in l]
assert len(listed) == 5, listed
print("  ok   five failures shown in full, the rest counted")
assert any("further read failures are counted" in l for l in logs)
assert any(" x  " in l and "Access denied" in l for l in logs)
print("  ok   a grouped tally replaces the repetition")

stop = [l for l in logs if "STOPPED after" in l]
assert stop, logs
print(f"  ok   {stop[0].strip()[:78]}")
assert any("permissions, not the apps" in l for l in logs)
assert any("Professional entitlement" in l for l in logs)
assert any("Managed space" in l for l in logs)
assert any("Diagnose visibility" in l for l in logs)
print("  ok   the diagnosis names the cause and the next step")

print()
print("and the report says what actually happened:")
apps = [{"guid": g, "name": f"App {i}", "space_name": "s"} for i, g in enumerate(GUIDS[:50])]
facts = {a["guid"]: sc.UNREADABLE for a in apps}
llog = []
res = landed.scan_landed_impact(apps, facts, lambda g: None, llog.append)
assert res.get("no_scripts_readable") is True, res.keys()
print("  " + llog[0][:100])
text = landed.render_text(res)
first = text.split("\n")[0]
print(f"  summary first line: {first}")
assert "NO LOAD SCRIPT COULD BE READ" in text
assert "permissions result, not a finding about the tenant" in text
print("  ok   not reported as 'no app stores a QVD'")

print()
print("a transient error is still retried, and a mixed run still succeeds:")
calls = {"n": 0}
class Flaky(FakeExporter):
    def fetch_script(self, h):
        calls["n"] += 1
        if self.guid.startswith("00000000"):
            if calls["n"] < 2:
                raise RuntimeError("Connection to remote host was lost.")
            return "STORE T INTO [lib://QVD/A.qvd](qvd);"
        return "LOAD * FROM [lib://QVD/A.qvd](qvd);"
core.QlikExporter = Flaky
sessions["n"] = 0
logs2 = []
out2 = core.fetch_scripts("t", "k", GUIDS[:10], log=logs2.append)
assert all(v is not None for v in out2.values()), out2
print(f"  ok   10 of 10 read, the transient failure retried "
      f"({sessions['n']} sessions for 10 apps)")
assert not any("STOPPED" in l for l in logs2)
print("  ok   a working run is not aborted")

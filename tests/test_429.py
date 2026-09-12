"""A 429 on the lineage call must be retried, not surfaced as 'unavailable'."""
import pathlib
import io, json, sys, time, urllib.error, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

GRAPH = {"graph": {"nodes": {"qri:app:sense://a" * 1: {"label": "x", "metadata": {}}},
                   "edges": []}}
slept = []
core.time.sleep = slept.append

class Resp(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {}            # a real HTTPResponse always has these
    def __enter__(self): return self
    def __exit__(self, *a): return False

class Hdr(dict):
    def get(self, k, d=None): return dict.get(self, k, d)

attempts = []
def flaky(req, timeout=None):
    attempts.append(req.full_url)
    if len(attempts) <= 2:
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                     Hdr({"Retry-After": "2"}), None)
    return Resp(json.dumps(GRAPH).encode())

urllib.request.urlopen = flaky
core.normalize_host = lambda t: "t.example"

g = core.fetch_native_lineage("t.example", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")
assert len(attempts) == 3, attempts
assert slept == [2.0, 2.0], slept
assert g["nodes"], g
print(f"PASS: two 429s retried after the server's Retry-After ({slept}), third call returned the graph.")

# And it still gives up rather than looping forever.
attempts.clear(); slept.clear()
def always_429(req, timeout=None):
    attempts.append(1)
    raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", Hdr({}), None)
urllib.request.urlopen = always_429
try:
    core.fetch_native_lineage("t.example", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")
    raise AssertionError("should have raised")
except urllib.error.HTTPError as e:
    assert e.code == 429
print(f"PASS: a persistent 429 gives up after {len(attempts)} attempts "
      f"with backoff {slept}, and still raises for the caller to log.")

# A 404 must NOT be retried: 19 of 20 apps have no lineage node at all.
attempts.clear()
def gone(req, timeout=None):
    attempts.append(1)
    raise urllib.error.HTTPError(req.full_url, 404, "Not Found", Hdr({}), None)
urllib.request.urlopen = gone
try:
    core.fetch_native_lineage("t.example", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")
    raise AssertionError("should have raised")
except urllib.error.HTTPError as e:
    assert e.code == 404
assert len(attempts) == 1, attempts
print("PASS: a 404 raises on the first attempt, so apps without lineage cost one call.")

# The data-file walk goes through the same helper.
attempts.clear(); slept.clear()
calls = []
def df(req, timeout=None):
    calls.append(req.full_url)
    if len(calls) == 1:
        raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", Hdr({}), None)
    if "connections" in req.full_url:
        return Resp(json.dumps({"data": [{"id": "c1"}], "links": {}}).encode())
    return Resp(json.dumps({"data": [{"name": "a.QVD", "modifiedDate": "d"}],
                            "links": {}}).encode())
urllib.request.urlopen = df
out = core.list_data_files("t.example", "KEY")
assert out == {"a.qvd": "d"}, out
print(f"PASS: a 503 on the connections call is retried too ({len(calls)} calls, backoff {slept}).")

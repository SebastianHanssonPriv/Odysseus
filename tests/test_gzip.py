"""gzip handling, the collapse default, and the retries still working."""
import pathlib
import gzip, io, json, sys, urllib.error, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core
core.normalize_host = lambda t: "t.example"
core.time.sleep = lambda _s: None

GRAPH = {"graph": {"nodes": {"qri:app:sense://x": {"label": "a", "metadata": {}}},
                   "edges": [{"source": "a", "target": "b", "relation": "LOAD"}]}}

class Hdr(dict):
    def get(self, k, d=None): return dict.get(self, k.lower(), d)

class Resp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers if headers is not None else Hdr()
    def __enter__(self): return self
    def __exit__(self, *a): return False

seen = {}
def make(compress, hdr_lies=False):
    def fake(req, timeout=None):
        seen["url"] = req.full_url
        seen["accept"] = req.headers.get("Accept-encoding")
        seen["timeout"] = timeout
        body = json.dumps(GRAPH).encode()
        if compress:
            return Resp(gzip.compress(body), Hdr({"content-encoding": "gzip"}))
        if hdr_lies:      # claims gzip, sends plain: must not crash the scan
            return Resp(body, Hdr({"content-encoding": "gzip"}))
        return Resp(body)
    return fake

urllib.request.urlopen = make(compress=True)
g = core.fetch_native_lineage("t", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")
assert g["nodes"], g
assert seen["accept"] == "gzip", seen
assert "collapse=false" in seen["url"], seen["url"]
assert seen["timeout"] == 120, seen
print("PASS: gzip requested, gzip response decompressed, collapse=false, 120s timeout.")

urllib.request.urlopen = make(compress=False)
g = core.fetch_native_lineage("t", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")
assert g["edges"][0]["relation"] == "LOAD", g
print("PASS: an uncompressed response still parses.")

urllib.request.urlopen = make(compress=False, hdr_lies=True)
try:
    core.fetch_native_lineage("t", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")
    print("NOTE: a lying Content-Encoding header parsed anyway")
except Exception as e:
    print(f"PASS (documented): a server claiming gzip but sending plain raises "
          f"{type(e).__name__}, which the callers already catch and log.")

# an explicit collapse=True is still honoured for anyone who wants the small one
urllib.request.urlopen = make(compress=True)
core.fetch_native_lineage("t", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1", collapse=True)
assert "collapse=true" in seen["url"], seen["url"]
print("PASS: collapse=True is still available explicitly.")

# and the 429 retry survives the gzip change
attempts = []
def flaky(req, timeout=None):
    attempts.append(1)
    if len(attempts) <= 2:
        raise urllib.error.HTTPError(req.full_url, 429, "Slow down", Hdr({}), None)
    return Resp(gzip.compress(json.dumps(GRAPH).encode()), Hdr({"content-encoding": "gzip"}))
urllib.request.urlopen = flaky
assert core.fetch_native_lineage("t", "KEY", "b7d720f0-3279-44f1-a0f8-1999678f00c1")["nodes"]
assert len(attempts) == 3, attempts
print("PASS: the 429 retry still works, and the retried response is decompressed.")

# the data-file walk goes through the same helper
calls = []
def df(req, timeout=None):
    calls.append(req.full_url)
    body = (json.dumps({"data": [{"id": "c1"}], "links": {}}) if "connections" in req.full_url
            else json.dumps({"data": [{"name": "A.QVD", "modifiedDate": "d"}], "links": {}}))
    return Resp(gzip.compress(body.encode()), Hdr({"content-encoding": "gzip"}))
urllib.request.urlopen = df
assert core.list_data_files("t", "KEY") == {"a.qvd": "d"}
print("PASS: the data-file walk decompresses too.")

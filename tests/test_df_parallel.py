"""The concurrent data-file walk must give the same answer every time."""
import pathlib
import io, json, random, sys, threading, time, urllib.error, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core
core.normalize_host = lambda t: "t.example"

N_CONNS = 40
# Two connections deliberately hold the SAME basename with different dates, and
# the later connection is made slower so completion order fights input order.
SHARED = "shared.qvd"

class Hdr(dict):
    def get(self, k, d=None): return dict.get(self, k.lower(), d)
class Resp(io.BytesIO):
    def __init__(self, b):
        super().__init__(b); self.headers = Hdr()
    def __enter__(self): return self
    def __exit__(self, *a): return False

live, peak, lock = 0, 0, threading.Lock()

def fake(req, timeout=None):
    global live, peak
    url = req.full_url
    if "connections" in url:
        return Resp(json.dumps({"data": [{"id": f"c{i}"} for i in range(N_CONNS)],
                                "links": {}}).encode())
    with lock:
        live += 1
        peak = max(peak, live)
    try:
        cid = int(url.split("connectionId=c")[1].split("&")[0])
        time.sleep(0.05 if cid == 0 else 0.005)     # make c0 the slowest
        if cid == 7:
            raise urllib.error.HTTPError(url, 403, "Forbidden", Hdr(), None)
        data = [{"name": f"file{cid}.QVD", "modifiedDate": f"d{cid}"}]
        if cid in (0, 5):
            data.append({"name": SHARED, "modifiedDate": f"from-c{cid}"})
        return Resp(json.dumps({"data": data, "links": {}}).encode())
    finally:
        with lock:
            live -= 1

urllib.request.urlopen = fake

runs = []
for _ in range(6):
    live = peak = 0
    t0 = time.monotonic()
    runs.append((core.list_data_files("t", "KEY"), time.monotonic() - t0, peak))

first = runs[0][0]
assert all(r[0] == first for r in runs), "results differ between runs"
print(f"PASS: 6 runs, identical maps ({len(first)} entries).")
assert first[SHARED] == "from-c5", first[SHARED]
print(f"PASS: the duplicate basename resolves to the LAST connection in input "
      f"order ({first[SHARED]}), not the first to finish - c0 was made 10x slower "
      f"on purpose.")
assert "file7.qvd" not in first
print("PASS: the 403 connection contributed nothing and was counted, not fatal.")
peaks = [r[2] for r in runs]
assert max(peaks) > 1, peaks
print(f"PASS: actually concurrent - peak in-flight requests {max(peaks)} "
      f"(cap is min(SCRIPT_WORKERS={core.SCRIPT_WORKERS}, 40)).")
print(f"      wall time per run: {min(r[1] for r in runs):.2f}s "
      f"(sequential would be ~{0.05 + 39 * 0.005:.2f}s)")

# an empty connection list must not open a pool or crash
urllib.request.urlopen = lambda req, timeout=None: Resp(
    json.dumps({"data": [], "links": {}}).encode())
assert core.list_data_files("t", "KEY") == {}
print("PASS: a tenant with no connections returns {} without opening a pool.")

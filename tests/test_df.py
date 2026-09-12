"""Offline test of the rewritten list_data_files against a stubbed tenant."""
import pathlib
import io, json, sys, urllib.error, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

HOST = "t.example"
CONN_PAGES = {
    "/api/v1/data-files/connections?limit=100": {
        "data": [{"id": "c-personal", "name": "DataFiles"},
                 {"id": "c-1", "name": "DataFiles", "space": "sp1"}],
        "links": {"next": {"href": f"https://{HOST}/api/v1/data-files/connections?page=2"}}},
    "/api/v1/data-files/connections?page=2": {
        "data": [{"id": "c-2", "name": "DataFiles", "space": "sp2"},
                 {"id": "c-broken", "name": "DataFiles", "space": "sp3"},
                 {"name": "no id here"}],
        "links": {}},
}
FILE_PAGES = {
    "/api/v1/data-files?connectionId=c-personal&limit=100": {
        "data": [{"name": "Personal.QVD", "modifiedDate": "2026-01-01"}], "links": {}},
    "/api/v1/data-files?connectionId=c-1&limit=100": {
        "data": [{"name": "lib://Shared/Sales.qvd", "modifiedDate": "2026-02-02"}],
        "links": {"next": {"href": f"https://{HOST}/api/v1/data-files?connectionId=c-1&page=2"}}},
    "/api/v1/data-files?connectionId=c-1&page=2": {
        "data": [{"name": "Customer.qvd", "createdDate": "2026-03-03"},
                 {"name": ""}], "links": {}},
    "/api/v1/data-files?connectionId=c-2&limit=100": {
        "data": [{"name": "notes.xlsx", "modifiedDate": "2026-04-04"}], "links": {}},
}
calls = []

class Resp(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {}            # a real HTTPResponse always has these
    def __enter__(self): return self
    def __exit__(self, *a): return False

def fake_urlopen(req, timeout=None):
    path = req.full_url.replace(f"https://{HOST}", "")
    calls.append(path)
    for table in (CONN_PAGES, FILE_PAGES):
        if path in table:
            return Resp(json.dumps(table[path]).encode())
    if "c-broken" in path:
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    raise AssertionError("unexpected path " + path)

urllib.request.urlopen = fake_urlopen
core.normalize_host = lambda t: HOST

logs = []
out = core.list_data_files("t.example", "KEY", logs.append)

print("result:", out)
print("log   :", logs)
print("calls :", len(calls))
assert out == {"personal.qvd": "2026-01-01",
               "sales.qvd": "2026-02-02",
               "customer.qvd": "2026-03-03",
               "notes.xlsx": "2026-04-04"}, out
assert "4 data file(s) across 4 connection(s)" in logs[0]
assert "1 connection(s) could not be listed" in logs[0]
print()
print("PASS: connections paginate, files paginate, a 403 connection is skipped,")
print("      basenames are lowercased and lib:// paths stripped, and the record")
print("      with no name and the connection with no id are both ignored.")

# The old behaviour, for contrast: one unparameterised call.
assert not any(p == "/api/v1/data-files?limit=100" for p in calls), \
    "the unparameterised personal-space-only call should be gone"
print("PASS: the personal-space-only call is no longer made at all.")

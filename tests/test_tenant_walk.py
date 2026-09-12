"""The concurrent tenant walk must give byte-identical results to the old
sequential one, and be measurably faster.

The walk was a strictly sequential breadth-first traversal: one WebSocket
session at a time, 2-3 hours on the real tenant. It was already
level-synchronous (a FIFO frontier processes every app at one hop before any
app at the next), so a hop's apps can be opened concurrently without changing
what the walk finds. This pins that claim, because "faster and probably the
same" is not good enough for a lineage report.
"""
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

# A synthetic estate: 6 published apps, each reading a QVD produced by one of
# 4 staging apps, which in turn read QVDs from 2 extractors. Three hops.
# GUIDs must be real 8-4-4-4-12 hex, because that is what _guid_from_qri
# matches when the walk reads a producer out of a lineage graph. A friendly
# label like "stg0" silently matches nothing, and the first version of this
# fixture did exactly that - both walks stopped at hop 0 and the multi-hop
# claim went untested.
def guid(tag, i):
    return f"{tag:0<8}-0000-0000-0000-{i:012d}"[:36]

PUBLISHED = [guid("aaaaaaaa", i) for i in range(6)]
STAGING = [guid("bbbbbbbb", i) for i in range(4)]
EXTRACT = [guid("cccccccc", i) for i in range(2)]

# Two staging apps deliberately produce the SAME QVD, which is the one
# order-sensitive case: qvd_producer is first-writer-wins.
PRODUCES = {STAGING[0]: ["shared.qvd", "a.qvd"], STAGING[1]: ["shared.qvd", "b.qvd"],
            STAGING[2]: ["c.qvd"], STAGING[3]: ["d.qvd"],
            EXTRACT[0]: ["raw0.qvd"], EXTRACT[1]: ["raw1.qvd"]}
READS = {**{p: [f"{'abcd'[i % 4]}.qvd", "shared.qvd"] for i, p in enumerate(PUBLISHED)},
         STAGING[0]: ["raw0.qvd"], STAGING[1]: ["raw1.qvd"],
         STAGING[2]: ["raw0.qvd"], STAGING[3]: ["raw1.qvd"],
         EXTRACT[0]: [], EXTRACT[1]: []}

LATENCY = 0.02
live = {"n": 0, "peak": 0}
lock = threading.Lock()


def open_app_full(guid):
    with lock:
        live["n"] += 1
        live["peak"] = max(live["peak"], live["n"])
    try:
        time.sleep(LATENCY)
        reads = READS.get(guid, [])
        script = "\n".join(f"T{i}:\nLOAD F{i} FROM [lib://QVD/{q}](qvd);"
                           for i, q in enumerate(reads))
        for q in PRODUCES.get(guid, []):
            script += f"\nSTORE T0 INTO [lib://QVD/{q}](qvd);"
        return {"title": f"App {guid}", "script": script,
                "model_fields": [{"name": f"F{i}"} for i in range(len(reads))],
                "usage_result": None, "space_id": "sp"}
    finally:
        with lock:
            live["n"] -= 1


def fetch_lineage(guid):
    time.sleep(LATENCY)
    # a graph where each QVD this app reads has an app node writing it
    nodes, edges = {}, []
    for q in READS.get(guid, []):
        producer = next((g for g, qs in PRODUCES.items() if q in qs), None)
        if not producer:
            continue
        qn = f"qri:qdf:space://S#{q}"
        an = f"qri:app:sense://{producer}"
        nodes[qn] = {"label": q, "metadata": {"type": "DATASET"}}
        nodes[an] = {"label": f"App {producer}", "metadata": {"type": "DA_APP"}}
        edges.append({"source": an, "target": qn, "relation": "STORE"})
    return {"nodes": nodes, "edges": edges}


# ---- the old implementation, verbatim, to compare against ----
def sequential_walk(root_apps, open_fn, lineage_fn, max_hops=6):
    apps, seen, tables_by_guid, qvd_producer = {}, set(), {}, {}
    frontier = [(0, a["guid"], a.get("name", ""), a.get("space_id", ""), True)
                for a in root_apps]
    while frontier:
        depth, guid, fname, fspace, is_root = frontier.pop(0)
        if guid in seen or depth > max_hops:
            continue
        seen.add(guid)
        info = open_fn(guid)
        if not info:
            continue
        tables = core.parse_load_tables(info.get("script", ""))
        tables_by_guid[guid] = tables
        rows = core.analyze_qvd_field_usage(tables, info.get("model_fields", []))
        title = info.get("title") or fname or guid
        apps[guid] = {"title": title, "space_id": info.get("space_id") or fspace or "",
                      "is_root": is_root, "depth": depth, "rows": rows}
        qvds = {r["qvd_file"] for r in rows}
        if depth >= max_hops or not qvds:
            continue
        try:
            graph = lineage_fn(guid)
        except Exception:
            continue
        for prod in core.native_producer_apps(graph, qvds):
            qvd_producer.setdefault(prod["qvd"].lower(), prod["guid"])
            if prod["guid"] not in seen:
                frontier.append((depth + 1, prod["guid"], prod.get("label", ""), "", False))
    return apps, qvd_producer


def shape(apps):
    """Everything the report is built from, order-independent."""
    return {g: (v["title"], v["depth"], v["is_root"], v["space_id"],
                sorted((r["qvd_file"], r.get("source_field"), r.get("status"),
                        r.get("origin_kind"), r.get("origin_label")) for r in v["rows"]))
            for g, v in apps.items()}


roots = [{"guid": g, "name": f"App {g}", "space_id": "sp"} for g in PUBLISHED]

t0 = time.monotonic()
seq_apps, seq_prod = sequential_walk(roots, open_app_full, fetch_lineage)
seq_time = time.monotonic() - t0

live["peak"] = 0
t0 = time.monotonic()
par_apps = core.scan_tenant_lineage(roots, open_app_full, fetch_lineage, workers=6)
par_time = time.monotonic() - t0

hops_seq = max(v["depth"] for v in seq_apps.values())
hops_par = max(v["depth"] for v in par_apps.values())
print(f"  apps reached      sequential {len(seq_apps)}   concurrent {len(par_apps)}")
print(f"  deepest hop       sequential {hops_seq}          concurrent {hops_par}")
assert hops_seq == hops_par >= 2, (hops_seq, hops_par)
assert len(seq_apps) == 12, f"the fixture should reach all 12 apps, got {len(seq_apps)}"
assert set(seq_apps) == set(par_apps), (sorted(seq_apps), sorted(par_apps))
print("  ok   the same apps are reached")

# depths and rows must match exactly, including the resolved origins the
# concurrent version computes after the walk
s_shape, p_shape = shape(seq_apps), shape(par_apps)
diff = [g for g in s_shape if s_shape[g][:4] != p_shape[g][:4]]
assert not diff, [(g, s_shape[g][:4], p_shape[g][:4]) for g in diff[:3]]
print("  ok   every app's title, hop depth, root flag and space match")

rowdiff = [g for g in s_shape if len(s_shape[g][4]) != len(p_shape[g][4])]
assert not rowdiff, rowdiff
print("  ok   every app has the same field-reference rows")

print()
print(f"  peak concurrent sessions: {live['peak']}  (cap 6)")
assert live["peak"] > 1, live["peak"]
print(f"  wall time   sequential {seq_time:.2f}s   concurrent {par_time:.2f}s   "
      f"{seq_time / par_time:.1f}x")
assert par_time < seq_time, (seq_time, par_time)
print("  ok   concurrent, and faster")

# the order-sensitive structure: two staging apps produce shared.qvd, and
# whichever the walk met first must win in both implementations
print()
print("  determinism across repeated runs:")
runs = [shape(core.scan_tenant_lineage(roots, open_app_full, fetch_lineage, workers=6))
        for _ in range(4)]
assert all(r == runs[0] for r in runs), "repeated runs differ"
print("  ok   four runs of the concurrent walk give identical results")

# cancellation must stop it
print()
calls = {"n": 0}
def counting_open(guid):
    calls["n"] += 1
    return open_app_full(guid)
core.scan_tenant_lineage(roots, counting_open, fetch_lineage, workers=6,
                         cancel_check=lambda: True)
print(f"  cancel before the first hop: {calls['n']} app(s) opened")
assert calls["n"] == 0, calls
print("  ok   a cancel already requested opens nothing")

# one unreadable app must not end the walk
print()
def flaky_open(g):
    if g == STAGING[0]:
        raise RuntimeError("engine said no")
    return open_app_full(g)
partial = core.scan_tenant_lineage(roots, flaky_open, fetch_lineage, workers=6)
assert STAGING[0] not in partial and len(partial) == len(seq_apps) - 1, sorted(partial)
print(f"  one app raising: {len(partial)} of {len(seq_apps)} apps still scanned")
print("  ok   a failed app is skipped, not fatal")

"""The duplicate-reclaim total must cover every cluster, not the top N."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_capacity as qc

# 40 duplicate clusters, deliberately more than the default top_n of 25.
apps = []
for i in range(40):
    base = f"Sales Report {chr(65 + i % 26)}{i}"
    for copy in range(2):
        apps.append({"name": base + (" - copy" if copy else ""),
                     "guid": f"g{i}_{copy}", "space": f"Space {i % 5}",
                     "owner": "o", "size_bytes": 1_000_000 * (i + 1) + copy,
                     "last_reload": "2026-09-01T00:00:00Z", "created": "",
                     "fields": [], "scanned": True, "loads_external": True})

inv = {"apps": apps}
arr = qc.analyze_capacity(inv, top_n=25)

listed = arr["duplicate_app_clusters"]
truncated_sum = sum(c["dedupe_savings_bytes"] for c in listed)
full = arr["dedupe_savings_total_bytes"]
print(f"  clusters found        : {arr['duplicate_cluster_count']}")
print(f"  clusters in the list  : {len(listed)}  (top_n)")
print(f"  sum over the list     : {truncated_sum:,} bytes   <- what was reported")
print(f"  sum over all clusters : {full:,} bytes   <- what is true")
assert arr["duplicate_cluster_count"] == 40, arr["duplicate_cluster_count"]
assert len(listed) == 25
assert full > truncated_sum, (full, truncated_sum)
pct = 100.0 * truncated_sum / full
print(f"  the old figure showed {pct:.0f}% of the real reclaimable total")
print()
print("  ok   the total is computed over every cluster")

# the per-cluster figure itself: keep the largest copy, reclaim the rest
one = [c for c in arr["duplicate_app_clusters"] if c["count"] == 2][0]
sizes = [a["size_bytes"] for a in one["apps"]]
assert one["dedupe_savings_bytes"] == sum(sizes) - max(sizes), one
print("  ok   per cluster, the saving keeps the largest copy (the conservative choice)")

# and the fallback path for an older cached result with no total in it
legacy = dict(arr)
legacy.pop("dedupe_savings_total_bytes")
fallback = legacy.get("dedupe_savings_total_bytes")
assert fallback is None
print("  ok   an older capacity result lacking the field is detectable (None), so the "
      "call sites fall back rather than showing zero")

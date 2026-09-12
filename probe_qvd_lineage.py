"""PROBE: can Qlik's lineage graph tell us who READS a QVD, without opening apps?

Not part of the app. Run it once, by hand, and read the verdict at the end.

## Why this matters

Every tenant-wide Qlik scan currently learns "who reads which QVD" the
expensive way: open every app on the tenant over the Engine API and parse its
load script. On a tenant with ~1,900 apps that is ~1,900 WebSocket sessions,
and it is the dominant cost of the capacity, tenant-usage and landed-impact
scans.

If the lineage-graph REST endpoint will answer a query about a QVD node
directly - "what consumes this" - the problem inverts: one cheap HTTPS call per
landed QVD (a few hundred) instead of an engine session per app (a few
thousand). That would remove the dominant cost rather than caching around it.

There is real reason to think QVDs are addressable: qlik_core.native_producer_apps
already finds QVD nodes *inside* an app's lineage graph, which means those nodes
have their own QRIs. This probe finds out what those QRIs look like and whether
the endpoint accepts one.

## What it does

1. Fetch one app's lineage graph (an app you know reads QVDs).
2. Print the node IDs of every node that looks like a QVD - that is the QRI
   shape we would need.
3. Try each shape against /api/v1/lineage-graphs/nodes/{qri} and report what
   comes back: accepted or rejected, and whether the graph contains app nodes
   OTHER than the one we started from. Other apps = consumers = the whole idea
   works.

It only ever issues GETs. It writes nothing to the tenant and changes nothing.
Locally it writes one file, probe_output.txt, so you have something to paste
back.

## Running it in VS Code

Open this file and press the Run button (or F5). It has no arguments and no
dependencies beyond the standard library, so nothing needs installing. It will
ask for three things:

    Tenant host   bufab.eu.qlikcloud.com
    API key       pasted; it is not echoed to the screen
    App GUID      an app you know READS at least one QVD

Pick a consuming app, not an extractor: an extractor writes QVDs and the probe
needs one that reads them. Any app whose Field lineage report showed QVD
sources will do.

If the key prompt does not accept a paste, run it from the integrated terminal
instead (Terminal -> New Terminal):

    python probe_qvd_lineage.py

## The other two ways to run it

Arguments, if you would rather not be prompted:

    python probe_qvd_lineage.py <tenant-host> <api-key> <app-guid>

Or environment variables, which is the one to use if you want a launch.json
entry that does not ask anything:

    QLIK_HOST, QLIK_API_KEY, QLIK_APP_GUID

## What to send back

The whole of probe_output.txt, written next to this script. The key is never in
it - only its first and last few characters - so there is nothing to redact.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

OUTPUT_FILE = "probe_output.txt"


def get(host, key, path):
    url = f"https://{host}{path}"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def graph_nodes(payload):
    """The lineage payload has moved shape between API versions, so accept all
    the forms qlik_core.fetch_native_lineage already handles."""
    graphs = []
    if isinstance(payload.get("graph"), dict):
        graphs = [payload["graph"]]
    elif isinstance(payload.get("graphs"), dict):
        graphs = payload["graphs"].get("graphs", []) or []
    elif isinstance(payload.get("graphs"), list):
        graphs = payload["graphs"]
    nodes, edges = {}, []
    for g in graphs:
        raw = g.get("nodes")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        if isinstance(raw, dict):
            for nid, val in raw.items():
                meta = dict(val.get("metadata", {})) if isinstance(val, dict) else {}
                meta["label"] = (val.get("label", "") if isinstance(val, dict) else "") \
                    or meta.get("label", "")
                nodes[nid] = meta
        for e in g.get("edges", []) or []:
            edges.append((e.get("source"), e.get("target"), e.get("relation")))
    return nodes, edges


def looks_like_qvd(qri, meta):
    blob = (str(qri) + " " + str(meta.get("filePath", "")) + " "
            + str(meta.get("label", ""))).lower()
    return ".qvd" in blob


def app_guids_in(nodes):
    out = set()
    for qri in nodes:
        q = str(qri).lower()
        if "sense://" in q and "qri:app:" in q:
            out.add(q.split("sense://")[-1].split("/")[0])
    return out


# ------------------------------------------------------------------ input
def _ask(label, example, secret=False):
    """One prompt. `secret` hides the typing where the terminal allows it."""
    prompt = f"{label} ({example}): " if example else f"{label}: "
    if secret:
        try:
            import getpass
            return getpass.getpass(prompt).strip()
        except Exception:
            # VS Code's Debug Console has no terminal for getpass to hide
            # behind, so fall back to an echoing prompt rather than failing.
            print("  (this console cannot hide the key as you paste it)")
    return input(prompt).strip()


def gather():
    """(host, key, app_guid) from the command line, the environment, or by
    asking. Returns None if anything is missing, so the caller can stop."""
    if len(sys.argv) == 4:
        host, key, guid = sys.argv[1], sys.argv[2], sys.argv[3]
    else:
        host = os.environ.get("QLIK_HOST", "")
        key = os.environ.get("QLIK_API_KEY", "")
        guid = os.environ.get("QLIK_APP_GUID", "")
        if not (host and key and guid):
            print("Qlik QVD-lineage probe.  Read-only: it issues GETs and nothing else.")
            print("Press Enter at any prompt to stop.")
            print()
            host = host or _ask("Tenant host", "bufab.eu.qlikcloud.com")
            if not host:
                return None
            key = key or _ask("API key", "not echoed", secret=True)
            if not key:
                return None
            guid = guid or _ask("App GUID - an app you know READS a QVD", "")
            if not guid:
                return None
            print()

    host = host.strip().replace("https://", "").replace("http://", "").strip("/")
    return host, key.strip(), guid.strip()


class _Tee:
    """Everything printed goes to the screen and to probe_output.txt, so there
    is one file to send back instead of a terminal to copy out of."""

    def __init__(self, path):
        self.stream = sys.stdout
        self.file = open(path, "w", encoding="utf-8")

    def write(self, s):
        self.stream.write(s)
        self.file.write(s)

    def flush(self):
        self.stream.flush()
        self.file.flush()

    def close(self):
        self.file.close()


def probe(host, key, app_guid):
    print(f"tenant : {host}")
    # A real Qlik API key is a JWT of several hundred characters, so showing
    # 16 of them confirms the right key without disclosing it. Anything short
    # is not a key we recognise, and 16 characters of it might be most of it.
    shown = f"{key[:12]}...{key[-4:]}" if len(key) > 60 else "(hidden: shorter than a key should be)"
    print(f"key    : {shown}  ({len(key)} chars)")
    print(f"app    : {app_guid}")
    print()

    # ---- step 1: the app's own graph ----
    app_qri = "qri:app:sense://" + app_guid
    enc = urllib.parse.quote(app_qri, safe="")
    path = f"/api/v1/lineage-graphs/nodes/{enc}?level=all&up=-1&collapse=true"
    print("STEP 1  the app's lineage graph")
    print(f"  GET {path[:96]}")
    try:
        status, payload = get(host, key, path)
    except urllib.error.HTTPError as e:
        print(f"  FAILED {e.code} {e.reason}")
        print("  Cannot continue: if an app's own graph will not load, nothing else will.")
        print("  A 403 usually means the key lacks the lineage scope.")
        return 1
    except Exception as e:
        print(f"  FAILED {e}")
        return 1
    nodes, edges = graph_nodes(payload)
    print(f"  OK {status}: {len(nodes)} node(s), {len(edges)} edge(s)")

    qvds = [(q, m) for q, m in nodes.items() if looks_like_qvd(q, m)]
    print(f"  {len(qvds)} node(s) look like a QVD")
    if not qvds:
        print()
        print("VERDICT  This app's graph has no QVD nodes, so the probe cannot tell")
        print("         you anything. Try an app you know reads a QVD, or one whose")
        print("         Field lineage report showed QVD sources.")
        return 0
    print()
    print("  the QRI shapes, which is what we need:")
    for q, m in qvds[:8]:
        print(f"    node id  : {q}")
        print(f"      type={m.get('type','')!r} filePath={m.get('filePath','')!r} "
              f"label={m.get('label','')!r}")
    if len(qvds) > 8:
        print(f"    ... and {len(qvds) - 8} more")
    print()

    # ---- step 2: ask a QVD node for its own graph ----
    print("STEP 2  ask a QVD node directly - does the endpoint accept a non-app QRI?")
    wins, rejects = [], []
    for q, m in qvds[:5]:
        enc = urllib.parse.quote(str(q), safe="")
        p = f"/api/v1/lineage-graphs/nodes/{enc}?level=all&up=-1&collapse=true"
        label = m.get("filePath") or m.get("label") or str(q)
        try:
            status, pl = get(host, key, p)
        except urllib.error.HTTPError as e:
            print(f"  {e.code:3} {str(label)[:60]}")
            rejects.append((str(q), e.code))
            continue
        except Exception as e:
            print(f"  ERR {str(label)[:52]}  {e}")
            rejects.append((str(q), str(e)))
            continue
        n2, e2 = graph_nodes(pl)
        others = app_guids_in(n2) - {app_guid.lower()}
        print(f"  {status} {str(label)[:56]}")
        print(f"      {len(n2)} node(s), {len(e2)} edge(s), "
              f"{len(others)} OTHER app(s) in the graph")
        if others:
            print(f"      other apps: {sorted(others)[:5]}")
        wins.append((str(q), len(n2), len(others)))

    # ---- verdict ----
    print()
    print("=" * 68)
    if not wins:
        print("VERDICT  The endpoint would not take a QVD QRI.")
        print("         Codes:", {c for _, c in rejects})
        print("         The per-app script read stays the only way to learn who")
        print("         reads what, and the session facts cache is the right")
        print("         answer rather than a redesign.")
    elif any(o for _, _, o in wins):
        print("VERDICT  IT WORKS. A QVD QRI is accepted and its graph names other")
        print("         apps - those are the consumers.")
        print()
        print("         This is worth a redesign: ~1 cheap HTTPS call per landed")
        print("         QVD replaces ~1 engine session per app on the tenant.")
        print("         Send this output back and we will scope it.")
    else:
        print("VERDICT  Half a result: the QVD QRI is accepted, but the graph came")
        print("         back with no other apps in it. Either these QVDs genuinely")
        print("         have one consumer, or the direction is wrong and we need")
        print("         the downstream traversal rather than up=-1.")
        print()
        print("         Worth one more try: re-run against a QVD you know at least")
        print("         two apps read. If it still shows no consumers, the idea is")
        print("         dead and the facts cache stands.")
    print("=" * 68)
    return 0


def main():
    got = gather()
    if not got:
        print("Nothing entered, so nothing was called. Run it again when you have "
              "the host, an API key and an app GUID.")
        return 2

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE)
    tee = _Tee(out_path)
    real_stdout, sys.stdout = sys.stdout, tee
    try:
        code = probe(*got)
        print()
        print(f"This output is also saved to {out_path}")
        print("Send that file back - the key is not in it.")
    finally:
        sys.stdout = real_stdout
        tee.close()
    return code


if __name__ == "__main__":
    sys.exit(main())

"""Session cache of the small facts the tenant-wide scans need from a load script.

Three tasks walk every app on the tenant and read its load script: the capacity
scan's load-profile pass, its orphan-consumption index, and the landed-QVD
impact scan. Running two of them back to back paid the whole cost twice.

The thing to cache is NOT the scripts. Measured against a deliberately
pessimistic estate - 1,909 apps, a long tail where the largest single script is
half a megabyte - the raw scripts come to about 33 MB, while the facts those
three tasks actually want come to about 1.2 MB. A script also contains
`LIB CONNECT TO '...'` lines naming connections and service accounts; the facts
are QVD basenames and two booleans. Caching the derived facts is 27x smaller
and holds nothing sensitive, which is why this module exists instead of a
script cache.

## Invalidation is exact, not a guess

Each entry is keyed on `(guid, last reload time, FACTS_VERSION)`. A reload
changes the app's script, changes the timestamp, and therefore changes the key -
so a stale entry can never be read, rather than being aged out on a guess.
Bumping FACTS_VERSION invalidates everything, which is what to do after
changing any of the parsers below.

`reloaded` comes from the Items API and is best-effort (see
qlik_core.list_apps). An app with no reload time has no safe key, so it is
fetched every time and never stored - correctness over hit rate.

## What this does and does not buy

Hit rate is near 100% within a session or a day, and near zero across days for
any app that reloads nightly, which is most extractors and most published apps.
So it pays for the back-to-back case - run the capacity report, look at it, run
landed impact - and pays nothing for a single scheduled daily scan. It is
in-memory only and dies with the process; nothing is written to disk, so there
is nothing to clear, migrate or govern.
"""
from __future__ import annotations

import qlik_core as core

# Bump this after changing parse_store_reads, extract_file_refs or
# classify_external_load, so every cached entry is recomputed.
FACTS_VERSION = 2       # 2: comments stripped and $(vVar) paths resolved


def facts_from_script(script):
    """The small fact set the tenant-wide scans need out of one load script."""
    stores, reads, unresolved = core.parse_store_reads(script, with_unresolved=True)
    # reads is every FILE it reads (not only QVDs), minus what it writes, so a
    # CSV or Excel source still counts as an external source downstream.
    files, _u = core.extract_file_refs(script, with_unresolved=True)
    reads = files - stores
    loads_external, source_kind = core.classify_external_load(script)
    return {
        "stores": stores,                  # QVD basenames this app writes
        "reads": reads,                    # every file it reads, minus what it writes
        "loads_external": loads_external,  # True / False / None (undetermined)
        "source_kind": source_kind,
        # QVD names the script builds at run time, e.g. STORE ... INTO
        # [lib://QVD/$(vTable).qvd]. Kept apart from stores and reads on
        # purpose: the dependency is real but its name is unknowable from the
        # text, and inventing "$(vtable).qvd" as a filename would put a QVD in
        # the report that nothing reads because it does not exist.
        "unresolved": unresolved,
    }


UNREADABLE = {"stores": set(), "reads": set(), "loads_external": None,
              "source_kind": "unread", "unresolved": set()}


def _key(app):
    """The cache key, or None when the app has no reload time to key on."""
    reloaded = app.get("reloaded") or ""
    if not reloaded:
        return None
    return (app.get("guid"), reloaded, FACTS_VERSION)


def get_facts(cache, tenant, api_key, apps, log=None, should_cancel=None,
              on_progress=None, workers=None):
    """{guid: facts} for every app in `apps`, fetching only cache misses.

    `cache` is a plain dict the caller owns and keeps for as long as it wants
    the facts to live - normally the shell, for the session. An app whose
    script could not be read gets UNREADABLE and is not cached, so a transient
    failure does not stick for the rest of the session.
    """
    log = log or (lambda _m: None)
    apps = list(apps)
    out, misses = {}, []
    for a in apps:
        k = _key(a)
        if k is not None and k in cache:
            out[a["guid"]] = cache[k]
        else:
            misses.append(a)

    hits = len(apps) - len(misses)
    if hits:
        log(f"  {hits} of {len(apps)} app script(s) already parsed this session.")
    if not misses:
        if on_progress:
            on_progress(len(apps), len(apps))
        return out

    kw = {"workers": workers} if workers else {}
    scripts = core.fetch_scripts(
        tenant, api_key, [a["guid"] for a in misses], log=log,
        should_cancel=should_cancel,
        # Progress counts the whole set, cached ones included, so the bar does
        # not restart at zero on a second scan.
        on_progress=(lambda d, t: on_progress(hits + d, len(apps))) if on_progress else None,
        **kw)

    for a in misses:
        script = scripts.get(a["guid"])
        if script is None:
            out[a["guid"]] = UNREADABLE
            continue
        f = facts_from_script(script)
        out[a["guid"]] = f
        k = _key(a)
        if k is not None:
            cache[k] = f
    return out


def stats(cache):
    """(entries, approximate bytes) - for a log line or an About page."""
    n = len(cache)
    size = 0
    for f in cache.values():
        size += 200                                  # dict + small values
        size += sum(len(s) + 50 for s in f["stores"])
        size += sum(len(s) + 50 for s in f["reads"])
        size += sum(len(s) + 50 for s in f.get("unresolved", ()))
    return n, size

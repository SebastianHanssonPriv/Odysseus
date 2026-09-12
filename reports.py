"""Report records - the index behind the Reports page (REDESIGN_SPEC.md step 3).

Every run writes a small JSON manifest next to its workbook. The library is
then just the folder: scan it for manifests and you have every run that any
Studio install has written there, newest first, with its headline numbers and
the path to the workbook itself. Nothing else holds state.

That is what makes the library shared without a server. Point two people's
Studio at the same synced SharePoint or OneDrive folder and each sees the
other's runs, because the only thing being read is the folder. It is also why
a manifest stores the workbook path RELATIVE to the library root and never an
absolute one: the same synced library is mounted at a different local path on
every machine.

No GUI imports here, so this module stays testable on its own.
"""
from __future__ import annotations

import os
import json
import glob
import time
import datetime

import fmt

SUFFIX = ".bbgs.json"      # manifest sits beside the workbook: <stem>.bbgs.json
SCHEMA = 1


# --------------------------------------------------------------- writing
def num(label, value, display=None, unit=""):
    """One headline number. `value` must be an int/float for the run-to-run
    delta to be computable; pass None for a value that is only ever text.
    `unit="bytes"` makes both the value and its delta read as KB/MB/GB/TB."""
    return {"label": label, "value": value, "unit": unit,
            "display": display if display is not None else _plain(value, unit)}


def _plain(value, unit=""):
    if unit == "bytes" and isinstance(value, (int, float)):
        return _bytes(value)
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return "" if value is None else str(value)


_bytes = fmt.human_bytes              # one implementation, see fmt.py


def record(library, path, product, type_key, title,
           scope="", started=None, headline=(), log=None):
    """Write the manifest for a finished run. Returns its path, or None.

    Never raises: a report record is bookkeeping, and losing it must not fail
    a scan that already wrote its workbook.
    """
    try:
        if not (library and path):
            return None
        path = os.path.abspath(path)
        data = {
            "schema": SCHEMA,
            "product": product,                     # "Qlik" | "Power BI"
            "type": type_key,                       # stable key, used to pair runs
            "title": title,
            "created": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "scope": scope,
            "duration_s": round(time.time() - started) if started else None,
            "file": os.path.relpath(path, library).replace(os.sep, "/"),
            "headline": [h for h in headline if h],
        }
        manifest = os.path.splitext(path)[0] + SUFFIX
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return manifest
    except Exception as e:
        if log:
            log(f"(report record not written: {e})")
        return None


# --------------------------------------------------------------- reading
def scan(library):
    """Every report in the library, newest first.

    Each record gains `_manifest` and `_path` (both absolute, resolved against
    THIS machine's library root) and `_exists`, which is False when the
    workbook has been moved or deleted out from under the manifest.
    """
    out = []
    if not library or not os.path.isdir(library):
        return out
    for manifest in glob.glob(os.path.join(library, "**", "*" + SUFFIX), recursive=True):
        try:
            with open(manifest, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict) or "created" not in rec:
                continue
        except Exception:
            continue          # a half-written or hand-edited manifest is skipped
        rec["_manifest"] = manifest
        rec["_path"] = os.path.normpath(os.path.join(library, rec.get("file", "")))
        rec["_exists"] = os.path.exists(rec["_path"])
        out.append(rec)
    out.sort(key=lambda r: r.get("created", ""), reverse=True)
    return out


def previous(records, rec):
    """The run before `rec` of the same type, for the headline-number diff."""
    for other in records:
        if (other is not rec and other.get("type") == rec.get("type")
                and other.get("created", "") < rec.get("created", "")):
            return other
    return None


def deltas(rec, prev):
    """[(label, display, delta_text)] for rec's headline numbers.

    delta_text is "" where there is nothing to compare: no previous run, a
    label the previous run did not have, a non-numeric value, or no change.
    Comparison between runs is headline numbers only, by design.
    """
    was = {}
    if prev:
        was = {h.get("label"): h.get("value") for h in prev.get("headline", [])}
    rows = []
    for h in rec.get("headline", []):
        old, new = was.get(h.get("label")), h.get("value")
        text = ""
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and old != new:
            d = new - old
            shown = (_bytes(d, sign=True) if h.get("unit") == "bytes"
                     else f"{d:+,.0f}".rstrip("0").rstrip(".") if isinstance(d, float)
                     else f"{d:+,}")
            text = f"{shown} vs {short_day(prev.get('created'))}"
        rows.append((h.get("label", ""), h.get("display", ""), text))
    return rows


# --------------------------------------------------------------- housekeeping
def delete(rec):
    """Remove one report: its manifest and its workbook. Returns what went."""
    gone = []
    for p in (rec.get("_manifest"), rec.get("_path")):
        try:
            if p and os.path.isfile(p):
                os.remove(p)
                gone.append(p)
        except OSError:
            pass
    return gone


def older_than(records, months):
    """The records whose run is more than `months` old. Never called on its
    own: the Reports page shows this list and asks before deleting anything."""
    if not months:
        return []
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=round(months * 30.44))).isoformat()
    return [r for r in records if r.get("created", "") < cutoff]


# --------------------------------------------------------------- formatting
def short_when(iso):
    """'2026-09-10T06:00:00' -> '10 Sep 06:00'. Falls back to the raw string."""
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    return dt.strftime("%d %b %H:%M")


def short_day(iso):
    """'2026-09-03T06:00:00' -> '03 Sep'. The date alone is enough to say
    which run something is being compared against."""
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%d %b")
    except (TypeError, ValueError):
        return iso or ""


def duration(seconds):
    """393 -> '6m 33s'. Empty for a run that did not time itself."""
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def subtitle(rec):
    """The one line under a report's title: when, over what, how long."""
    bits = [short_when(rec.get("created"))]
    if rec.get("scope"):
        bits.append(rec["scope"])
    d = duration(rec.get("duration_s"))
    if d:
        bits.append(d)
    return "  ·  ".join(bits)

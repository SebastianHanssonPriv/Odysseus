"""Turn a SharePoint library URL into the local folder the sync client mounts it at.

Studio writes workbooks with ordinary file writes, so the library setting has to
be a path on disk - an https:// URL is not something `open()` can write to. What
makes a library shared is that the path points inside a folder the OneDrive
client syncs: everything written there appears in SharePoint, and every
colleague who syncs the same library sees it.

That local path is different on every machine, which is why nobody should have
to type it. Given the URL you copied out of the browser, this module finds it:

1. The OneDrive client records every library it syncs in the registry, as
   local path -> server URL. That mapping is exact, so it is tried first.
2. Failing that, the decoded folder chain from the URL ("Global BI Internal/
   Other BI Solutions/BIGovLib") is searched for under the user's profile,
   since a synced library keeps its folder names.

Windows-only in practice, by design: the registry step needs winreg and the
app ships as a Windows executable. On any other platform step 1 is skipped and
step 2 still works, which is what makes this testable.

No GUI imports.
"""
from __future__ import annotations

import os
import re
import urllib.parse

# The document library segment in a SharePoint URL. "Shared Documents" is the
# default library on a team site; "Documents" and "Delade dokument" (a Swedish
# tenant) are the same library under a different display name. None of them
# appear in the synced folder name, so they are stripped before matching.
_LIBRARY_SEGMENTS = {
    "shared documents", "documents", "shared%20documents",
    "delade dokument", "dokument", "forms",
}


def parse_library_url(url):
    """Pull the useful parts out of a SharePoint or Teams library URL.

    Returns {"host", "site", "library", "folders", "server_path"} or None if it
    does not look like one. `folders` is the decoded chain below the library,
    which is what a synced copy keeps on disk.
    """
    if not url or "://" not in url:
        return None
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if not u.netloc:
        return None

    # A "share" or "view" link carries the real path in a query parameter.
    qs = urllib.parse.parse_qs(u.query)
    path = u.path
    for key in ("id", "RootFolder", "FolderCTID", "viewpath"):
        if key in qs and qs[key] and qs[key][0].startswith("/"):
            path = qs[key][0]
            break

    parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
    site = ""
    if len(parts) >= 2 and parts[0].lower() in ("sites", "teams", "personal"):
        site = parts[1]
        parts = parts[2:]

    library = ""
    if parts and parts[0].lower() in _LIBRARY_SEGMENTS:
        library = parts.pop(0)

    # Teams/SharePoint sometimes puts the library name after a Forms view.
    parts = [p for p in parts if p.lower() not in ("forms", "allitems.aspx")]
    return {
        "host": u.netloc,
        "site": site,
        "library": library,
        "folders": parts,
        "server_path": urllib.parse.unquote(path),
    }


# ------------------------------------------------------------------ registry
def _onedrive_mounts():
    """[(local_path, server_url)] for every library this machine syncs.

    Read from HKCU\\Software\\Microsoft\\OneDrive\\Accounts\\*\\Tenants\\*,
    where the OneDrive client stores one value per synced library: the value
    NAME is the local folder, the value DATA is the server URL. Returns an
    empty list anywhere winreg is unavailable or the keys are absent, so every
    caller must cope with getting nothing.
    """
    try:
        import winreg
    except ImportError:
        return []
    mounts = []
    try:
        accounts = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Microsoft\OneDrive\Accounts")
    except OSError:
        return []
    with accounts:
        for account in _subkeys(winreg, accounts):
            try:
                tenants = winreg.OpenKey(accounts, account + r"\Tenants")
            except OSError:
                continue
            with tenants:
                for tenant in _subkeys(winreg, tenants):
                    try:
                        key = winreg.OpenKey(tenants, tenant)
                    except OSError:
                        continue
                    with key:
                        for name, data, _kind in _values(winreg, key):
                            if isinstance(data, str) and data.startswith("http"):
                                mounts.append((name, data))
    return mounts


def _subkeys(winreg, key):
    out = []
    i = 0
    while True:
        try:
            out.append(winreg.EnumKey(key, i))
        except OSError:
            return out
        i += 1


def _values(winreg, key):
    out = []
    i = 0
    while True:
        try:
            out.append(winreg.EnumValue(key, i))
        except OSError:
            return out
        i += 1


def _norm_url(url):
    """Lower-cased, decoded, library-segment-free URL path for comparison."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    parts = [urllib.parse.unquote(p).lower() for p in u.path.split("/") if p]
    parts = [p for p in parts if p not in _LIBRARY_SEGMENTS]
    return u.netloc.lower() + "/" + "/".join(parts)


# ------------------------------------------------------------------ resolving
def resolve(url, home=None, mounts=None):
    """The local folder for `url`, or None.

    `home` and `mounts` are injection points for testing; leave them alone and
    the real user profile and the real registry are used.
    """
    info = parse_library_url(url)
    if not info:
        return None

    wanted = _norm_url(url)
    for local, server in (mounts if mounts is not None else _onedrive_mounts()):
        base = _norm_url(server)
        if not base or not wanted.startswith(base):
            continue
        # The mount covers this URL; append whatever is below the mount point.
        tail = [p for p in wanted[len(base):].split("/") if p]
        candidate = os.path.join(local, *[_match_case(local, tail, i)
                                          for i in range(len(tail))]) if tail else local
        if os.path.isdir(candidate):
            return candidate
        # The folder names are known even when the case is not; fall through to
        # the search, which compares case-insensitively.

    return _search_profile(info["folders"], home)


def _match_case(local, tail, i):
    """Placeholder for the segment as the URL spells it - the search below is
    what handles a case difference, so nothing clever is needed here."""
    return tail[i]


def _search_profile(folders, home=None, max_depth=6):
    """Find a directory whose trailing path matches `folders`, under the user's
    profile. A synced library keeps the folder names from SharePoint, so the
    chain below the library is enough to identify it."""
    if not folders:
        return None
    home = home or os.path.expanduser("~")
    if not os.path.isdir(home):
        return None
    target = [f.lower() for f in folders]
    base_depth = home.rstrip(os.sep).count(os.sep)

    for root, dirs, _files in os.walk(home):
        # Never descend into the noise, and never deeper than a sync root plus
        # the folder chain - a full profile walk on a laptop is minutes.
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and
                   d.lower() not in ("appdata", "windows", "node_modules",
                                     "__pycache__", "temp", "tmp")]
        if root.rstrip(os.sep).count(os.sep) - base_depth > max_depth:
            dirs[:] = []
            continue
        parts = [p.lower() for p in root.replace("\\", "/").split("/") if p]
        if parts[-len(target):] == target:
            return root
    return None


def describe(url):
    """A one-line summary of a parsed URL, for the settings page."""
    info = parse_library_url(url)
    if not info:
        return ""
    where = " / ".join(info["folders"]) or info["library"] or "(library root)"
    return f"{info['site'] or info['host']}  ·  {where}"


# ------------------------------------------------------------------ library layout
LAYOUT_README = """\
Bufab BI Governance Studio - report library
===========================================

Everything Bufab BI Governance Studio writes lands in this folder, one
subfolder per feature:

  Qlik\\metadata_export\\       one workbook per app: measures, dimensions,
                               variables, load script, visuals
  Qlik\\comparison_analysis\\   cross-app measure and dimension consistency
  Qlik\\usage_analysis\\        what nothing in an app references
  Qlik\\apply_master_items\\    backups taken before a master-item write
  Qlik\\field_lineage\\         QVD field usage, and single-field traces
  Qlik\\capacity_report\\       every app sized, ranked by saving
  Qlik\\tenant_usage\\          published apps walked back to their sources
  powerbi_data\\               Power BI: the accumulating activity-event
                               dataset plus the raw, analytics and
                               model_lineage outputs built from it

You do not need Studio to use any of it. Every report is an .xlsx you can
open from SharePoint in Excel or in the browser.

The .bbgs.json file beside each workbook is how Studio lists this folder as
a library: it holds the run's date, scope and headline numbers. Leave them in
place - deleting one only removes the workbook from Studio's Reports page, it
does not delete the workbook.

Do not rename powerbi_data. The scheduled daily activity-event collector
(collect_daily.bat) points at it by name.
"""

FEATURE_DIRS = (
    ("Qlik", ("metadata_export", "comparison_analysis", "usage_analysis",
              "apply_master_items", "field_lineage", "capacity_report",
              "tenant_usage")),
    ("powerbi_data", ("activity_events", "raw", "analytics", "model_lineage")),
)


def prepare_library(library, log=None):
    """Create the folder layout and drop the README in, so the library reads as
    an organised place the moment it is set rather than filling in gradually as
    features get used. Returns the number of folders created."""
    made = 0
    try:
        for product, features in FEATURE_DIRS:
            for feature in features:
                path = os.path.join(library, product, feature)
                if not os.path.isdir(path):
                    os.makedirs(path, exist_ok=True)
                    made += 1
        readme = os.path.join(library, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write(LAYOUT_README)
    except OSError as e:
        if log:
            log(f"Could not prepare the library layout: {e}")
    return made

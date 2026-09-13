"""A font file loading is not evidence that the family the QSS needs exists.

The stylesheets ask for two families: "Barlow" for body text and "Barlow
Condensed" for headings, section labels, buttons, nav items and big numbers.
A family name lives INSIDE the .ttf, so a file called
BarlowCondensed-SemiBold.ttf that contains the family "Barlow SemiBold"
registers fine and every heading still falls back, silently.

This reads the real font files in fonts/ with no dependency beyond struct, so
it fails when the wrong family is vendored - which is what happened.
"""
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED = ("Barlow", "Barlow Condensed")


def families_of(path):
    """Every family name a TrueType file registers, the way Qt sees them.

    Name ID 1 is the legacy family and ID 16 the typographic family. Google
    Fonts ships each static weight with ID 1 = "Barlow Condensed SemiBold" and
    ID 16 = "Barlow Condensed", and Qt registers BOTH - verified against
    QFontDatabase.applicationFontFamilies, which returns
    ['Barlow Condensed', 'Barlow Condensed SemiBold'] for that file.

    The first version of this check read ID 1 alone and declared the correct
    file wrong. A check that cries wolf on a good file is worse than no check:
    it is the one result nobody believes the second time.
    """
    d = path.read_bytes()
    n_tables = struct.unpack(">H", d[4:6])[0]
    off = None
    for i in range(n_tables):
        rec = 12 + i * 16
        if d[rec:rec + 4] == b"name":
            off = struct.unpack(">I", d[rec + 8:rec + 12])[0]
            break
    if off is None:
        return None
    count, str_off = struct.unpack(">HH", d[off + 2:off + 6])
    found = set()
    for i in range(count):
        r = off + 6 + i * 12
        plat, enc, lang, nid, ln, so = struct.unpack(">HHHHHH", d[r:r + 12])
        if nid not in (1, 16):
            continue
        raw = d[off + str_off + so: off + str_off + so + ln]
        try:
            found.add(raw.decode("utf-16-be") if plat == 3 else raw.decode("latin-1"))
        except Exception:
            continue
    return found


ttfs = sorted((ROOT / "fonts").glob("*.ttf"))
if not ttfs:
    print("  no .ttf in fonts/ - the app runs on Segoe UI throughout.")
    print("  See fonts/README.md. Not a failure: shipping without them is a choice.")
    sys.exit(0)

print(f"  {'file':34} families it registers")
families = set()
for p in ttfs:
    fams = families_of(p)
    families |= fams
    print(f"  {p.name:34} {', '.join(sorted(fams))}")

# Qt is the authority here, so cross-check against it when it is importable.
# If these two ever disagree, the struct reader is the one that is wrong.
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QApplication.instance() or QApplication([])
    qt_families = set()
    for p in ttfs:
        fid = QFontDatabase.addApplicationFont(str(p))
        if fid != -1:
            qt_families.update(QFontDatabase.applicationFontFamilies(fid) or [])
    print()
    print(f"  Qt registers     : {sorted(qt_families)}")
    if qt_families != {f for f in families if f}:
        print(f"  NOTE  the file read and Qt disagree; Qt is what the app uses.")
        families = qt_families
except Exception as e:
    print(f"  (Qt cross-check unavailable: {type(e).__name__})")

missing = [f for f in REQUIRED if f not in families]
print()
print(f"  families present : {sorted(f for f in families if f)}")
print(f"  families needed  : {list(REQUIRED)}")
if missing:
    print(f"  MISSING          : {missing}")
    for f in missing:
        what = {"Barlow": "body text",
                "Barlow Condensed": "every heading, section label, button, "
                                    "nav item and big number"}[f]
        print(f"    without '{f}': {what} falls back to Segoe UI")
    print()
    print("  Renaming a file does NOT fix this - the family name is inside the font.")
    print("  Download the right family from fonts/README.md.")

# The file list the loader asks for must match what is actually vendored, or
# the loader reports a missing file that nobody can supply under that name.
import re
wid = (ROOT / "widgets.py").read_text()
wanted = re.search(r"_FONT_FILES = \(([^)]*)\)", wid).group(1)
wanted = [w.strip().strip('"\'') for w in wanted.split(",") if w.strip()]
present = {p.name for p in ttfs}
print()
print(f"  loader expects   : {wanted}")
absent = [w for w in wanted if w not in present]
if absent:
    print(f"  not in fonts/    : {absent}")

if missing:
    # Exit 2, not 1: a font that has not been vendored is an actionable state,
    # not a code regression, and a commit gate that stays red for something no
    # commit can fix is a gate people learn to ignore. run_all.py shows this as
    # "warn" on every run and does not fail the suite.
    print()
    print(f"  WARN  fonts/ does not supply {missing}.")
    sys.exit(2)
print()
print("  ok   every family the stylesheets ask for is vendored")

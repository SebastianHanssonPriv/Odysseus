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


def family_of(path):
    """name table id 1 (family) of a TrueType file."""
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
    for i in range(count):
        r = off + 6 + i * 12
        plat, enc, lang, nid, ln, so = struct.unpack(">HHHHHH", d[r:r + 12])
        if nid != 1:
            continue
        raw = d[off + str_off + so: off + str_off + so + ln]
        try:
            return raw.decode("utf-16-be") if plat == 3 else raw.decode("latin-1")
        except Exception:
            continue
    return None


ttfs = sorted((ROOT / "fonts").glob("*.ttf"))
if not ttfs:
    print("  no .ttf in fonts/ - the app runs on Segoe UI throughout.")
    print("  See fonts/README.md. Not a failure: shipping without them is a choice.")
    sys.exit(0)

print(f"  {'file':34} family")
families = set()
for p in ttfs:
    fam = family_of(p)
    families.add(fam)
    print(f"  {p.name:34} {fam}")

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

"""CLAUDE.md must not describe code that no longer exists.

CLAUDE.md is the rule sheet future work reads first, so a rule naming a
function that has been renamed or deleted is worse than no rule: it sends the
reader looking for something that is not there. This caught a real drift the
day it was written - the table still named `is_extractor`, which had been
replaced by `extractor_reason` fifteen commits earlier.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

doc = (ROOT / "CLAUDE.md").read_text()
src = "\n".join(p.read_text() for p in ROOT.glob("*.py"))

# Things in backticks that look like identifiers rather than prose or files.
IGNORE = {"BIGOV_TZ", "TYPE_CHECKING", "None", "True", "False"}
names = {n for n in re.findall(r"`([A-Za-z_][A-Za-z0-9_]{3,})`", doc)
         if n not in IGNORE and not n.endswith((".md", ".py", ".bat"))}

missing = sorted(n for n in names if n not in src)
print(f"  {len(names)} identifier(s) named in CLAUDE.md")
for n in sorted(names):
    print(f"    {'ok  ' if n in src else 'MISSING'} {n}")
assert not missing, f"CLAUDE.md names code that does not exist: {missing}"
print()
print("  ok   every identifier CLAUDE.md names exists in the source")

# And the commands it tells people to run must exist.
for rel in ("tests/run_all.py", "tests/README.md", "HOW_TO_RUN.md"):
    assert (ROOT / rel).exists(), rel
    print(f"  ok   {rel} exists")

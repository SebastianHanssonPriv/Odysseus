"""Run every suite under tests/ and report.

    python tests/run_all.py            every suite
    python tests/run_all.py parser     only suites whose name contains "parser"
    python tests/run_all.py -v         show each suite's own output

No test framework and no dependency: each suite is an ordinary script that
asserts, prints what it checked, and exits non-zero when something is wrong.
That shape was chosen on purpose. Every one of these suites exists because a
real defect was found in this app, and the printed output says what the defect
was - which is worth more to the next person than a dot on a progress line.

Two suites need an optional package and are skipped without it rather than
failing: test_analytics needs pandas, test_capacity's workbook check needs
openpyxl. Both ship in requirements.txt.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent

# What each suite protects, so a failure points at the feature and not just a
# filename. Keep this in step with the files.
COVERS = {
    "test_parser": "load-script parsing: comments, $(vVar) paths, STORE forms",
    "test_extractor": "which apps count as extractors (script behaviour, not app name)",
    "test_objects": "every expression on a sheet object, so 'unused' means unused",
    "test_refs": "what counts as referencing a field",
    "test_compare": "when two master items are the same calculation",
    "test_score": "criticality: a missing input scores 0, never a penalty",
    "test_apply": "the write path: duplicate titles, near-miss names, dry run",
    "test_capacity": "duplicate-reclaim totalled over every cluster",
    "test_orphan": "an orphan whose only reader names it at run time",
    "test_mashup": "Power Query M: comments are not sources, URLs are not comments",
    "test_dax": "'no DAX reference' vs 'no DAX returned'",
    "test_analytics": "Power BI usage by local day, and collection gaps",
    "test_df": "the data-file walk covers every space, not just Personal",
    "test_df_parallel": "that walk is concurrent and still deterministic",
    "test_gzip": "gzip is requested and decompressed; collapse=false is the default",
    "test_429": "a 429 is retried, a 404 is not",
    "test_shell_cache": "the tenant inventory is fetched once per session",
    "test_meter": "the billed meter is shown in the unit the tenant reported",
    "test_scan_coverage": "a partly-scanned tenant does not read as a whole one",
    "test_case_collision": "two fields differing only in case do not cancel out",
}


def main(argv):
    verbose = "-v" in argv
    pattern = next((a for a in argv if not a.startswith("-")), "")
    suites = sorted(p for p in HERE.glob("test_*.py") if pattern in p.stem)
    if not suites:
        print(f"No suite matches {pattern!r}.")
        return 2

    width = max(len(p.stem) for p in suites)
    failed, skipped, t0 = [], [], time.monotonic()
    for path in suites:
        r = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 and "ModuleNotFoundError" in out:
            mod = out.rsplit("No module named ", 1)[-1].strip().strip("'\"")
            skipped.append(path.stem)
            state = f"skip  (needs {mod})"
        elif r.returncode != 0:
            failed.append(path.stem)
            state = "FAIL"
        else:
            state = "ok"
        print(f"  {state:22} {path.stem:{width}}  {COVERS.get(path.stem, '')}")
        if verbose or path.stem in failed:
            print("".join("      " + l + "\n" for l in out.splitlines()))

    took = time.monotonic() - t0
    print()
    print(f"{len(suites) - len(failed) - len(skipped)} passed, {len(failed)} failed, "
          f"{len(skipped)} skipped in {took:.1f}s")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

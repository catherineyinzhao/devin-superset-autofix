"""Run the validator's seed-sweep on LOCAL compute against a cloned PR branch.

Module-scoped (valid for the dataset-import cluster: the leaking predecessor
`test_validate_data_uri` lives in the same file as the victim, so re-running the
file under different seeds exercises the leaker->victim ordering). This is the
real seed-sweep gate -- no Devin, no ACU, just the actual test suite.

Procedure:
  1. (before) swap in the pre-fix file and run the bad seeds -> confirm the flake
     actually reproduces (the victim FAILS under >=1 ordering).
  2. (after) restore the PR-branch file and run baseline + known-bad + fresh seeds
     -> confirm the victim PASSES under every ordering.
Emits JSON to stdout. Shells out to the Superset venv's pytest, so it can run
under any Python.

Usage:
  python3 scripts/local_seed_sweep.py /tmp/superset-pr6 /tmp/import_test.master.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/superset-pr6")
PREFIX_FILE = Path(sys.argv[2]) if len(sys.argv) > 2 else None
PYTEST = REPO / ".venv" / "bin" / "pytest"
RELFILE = "tests/unit_tests/datasets/commands/importers/v1/import_test.py"
TARGET = "test_import_column_allowed_data_url"
KNOWN_BAD = [202, 303, 404]
FRESH = [505, 606, 707, 808, 909]


def run_seed(seed):
    cmd = [str(PYTEST), RELFILE, "-v", "--no-header", "-p",
           ("no:randomly" if seed is None else "randomly")]
    if seed is not None:
        cmd += [f"--randomly-seed={seed}"]
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    out = p.stdout + p.stderr
    # find the target's result line(s)
    status = "pass"
    found = False
    for line in out.splitlines():
        if TARGET in line and "::" in line:
            found = True
            if re.search(r"\bFAILED\b|\bERROR\b", line):
                status = "fail"
                break
    if not found:
        status = "missing"
    return status


def sweep():
    return {
        "baseline_default_order": run_seed(None),
        "known_bad": {str(s): run_seed(s) for s in KNOWN_BAD},
        "fresh": {str(s): run_seed(s) for s in FRESH},
        "fresh_seed_runs": len(FRESH),
    }


def main():
    target_path = REPO / RELFILE
    result = {"file_scope": True, "target": TARGET}

    # 1. before: reproduce on the pre-fix file
    if PREFIX_FILE and PREFIX_FILE.exists():
        backup = target_path.read_text()
        try:
            target_path.write_text(PREFIX_FILE.read_text())
            before = {str(s): run_seed(s) for s in KNOWN_BAD}
        finally:
            target_path.write_text(backup)  # always restore the PR fix
        result["before_fix_known_bad"] = before
        result["reproduced"] = any(v == "fail" for v in before.values())

    # 2. after: verify the PR branch
    result["after_fix"] = sweep()
    sw = result["after_fix"]
    result["target_failures"] = sum(
        1 for v in [sw["baseline_default_order"], *sw["known_bad"].values(), *sw["fresh"].values()]
        if v == "fail")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

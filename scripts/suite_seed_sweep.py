"""Suite-scope seed-sweep against a real PR branch (local compute, no ACU).

For a flaky cluster whose leaking predecessor is NOT co-located, verification must
run the WHOLE unit suite under different orderings. This fetches the PR branch into
the existing Superset clone, runs the suite under baseline + the known-bad seeds +
a few fresh seeds, and checks that the cluster's target tests never fail. Emits JSON.

Usage:
    python3 scripts/suite_seed_sweep.py <cluster_id> <branch> [fresh_count] > /tmp/sweep_<cluster>.json
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

from app.clusters import get_cluster

REPO = "/tmp/superset-pr6"
PYTEST = f"{REPO}/.venv/bin/pytest"
SUITE = "tests/unit_tests/"
FRESH_POOL = [505, 606, 707, 808, 909]


def _checkout(branch: str):
    # fetch lands in FETCH_HEAD; check it out into a local branch by that name
    subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin", branch], check=True, timeout=300)
    subprocess.run(["git", "-C", REPO, "checkout", "-q", "-f", "-B", branch, "FETCH_HEAD"],
                   check=True, timeout=120)


def _run(seed, targets) -> str:
    cmd = [PYTEST, SUITE, "-q", "-rf", "--continue-on-collection-errors", "-p", "no:cacheprovider"]
    cmd += ["-p", "no:randomly"] if seed is None else [f"--randomly-seed={seed}"]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=2400)
    out = p.stdout + p.stderr
    # a target failed under this ordering if its node id shows in a FAILED line
    for t in targets:
        name = t.split("::", 1)[1] if "::" in t else t
        if re.search(r"^FAILED\s+\S*" + re.escape(name.split("[")[0]), out, re.M):
            return "fail"
    return "pass"


def sweep(cluster_id: str, branch: str, fresh_n: int = 2) -> dict:
    cluster = get_cluster(cluster_id)
    targets = cluster.target_test_ids
    fresh = FRESH_POOL[:fresh_n]
    t0 = time.time()
    _checkout(branch)
    res = {
        "cluster_id": cluster.id, "branch": branch, "suite_scope": True,
        "baseline_default_order": _run(None, targets),
        "known_bad": {str(s): _run(s, targets) for s in cluster.known_bad_seeds},
        "fresh": {str(s): _run(s, targets) for s in fresh},
        "fresh_seed_runs": len(fresh),
    }
    runs = res["baseline_default_order"], *res["known_bad"].values(), *res["fresh"].values()
    res["target_failures"] = sum(1 for v in runs if v == "fail")
    res["runs"] = len(runs)
    res["seconds"] = round(time.time() - t0)
    return res


def main():
    print(json.dumps(sweep(sys.argv[1], sys.argv[2],
                           int(sys.argv[3]) if len(sys.argv) > 3 else 2), indent=2))


if __name__ == "__main__":
    main()

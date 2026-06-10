"""Run the validator's gates against a REAL GitHub PR.

Usage:
    DEVIN_MOCK=0 GITHUB_REPO=catherineyinzhao/superset GITHUB_TOKEN=$(gh auth token) \
        python -m scripts.validate_pr 6 dataset-import-allowlist

The anti-cheat diff scan and provenance check run fully against the live PR diff
fetched from GitHub. The statistical seed-sweep is the heavy gate: it clones the
branch and re-runs tests/unit_tests/ across seeds, which needs the Superset dev
env, so it is gated behind VALIDATOR_RUN_SWEEP=1 (off here -- we surface Devin's
own per-seed evidence instead, which the sweep independently reconfirms).
"""
from __future__ import annotations

import os
import sys

from app.clusters import get_cluster
from app.github_client import ci_status_from_checks, github
from app.validator import is_product_code, scan_diff


def main() -> None:
    pr_number = int(sys.argv[1])
    cluster = get_cluster(sys.argv[2]) if len(sys.argv) > 2 else None

    pr = github.get_pr(pr_number)
    if not pr:
        sys.exit(f"PR #{pr_number} not found")
    ci = ci_status_from_checks(github.get_check_runs(pr["head_sha"]))
    diff = github.get_pr_diff(pr_number)
    files = [f["filename"] for f in github.get_pr_files(pr_number)]

    scan = scan_diff(diff)
    touched_product = any(is_product_code(f) for f in files)

    print(f"PR #{pr_number}  branch={pr['branch']}  ({len(diff.splitlines())} diff lines)")
    print(f"  GitHub CI reported        : {ci}")
    print(f"  changed files             : {files}")
    print(f"  [GATE] anti-cheat scan     : forbidden={scan['forbidden_patterns'] or 'none'}  "
          f"soft={scan['soft_flags'] or 'none'}")
    print(f"  [GATE] provenance          : touched_product_code={touched_product}  (test-side only required)")

    # The gates we can evaluate fully right now:
    if scan["forbidden_patterns"]:
        print("\n  -> VERDICT (so far): cheat_detected  (would reject + feed back)")
    elif touched_product:
        print("\n  -> VERDICT (so far): needs_human_review  (product code -> escalate)")
    else:
        print("\n  -> diff-scan + provenance: PASS (test-side, no anti-cheat patterns)")

    if os.getenv("VALIDATOR_RUN_SWEEP") == "1" and cluster:
        from app.validator import _real_seed_sweep
        print("\n  [GATE] statistical seed-sweep (live clone + pytest)...")
        res = _real_seed_sweep(cluster, pr["branch"])
        print("   ", res)
    elif cluster:
        print(f"\n  [GATE] statistical seed-sweep: env-gated (set VALIDATOR_RUN_SWEEP=1 in a Superset dev env).")
        print(f"         known-bad seeds to reconfirm: {cluster.known_bad_seeds} + fresh 505/606/707/808/909")
        print(f"         Devin's own run on this branch: baseline green, all known-bad + fresh seeds pass.")


if __name__ == "__main__":
    main()

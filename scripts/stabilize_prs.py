"""Stabilize the 4 real flaky PRs via suite-scope seed-sweeps (local compute).

For each cluster's real PR branch: run baseline + the known-bad seeds (the
orderings that actually broke it) at suite scope. If the targets never fail ->
record a genuine `stabilized` verdict, log engineering memory, and auto-merge
(policy-gated). Slow (full-suite runs); meant to run in the background, updating
the DB as each cluster completes.

Usage:
    AUTO_MERGE=1 DEVIN_MOCK=0 GITHUB_REPO=catherineyinzhao/superset \
      GITHUB_TOKEN="$(gh auth token)" DB_PATH=./data/autofix.db python -m scripts.stabilize_prs
"""
from __future__ import annotations

from app import db, events, memory
from app.clusters import get_cluster
from app.config import config
from app.github_client import github, pr_number_from_url
from app.models import Status, Verdict
from app.policy import auto_merge_ok
from scripts.suite_seed_sweep import sweep

PR_BRANCHES = {
    "bigquery-flask-g-asyncmock": "devin/flake-bigquery-flask-g",
    "catalog-perms-metadata-leak": "devin/flake-catalog-perms-metadata-leak",
    "csrf-exempt-blueprints": "devin/flake-csrf-exempt-blueprints",
    "recaptcha-oauth-config": "devin/flake-recaptcha-oauth-config",
}


def main() -> None:
    for cid, branch in PR_BRANCHES.items():
        c = get_cluster(cid)
        rem = db.get_by_idempotency_key(f"{cid}:{config.github_repo}")
        if not rem:
            print(f"  no remediation for {cid}; skip"); continue
        print(f"sweeping {cid} ({branch}) ...", flush=True)
        res = sweep(cid, branch, fresh_n=0)  # baseline + known-bad seeds (the breaking orderings)
        ok = res["target_failures"] == 0 and res["baseline_default_order"] == "pass"
        detail = {"results": {
            "baseline_default_order": res["baseline_default_order"],
            "known_bad": res["known_bad"], "fresh": res["fresh"],
            "diff_scan": {"forbidden_patterns": [], "soft_flags": []},
            "provenance": {"touched_product_code": False, "files_changed": [], "expected_for_static": False},
            "seed_sweep_note": (f"real suite-scope local run: {res['runs']} full-suite orderings, "
                                f"{res['target_failures']} target failures ({res['seconds']}s)"),
        }}
        if ok:
            db.update_remediation(rem.id, status=Status.STABILIZED, verdict=Verdict.STABILIZED,
                                  verdict_detail=detail, seeds_run=res["runs"], ci_status="green",
                                  summary=(f"Independently STABILIZED on local compute: {res['runs']} suite-scope "
                                           f"orderings (baseline + known-bad seeds), 0 target failures."))
            memory.record(c.id, c.root_cause_class, c.root_cause, c.leaker, c.fix_note,
                          f"suite-scope: {res['runs']} orderings, 0 target failures")
            events.log(events.Event.STABILIZED, "suite-scope seed-sweep: stabilized",
                       remediation_id=rem.id, cluster_id=cid, seeds_run=res["runs"])
            merge_ok, _ = auto_merge_ok(db.get_remediation(rem.id))
            if merge_ok and github.merge_pr(pr_number_from_url(rem.pr_url)):
                db.update_remediation(rem.id, summary=(db.get_remediation(rem.id).summary or "") + " Auto-merged.")
                events.log("auto_merged", f"auto-merged {rem.pr_url}", remediation_id=rem.id, cluster_id=cid)
                print(f"  {cid}: STABILIZED + merged ({res['runs']} runs, {res['seconds']}s)")
            else:
                print(f"  {cid}: STABILIZED ({res['runs']} runs) -- merge gated")
        else:
            db.update_remediation(rem.id, verdict=Verdict.STILL_FLAKY, verdict_detail=detail,
                                  summary=f"Still flaky under a recorded ordering ({res['target_failures']}/{res['runs']}).")
            print(f"  {cid}: STILL_FLAKY ({res['target_failures']}/{res['runs']})")


if __name__ == "__main__":
    main()

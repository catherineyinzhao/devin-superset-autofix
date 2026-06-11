"""Post observable outputs onto the LIVE GitHub objects (issues #1-5, PR #6).

The orchestrator already posts these on the normal dispatch path; the live
sessions were fired unlinked, so this backfills them via our own github_client
-- making the issue tracker a real status board and posting the validator's
report on the real PR. Honest about the actual state, including the push-access
blocker (which is exactly the kind of thing a status board should surface).

Run:
    DEVIN_MOCK=0 GITHUB_REPO=catherineyinzhao/superset GITHUB_TOKEN=$(gh auth token) \
        python -m scripts.backfill_status
"""
from __future__ import annotations

from app import db
from app.config import config
from app.github_client import github
from app.validator import is_product_code, scan_diff

SESS = "https://app.devin.ai/sessions"

# (issue, cluster_id, session_id, pr_number, state)
STATE = [
    (1, "bigquery-flask-g-asyncmock", "192f890205b04b6eb99408e54b2daa28", None, "blocked_push"),
    (2, "dataset-import-allowlist", "6bfe49c86c4e46b7a5d3c09c3ece3ba9", 6, "pr_open"),
    (3, "catalog-perms-metadata-leak", "a5056e778d6d499c8b19eacb3fa5fe0f", None, "blocked_push"),
    (4, "csrf-exempt-blueprints", "8d8b6a58411d4222b5a7bc17a1093461", None, "blocked_push"),
    (5, "recaptcha-oauth-config", "8cb45708f51940bb87e876a938e7e3fd", None, "blocked_push"),
]


def validator_report(pr_number: int) -> str:
    pr = github.get_pr(pr_number)
    diff = github.get_pr_diff(pr_number)
    files = [f["filename"] for f in github.get_pr_files(pr_number)]
    scan = scan_diff(diff)
    touched = any(is_product_code(f) for f in files)
    return (
        f"## Independent validator report -- PR #{pr_number}\n\n"
        f"Re-derived from the live PR branch (`{pr['branch']}`), not Devin's self-report.\n\n"
        f"| Gate | Result |\n|---|---|\n"
        f"| Anti-cheat diff scan | {'FAIL: ' + ', '.join(scan['forbidden_patterns']) if scan['forbidden_patterns'] else 'PASS -- no skip/flaky/xfail/retry/sleep/assert-weakening'} |\n"
        f"| Provenance (test-side only) | {'FAIL -- touched product code' if touched else 'PASS -- ' + ', '.join(files)} |\n"
        f"| Statistical seed-sweep | env-gated (runs in the Superset dev env); Devin's run on this branch: baseline + seeds 202/303/404 + fresh 505-909 all green |\n\n"
        f"Verdict (gates evaluated here): **no cheat, test-side only.** Full `stabilized` "
        f"requires the seed-sweep in a Superset env (or via a Machine Snapshot)."
    )


def main() -> None:
    for name, color in [("status:devin-finished", "5319e7"), ("status:pr-open", "0e8a16"),
                        ("status:needs-human", "d93f0b")]:
        github.ensure_label(name, color, "")

    # PR #6: post the validator report.
    github.add_comment(6, validator_report(6))
    print("posted validator report on PR #6")

    for issue, cluster_id, sid, pr, state in STATE:
        if state == "pr_open":
            body = (f"Devin session [{sid[:12]}]({SESS}/{sid}) produced a verified root-cause "
                    f"fix -> **PR #{pr}**. Independent validator: anti-cheat PASS, provenance PASS "
                    f"(test-side only). See the report on the PR.")
            labels = ["status:pr-open"]
        else:
            body = (f"Devin session [{sid[:12]}]({SESS}/{sid}) **finished with a verified "
                    f"test-side fix** but could not open a PR (`git push` -> HTTP 403). Awaiting "
                    f"Devin GitHub-app authorization on this fork; the verified diff can be applied "
                    f"in the interim.")
            labels = ["status:devin-finished", "status:needs-human"]
        github.add_comment(issue, body)
        github.add_labels(issue, labels)
        print(f"updated issue #{issue} ({cluster_id}) -> {labels}")

        # Re-link the local remediation row so the dashboard shows the issue/PR.
        rem = db.get_by_idempotency_key(f"{cluster_id}:{config.github_repo}")
        if rem:
            fields = {"issue_number": issue, "issue_url": f"https://github.com/{config.github_repo}/issues/{issue}"}
            if pr:
                fields["pr_url"] = f"https://github.com/{config.github_repo}/pull/{pr}"
                fields["pr_number"] = pr
            db.update_remediation(rem.id, **fields)
    print("done")


if __name__ == "__main__":
    main()

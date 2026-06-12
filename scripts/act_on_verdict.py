"""Act on the verdict: auto-merge every stabilized PR that passes policy.

This closes the loop -- the system doesn't just report a verdict, it acts on it.
Conservative by design (see app/policy.py): only `stabilized` PRs, only with
AUTO_MERGE enabled.

Usage:
    AUTO_MERGE=1 DEVIN_MOCK=0 GITHUB_REPO=catherineyinzhao/superset \
        GITHUB_TOKEN="$(gh auth token)" DB_PATH=./data/autofix.db python -m scripts.act_on_verdict
"""
from __future__ import annotations

from app import db, events
from app.github_client import github, pr_number_from_url
from app.policy import auto_merge_ok


def main() -> None:
    acted = 0
    for rem in db.list_remediations():
        ok, reason = auto_merge_ok(rem)
        if not ok:
            print(f"  skip {rem.cluster_id}: {reason}")
            continue
        prn = pr_number_from_url(rem.pr_url)
        if github.merge_pr(prn):
            db.update_remediation(rem.id, summary=(rem.summary or "") + " Auto-merged on the verified verdict.")
            events.log("auto_merged", f"auto-merged PR #{prn} ({rem.cluster_id}) on stabilized verdict",
                       remediation_id=rem.id, cluster_id=rem.cluster_id, pr=prn)
            print(f"  MERGED PR #{prn} ({rem.cluster_id})")
            acted += 1
        else:
            print(f"  merge FAILED for PR #{prn} ({rem.cluster_id})")
    print(f"acted on {acted} verified PR(s)")


if __name__ == "__main__":
    main()

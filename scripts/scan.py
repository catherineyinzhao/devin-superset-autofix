"""The scan-results trigger: scan the cloned fork, then act on the findings.

For each finding not already tracked, file a GitHub issue and register a
remediation. This is a genuine 'scan results' event (Part 2 of the brief) across
more than one issue class -- flaky (order-dependence) and security (S506).

Usage:
    DEVIN_MOCK=0 GITHUB_REPO=catherineyinzhao/superset GITHUB_TOKEN="$(gh auth token)" \
        DB_PATH=./data/autofix.db python -m scripts.scan /tmp/superset-pr6
"""
from __future__ import annotations

import sys

from app import db, events
from app.clusters import get_cluster
from app.config import config
from app.github_client import github
from app.models import Remediation, Status
from app.scanners import scan_all


def main() -> None:
    repo_dir = sys.argv[1] if len(sys.argv) > 1 else None
    findings = scan_all(repo_dir)
    events.log(events.Event.SCAN_STARTED, f"scan produced {len(findings)} finding(s)")
    from collections import Counter
    by_class = Counter(f["issue_class"] for f in findings)
    print(f"scan: {len(findings)} finding(s) -- " + ", ".join(f"{n} {k}" for k, n in by_class.items()))

    for f in findings:
        c = get_cluster(f["cluster_id"])
        key = f"{c.id}:{config.github_repo}"
        if db.get_by_idempotency_key(key):
            print(f"  already tracked: {c.id}")
            continue
        loc = f.get("location", "")
        body = (f"Found by automated scan (`{f.get('rule', c.root_cause_class)}`) at `{loc}`.\n\n"
                f"{c.root_cause}\n\n**Suggested fix:** {c.fix_note}\n\n"
                f"Labeling `{config.trigger_label}` dispatches a Devin session; the fix is then "
                f"independently verified before a human reviews it.")
        issue = github.create_issue(c.title, body, labels=c.labels) if not config.devin_mock \
            else {"number": None, "html_url": None}
        rem = db.insert_remediation(Remediation(
            cluster_id=c.id, cluster_title=c.title, issue_number=issue["number"],
            issue_url=issue["html_url"], status=Status.QUEUED, target_count=c.target_count,
            known_bad_seeds=c.known_bad_seeds, eng_hours_saved=c.human_baseline_hours,
            idempotency_key=key,
            summary=f"Found by scan ({f.get('rule', c.root_cause_class)}) at {loc}; issue filed; awaiting remediation."))
        events.log(events.Event.ISSUE_CREATED, f"filed issue for {c.id}",
                   remediation_id=rem.id, cluster_id=c.id)
        print(f"  filed + registered: {c.id} -> {issue['html_url']}")


if __name__ == "__main__":
    main()

"""Create one GitHub issue per flaky cluster in the fork (Part 1 deliverable).

Each issue documents a REAL flaky-test cluster discovered by Devin (see
docs/FLAKY_REPORT.md) and carries the trigger label so the webhook/scan path
picks it up. Idempotent: skips a cluster if an open issue with the same title
already exists.

Run:
    GITHUB_REPO=catherineyinzhao/superset GITHUB_TOKEN=$(gh auth token) \
        python -m scripts.seed_issues
"""
from __future__ import annotations

from app.clusters import CLUSTERS, Cluster
from app.config import config
from app.github_client import github


def issue_body(c: Cluster) -> str:
    targets = "\n".join(f"- `{t}`" for t in c.target_test_ids)
    seeds = ", ".join(str(s) for s in c.known_bad_seeds)
    return f"""## Flaky-test cluster: `{c.id}`

**Class:** {c.root_cause_class}
**Target test(s) ({c.target_count}):**
{targets}

**Behaviour:** green in default order; fails only under specific randomized
orderings. Known-bad seeds: {seeds}.

**Suspected root cause:** {c.root_cause}

---
This issue is auto-remediated by the Devin flaky-test autofix system. Applying
the `{config.trigger_label}` label dispatches a Devin session that must fix the
root-cause state leak (no skip/flaky/retry/sleep), after which an independent
statistical validator re-runs the targets across the known-bad + fresh seeds
before the PR is marked ready for human review.
"""


def main() -> None:
    if config.devin_mock:
        print("DEVIN_MOCK=1 -> would create these issues (not creating in mock):")
        for c in CLUSTERS:
            print(f"  - {c.title}  labels={c.labels}")
        return

    existing = {i["title"] for i in github.list_labeled_issues(config.trigger_label)}
    for c in CLUSTERS:
        github.ensure_label(config.trigger_label, "0052cc", "Trigger Devin flaky-test remediation")
        for lbl in c.labels:
            if lbl != config.trigger_label:
                github.ensure_label(lbl, "5319e7", "")
        if c.title in existing:
            print(f"  SKIP (exists): {c.title}")
            continue
        res = github.create_issue(c.title, issue_body(c), labels=c.labels)
        print(f"  CREATED #{res['number']}: {res['html_url']}")


if __name__ == "__main__":
    main()

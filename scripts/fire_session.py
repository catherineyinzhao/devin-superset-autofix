"""Fire a REAL Devin remediation session for one cluster, then poll it.

Usage:
    DEVIN_MOCK=0 DEVIN_API_KEY=... GITHUB_REPO=catherineyinzhao/superset \
        python -m scripts.fire_session bigquery-flask-g-asyncmock

Reads the cluster's prompt file, creates a Devin session via our own
DevinClient (the real API path), records a Remediation row, prints the
app.devin.ai URL to watch, and polls a few times so you can see it move.
"""
from __future__ import annotations

import sys

from app import db, events
from app.clusters import CLUSTERS, Cluster, get_cluster
from app.config import config
from app.devin_client import devin
from app.devin_primitives import primitives_for, summary as primitives_summary
from app.models import Remediation, Status
from app.prompts import FIX_OUTPUT_SCHEMA, build_prompt


def fire_one(cluster: Cluster) -> Remediation:
    key = f"{cluster.id}:{config.github_repo}"
    existing = db.get_by_idempotency_key(key)
    if existing and existing.session_id and existing.status != Status.FAILED:
        print(f"  SKIP  {cluster.id}: already has session {existing.session_id} "
              f"(status={existing.status}) -> {existing.session_url}")
        return existing

    prompt = build_prompt(cluster, repo_url=f"https://github.com/{config.github_repo}",
                          issue_number=existing.issue_number if existing else None)
    prim = primitives_for(cluster)
    created = devin.create_session(
        prompt, title=f"[flaky-fix] {cluster.id}", tags=cluster.labels,
        playbook_id=prim["playbook_id"], snapshot_id=prim["snapshot_id"],
        knowledge_ids=prim["knowledge_ids"], structured_output_schema=FIX_OUTPUT_SCHEMA,
        mock_cluster_id=cluster.id,
    )
    sid, surl = created["session_id"], created["session_url"]
    rem = db.insert_remediation(Remediation(
        cluster_id=cluster.id, cluster_title=cluster.title,
        session_id=sid, session_url=surl, status=Status.RUNNING,
        attempts=1, target_count=cluster.target_count,
        known_bad_seeds=cluster.known_bad_seeds,
        eng_hours_saved=cluster.human_baseline_hours,
        primitives=primitives_summary(prim),
        idempotency_key=key,
    ))
    events.log(events.Event.SESSION_CREATED, f"fired session for {cluster.id}",
               remediation_id=rem.id, cluster_id=cluster.id, session_url=surl)
    print(f"  FIRED {cluster.id}  ({cluster.target_count} tests)\n        WATCH: {surl}")
    return rem


def main() -> None:
    args = sys.argv[1:]
    if not args or args == ["all"]:
        clusters = list(CLUSTERS)
    else:
        clusters = [get_cluster(a) for a in args]
        if any(c is None for c in clusters):
            sys.exit(f"unknown cluster in {args}")

    print(f"mock_mode={config.devin_mock}  repo={config.github_repo}  "
          f"acu_limit={config.devin_max_acu_limit}  firing {len(clusters)} cluster(s)\n")
    db.init_db()
    for c in clusters:
        fire_one(c)
    print("\n  Sessions are running on Devin's side; the poller/dashboard tracks them from here.")


if __name__ == "__main__":
    main()



if __name__ == "__main__":
    main()

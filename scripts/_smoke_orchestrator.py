"""Full mock pipeline: dispatch all clusters, poll to terminal, print outcomes.

Run: python -m scripts._smoke_orchestrator
"""
from app import db
from app.clusters import CLUSTERS
from app.models import Status
from app.orchestrator import dispatch, poll_once


def main() -> None:
    db.init_db()
    for c in CLUSTERS:
        dispatch(c, issue_number=100 + len(c.id) % 5)  # fake issue numbers

    # Reconcile until everything is terminal (or we hit a safety cap).
    for cycle in range(40):
        active = poll_once()
        if active == 0:
            break

    print(f"\n{'cluster':32} {'status':12} {'verdict':18} attempts seeds")
    print("-" * 80)
    for rem in sorted(db.list_remediations(), key=lambda r: r.cluster_id):
        print(f"{rem.cluster_id:32} {rem.status:12} {rem.verdict:18} "
              f"{rem.attempts:>5}    {rem.seeds_run:>4}")

    # Assertions: the demo narrative must hold.
    by_id = {r.cluster_id: r for r in db.list_remediations()}
    assert by_id["bigquery-flask-g-asyncmock"].status == Status.STABILIZED
    assert by_id["csrf-exempt-blueprints"].status == Status.STABILIZED
    # cheat -> feedback -> stabilized (took >1 attempt)
    ds = by_id["dataset-import-allowlist"]
    assert ds.status == Status.STABILIZED and ds.attempts >= 2, (ds.status, ds.attempts)
    # still_flaky -> feedback -> stabilized
    cat = by_id["catalog-perms-metadata-leak"]
    assert cat.status == Status.STABILIZED and cat.attempts >= 2, (cat.status, cat.attempts)
    # product bug -> escalated, never stabilized
    assert by_id["recaptcha-oauth-config"].status == Status.ESCALATED

    stabilized = sum(1 for r in by_id.values() if r.status == Status.STABILIZED)
    hours = sum(r.eng_hours_saved for r in by_id.values())
    print(f"\nstabilized: {stabilized}/5   escalated: 1/5   eng-hours saved: {hours}")
    print("OK orchestrator (full closed loop verified)")


if __name__ == "__main__":
    main()

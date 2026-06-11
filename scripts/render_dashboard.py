"""Render a static dashboard snapshot to docs/dashboard-sample.html.

Seeds a representative *frozen frame* that shows the whole range at once -- the
contrast beat included (CI green, but the validator caught a cheat / still-flaky
/ product bug) -- then renders the real Jinja template standalone. Open the file
in a browser for a Loom screenshot; no server needed.

Run:  DB_PATH=/tmp/sample.db python -m scripts.render_dashboard
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app import db, events
from app.config import config
from app.models import Remediation, Status, Verdict

ROOT = Path(__file__).resolve().parents[1]
PRIM = "playbook:pb-flaky-state-isolation | snapshot:snap-superset-dev | knowledge:kb-superset-flaky-incidents"
REPO = config.github_repo


def _rem(cluster, title, status, verdict, ci, seeds, attempts, hours, pr=None):
    return Remediation(
        cluster_id=cluster, cluster_title=title, status=status, verdict=verdict,
        ci_status=ci, seeds_run=seeds, attempts=attempts, target_count=1,
        eng_hours_saved=hours, primitives=PRIM,
        session_url=f"https://app.devin.ai/sessions/{cluster}",
        pr_url=(f"https://github.com/{REPO}/pull/{pr}" if pr else None), pr_number=pr,
        idempotency_key=f"{cluster}:{REPO}",
    )


FRAME = [
    # cluster, title, status, verdict, ci, seeds, attempts, hours, pr
    ("bigquery-flask-g-asyncmock", "BigQuery fetch_data", Status.STABILIZED, Verdict.STABILIZED, "green", 10, 1, 5.0, 7),
    ("dataset-import-allowlist", "dataset import allow-list", Status.FEEDBACK, Verdict.CHEAT_DETECTED, "green", 9, 2, 0.0, 6),
    ("catalog-perms-metadata-leak", "catalog perms", Status.VALIDATING, Verdict.STILL_FLAKY, "green", 10, 2, 0.0, None),
    ("csrf-exempt-blueprints", "csrf exempt blueprints", Status.RUNNING, Verdict.PENDING, "none", 0, 1, 0.0, None),
    ("recaptcha-oauth-config", "recaptcha / OAuth", Status.ESCALATED, Verdict.NEEDS_HUMAN_REVIEW, "green", 9, 1, 0.0, None),
]


def main() -> None:
    db.init_db()
    for c, title, status, verdict, ci, seeds, att, hrs, pr in FRAME:
        rem = db.insert_remediation(_rem(c, title, status, verdict, ci, seeds, att, hrs, pr))
        events.log(events.Event.VERDICT, f"{verdict}", remediation_id=rem.id,
                   cluster_id=c, verdict=verdict, ci_status=ci, seeds_run=seeds)
        if verdict == Verdict.CHEAT_DETECTED:
            events.log(events.Event.CHEAT_CAUGHT, "caught @pytest.mark.flaky in diff",
                       remediation_id=rem.id, cluster_id=c, patterns=["pytest.mark.flaky"])

    env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")))
    html = env.get_template("dashboard.html").render(
        request=None, metrics=db.metrics(), remediations=db.list_remediations(),
        events=db.list_events(limit=40), now="2026-06-10T16:00:00Z (static sample)",
    )
    # Strip the live auto-refresh so the static file doesn't reload itself.
    html = html.replace('<meta http-equiv="refresh" content="5">',
                        '<!-- auto-refresh disabled in static sample -->')
    out = ROOT / "docs" / "dashboard-sample.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}  ({len(html)} bytes)")
    print("metrics:", {k: db.metrics()[k] for k in
                       ("stabilized", "ci_green_lies_caught", "cheats_caught", "in_progress", "eng_hours_saved")})


if __name__ == "__main__":
    main()

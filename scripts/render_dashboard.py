"""Render an AUTHENTIC dashboard snapshot to docs/dashboard-sample.html.

Runs the real pipeline (orchestrator -> clients -> validator, in mock mode) to
completion, then renders the resulting live DB state through the real Jinja
template. The data is genuine output of the system, not hand-seeded; no server
needed. Open the file in a browser for a Loom screenshot.

Run:
    DEVIN_MOCK=1 DB_PATH=/tmp/sample.db GITHUB_REPO=catherineyinzhao/superset \
      DEVIN_PLAYBOOK_STATE_ISOLATION=pb-flaky-state-isolation \
      DEVIN_KNOWLEDGE_IDS=kb-superset-flaky-incidents \
      DEVIN_SNAPSHOT_ID=snap-superset-dev \
      python -m scripts.render_dashboard
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app import db, views
from app.clusters import CLUSTERS
from app.config import config
from app.orchestrator import dispatch, poll_once

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    db.init_db()
    # Dispatch every cluster against its (real) issue, then reconcile to convergence.
    for i, cluster in enumerate(CLUSTERS, start=1):
        dispatch(cluster, issue_number=i,
                 issue_url=f"https://github.com/{config.github_repo}/issues/{i}")
    for _ in range(120):
        if poll_once() == 0:
            break

    env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")))
    html = env.get_template("dashboard.html").render(
        request=None, metrics=db.metrics(), rows=views.remediation_rows(),
        compare=views.approaches_comparison(), live=None,
        now="simulated run (mock mode) -- capability demo",
    )
    # Static file: drop the self-refresh.
    html = html.replace('<meta http-equiv="refresh" content="5">',
                        '<!-- auto-refresh disabled in static sample -->')
    out = ROOT / "docs" / "dashboard-demo.html"
    out.write_text(html, encoding="utf-8")

    m = db.metrics()
    print(f"wrote {out}  ({len(html)} bytes)")
    print("AUTHENTIC end-state:", {k: m[k] for k in
          ("stabilized", "escalated", "ci_green_lies_caught", "cheats_caught",
           "in_progress", "eng_hours_saved", "total_seeds_run")})


if __name__ == "__main__":
    main()

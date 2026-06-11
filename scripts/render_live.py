"""Render the LIVE dashboard to docs/dashboard-sample.html from REAL Devin sessions.

Pulls each real session's status from the Devin API, runs the validator's fast
gates (anti-cheat + provenance) live on any open PR, and renders the cards as an
artifact of those live sessions -- the rich pipeline view is downstream of the
live strip, not a static mock. The statistical seed-sweep is left deferred (it
needs the Superset dev env / a Machine Snapshot).

Run:
    DEVIN_MOCK=0 DEVIN_API_KEY="$(cat ~/.devin/api_key)" GITHUB_REPO=catherineyinzhao/superset \
      GITHUB_TOKEN="$(gh auth token)" DB_PATH=./data/autofix.db python -m scripts.render_live
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app import db, live, memory, views

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    db.init_db()
    n = live.sync()  # live-pull real sessions + run fast gates on PRs -> DB
    env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")))
    html = env.get_template("dashboard.html").render(
        request=None, metrics=db.metrics(), rows=views.remediation_rows(),
        compare=views.approaches_comparison(), live=live.strip(), memory=memory.entries(),
        now="live pull from the Devin API",
    )
    html = html.replace('<meta http-equiv="refresh" content="5">',
                        '<!-- auto-refresh disabled in static sample -->')
    out = ROOT / "docs" / "dashboard-sample.html"
    out.write_text(html, encoding="utf-8")
    m = db.metrics()
    print(f"synced {n} real Devin sessions; wrote {out}")
    print("live end-state:", {k: m[k] for k in ("total", "stabilized", "escalated", "in_progress")})


if __name__ == "__main__":
    main()

"""FastAPI app: event intake + observability.

Event-driven entry points (Part 2 of the brief):
  - POST /webhook/github   GitHub webhook. `issues` + action=labeled with the
                           trigger label -> dispatch a Devin session for that
                           cluster. HMAC-validated when a secret is configured.
  - POST /trigger/scan     Scheduled/manual scan: dispatch any trigger-labeled
                           issue that has no active remediation. (In mock mode,
                           seeds all clusters so the demo runs with no GitHub.)

Observability (Part 3):
  - GET /dashboard         the status board (the contrast: CI green vs verdict)
  - GET /api/remediations  JSON of every remediation
  - GET /api/metrics       aggregate trust metrics
  - GET /api/events        recent lifecycle events
  - GET /healthz

A background thread reconciles active sessions every POLL_INTERVAL_SECONDS.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import db, events, views
from app.clusters import CLUSTERS, cluster_for_issue_title
from app.config import config
from app.github_client import github
from app.orchestrator import dispatch, poll_once

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_poller_stop = threading.Event()


def _poller_loop() -> None:
    while not _poller_stop.wait(config.poll_interval_seconds):
        try:
            poll_once()
        except Exception as exc:  # keep the loop alive
            events.log("poll_loop_error", str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    github.ensure_label(config.trigger_label, "0052cc", "Trigger Devin flaky-test remediation")
    for lbl, color in [("status:devin-running", "fbca04"), ("status:stabilized", "0e8a16"),
                       ("status:needs-human", "d93f0b")]:
        github.ensure_label(lbl, color, "")
    thread = None
    if config.run_poller:
        thread = threading.Thread(target=_poller_loop, daemon=True, name="poller")
        thread.start()
        events.log("poller_started", f"interval={config.poll_interval_seconds}s")
    try:
        yield
    finally:
        _poller_stop.set()


app = FastAPI(title="Devin Flaky-Test Autofix", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Event intake
# --------------------------------------------------------------------------- #
def _verify_signature(body: bytes, signature: str) -> bool:
    if not config.github_webhook_secret:
        return True  # no secret configured -> skip (dev/demo)
    expected = "sha256=" + hmac.new(
        config.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
):
    body = await request.body()
    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = await request.json()

    if x_github_event == "issues" and payload.get("action") == "labeled":
        label = (payload.get("label") or {}).get("name")
        if label != config.trigger_label:
            return {"ignored": f"label {label}"}
        issue = payload.get("issue", {})
        cluster = cluster_for_issue_title(issue.get("title", ""))
        if not cluster:
            return {"ignored": "issue title does not map to a known cluster"}
        rem = dispatch(cluster, issue_number=issue.get("number"), issue_url=issue.get("html_url"))
        return {"dispatched": cluster.id, "remediation_id": rem.id}

    return {"ignored": x_github_event}


@app.post("/trigger/scan")
async def trigger_scan():
    """Scheduled/manual scan. In mock mode (no GitHub), seed all clusters."""
    events.log(events.Event.SCAN_STARTED, "scan for trigger-labeled issues")
    dispatched = []
    if config.devin_mock:
        for cluster in CLUSTERS:
            rem = dispatch(cluster)
            dispatched.append({"cluster": cluster.id, "remediation_id": rem.id})
    else:
        for issue in github.list_labeled_issues(config.trigger_label):
            cluster = cluster_for_issue_title(issue.get("title", ""))
            if cluster:
                rem = dispatch(cluster, issue_number=issue["number"], issue_url=issue.get("html_url"))
                dispatched.append({"cluster": cluster.id, "remediation_id": rem.id})
    return {"scanned": True, "dispatched": dispatched}


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return TEMPLATES.TemplateResponse("dashboard.html", {
        "request": request,
        "metrics": db.metrics(),
        "rows": views.remediation_rows(),
        "compare": views.approaches_comparison(),
        "now": "updated " + db.now_iso(),
    })


@app.get("/api/remediations")
async def api_remediations():
    return JSONResponse([r.to_row() for r in db.list_remediations()])


@app.get("/api/metrics")
async def api_metrics():
    return JSONResponse(db.metrics())


@app.get("/api/events")
async def api_events(limit: int = 100):
    return JSONResponse(db.list_events(limit=limit))


@app.get("/healthz")
async def healthz():
    return {"ok": True, "mock": config.devin_mock, "repo": config.github_repo}


@app.get("/", response_class=HTMLResponse)
async def root():
    return '<meta http-equiv="refresh" content="0; url=/dashboard">'

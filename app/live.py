"""Live pulls from the real Devin sessions -- the source of truth.

`strip()` pulls each real session's CURRENT status straight from the Devin API
(used for the live status bar). `sync()` goes further: it pulls status, and for
any session that opened a PR it runs the validator's FAST gates live (anti-cheat
diff scan + provenance, both real over the live PR diff) and writes the result
back to the DB. The rich remediation cards then render from that synced state --
i.e. they are an artifact of the live sessions, not a static mock.

The statistical seed-sweep is the one gate left deferred here: it needs the
Superset dev env (or a Machine Snapshot) and is not run inside the dashboard.
Sessions that finished without a PR are shown honestly as blocked on push.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app import db, events
from app.devin_client import devin
from app.github_client import (ci_status_from_checks, github, pr_number_from_url)
from app.models import Status, Verdict
from app.validator import is_product_code, scan_diff


def _real(rem) -> bool:
    return bool(rem.session_id) and not rem.session_id.startswith("devin-mock")


def strip() -> List[Dict[str, Any]]:
    """Current status of every real Devin session.

    Source of truth is the verification pipeline (the DB), with the Devin API
    consulted live only for sessions still in flight. A row that has reached a
    terminal pipeline state -- stabilized, merged, escalated -- shows that state
    directly (the Devin session object may still read 'working' long after we've
    verified and merged its PR). For in-flight rows we pull the live Devin status,
    and if the API is unreachable (e.g. trial credits exhausted -> 403) we fall
    back to the last-known pipeline status rather than a misleading 'unknown'.
    """
    out: List[Dict[str, Any]] = []
    for r in db.list_remediations():
        if not _real(r):
            continue
        merged = bool(r.summary and "Auto-merged" in r.summary)
        if merged or r.status in Status.TERMINAL:
            status = "merged" if merged else r.status
        else:
            try:
                status = devin.get_session(r.session_id)["status"]
            except Exception:
                status = r.status or "unknown"
        out.append({"cluster_id": r.cluster_id, "session_url": r.session_url,
                    "status": status, "pr_url": r.pr_url})
    return out


def sync() -> int:
    """Pull each real session live; run the fast gates on any PR; persist. Returns
    how many real sessions were synced."""
    n = 0
    for r in db.list_remediations():
        if not _real(r):
            continue
        if r.status in Status.TERMINAL:
            continue  # terminal (stabilized / escalated / failed) -- don't churn it on a volatile live pull
        try:
            snap = devin.get_session(r.session_id)
        except Exception:
            continue  # API unreachable (e.g. credits exhausted -> 403): keep last-known DB state, don't clobber
        n += 1
        status, pr = snap["status"], (snap.get("pr_url") or r.pr_url)

        if pr:  # a PR exists (session may still be working/blocked) -> verify it
            prn = pr_number_from_url(pr)
            pri = github.get_pr(prn) if prn else None
            diff = github.get_pr_diff(prn) if prn else ""
            files = [f["filename"] for f in github.get_pr_files(prn)] if prn else []
            ds = scan_diff(diff)
            touched = any(is_product_code(f) for f in files)
            ci = ci_status_from_checks(github.get_check_runs(pri["head_sha"])) if pri else "none"
            if ds["forbidden_patterns"]:
                verdict, st = Verdict.CHEAT_DETECTED, Status.FEEDBACK
            elif touched:
                verdict, st = Verdict.NEEDS_HUMAN_REVIEW, Status.ESCALATED
            else:
                verdict, st = Verdict.PENDING, Status.VALIDATING  # gates passed; sweep pending
            detail = {"results": {
                "diff_scan": ds,
                "provenance": {"touched_product_code": touched, "files_changed": files},
                "seed_sweep_note": "deferred -- runs in the Superset dev env / a Machine Snapshot",
            }}
            db.update_remediation(r.id, status=st, verdict=verdict, verdict_detail=detail,
                                  ci_status=ci, pr_url=pr, pr_number=prn,
                                  summary="PR open; anti-cheat + provenance gates run live; statistical seed-sweep deferred.")
            events.log(events.Event.CI_OBSERVED, f"live: GitHub CI = {ci}", remediation_id=r.id,
                       cluster_id=r.cluster_id, ci_status=ci)
        elif devin.is_finished(status):  # finished, still no PR
            db.update_remediation(r.id, status=Status.ESCALATED, verdict=Verdict.NEEDS_HUMAN_REVIEW,
                                  summary="Devin finished a fix but could not open a PR (push blocked, HTTP 403) -- awaiting Devin GitHub-app authorization on the fork.")
        elif devin.is_blocked(status):
            db.update_remediation(r.id, status=Status.RUNNING, verdict=Verdict.PENDING,
                                  summary="Devin session is blocked / needs input (pulled live).")
        else:
            db.update_remediation(r.id, status=Status.RUNNING, verdict=Verdict.PENDING,
                                  summary="Devin session is working (status pulled live from the Devin API).")
    return n

"""The control loop: fire -> poll -> validate -> feed back / escalate.

This is the closed loop from docs/APPROACH.md. A generation-only pipeline stops
when Devin opens a PR; we treat that as the *start* of verification:

  - dispatch()    create a Devin session for a cluster, persist a Remediation
  - poll_once()   advance every non-terminal remediation by one step
  - on finished   run the independent validator, then ROUTE on the verdict:
        stabilized          -> done; PR ready for human review
        cheat/still/regress  -> send the validator's evidence back (bounded), retry
        needs_human_review   -> escalate (product bug, don't mask)
"""
from __future__ import annotations

import os
from typing import Optional

from app import db, events, memory
from app.clusters import Cluster, get_cluster
from app.config import config
from app.devin_client import devin
from app.github_client import github, pr_number_from_url
from app.devin_primitives import primitives_for, summary as primitives_summary
from app.models import Remediation, Status, Verdict
from app.prompts import FIX_OUTPUT_SCHEMA, build_followup, build_prompt
from app.validator import validate

MAX_CORRECTION_ROUNDS = int(os.getenv("MAX_CORRECTION_ROUNDS", "3"))


# --------------------------------------------------------------------------- #
# Dispatch (event handler side)
# --------------------------------------------------------------------------- #
_ACTIVE_STATES = (Status.RUNNING, Status.VALIDATING, Status.FEEDBACK)


def _active_session_count() -> int:
    return sum(1 for r in db.list_remediations()
               if r.session_id and r.status in _ACTIVE_STATES)


def dispatch(cluster: Cluster, issue_number: Optional[int] = None,
             issue_url: Optional[str] = None) -> Remediation:
    """Register a remediation for a cluster, then fire it if there is spare
    concurrency budget; otherwise leave it QUEUED for the poller to promote.
    Idempotent per (cluster, repo): a webhook retry or re-scan never double-fires."""
    key = f"{cluster.id}:{config.github_repo}"
    existing = db.get_by_idempotency_key(key)
    if existing and existing.session_id and existing.status != Status.FAILED:
        return existing
    if existing:
        rem = existing
    else:
        rem = db.insert_remediation(Remediation(
            cluster_id=cluster.id, cluster_title=cluster.title,
            issue_number=issue_number, issue_url=issue_url, status=Status.QUEUED,
            target_count=cluster.target_count, known_bad_seeds=cluster.known_bad_seeds,
            eng_hours_saved=cluster.human_baseline_hours, idempotency_key=key,
        ))
        events.log(events.Event.SCAN_STARTED, f"queued {cluster.id}",
                   remediation_id=rem.id, cluster_id=cluster.id)
    promote_queued()
    return db.get_remediation(rem.id) or rem


def _fire(rem: Remediation) -> None:
    """Actually create the Devin session for an already-registered remediation,
    attaching the configured Devin primitives (Playbook / Knowledge / Snapshot)."""
    cluster = get_cluster(rem.cluster_id)
    prompt = build_prompt(cluster, f"https://github.com/{config.github_repo}", rem.issue_number)
    prim = primitives_for(cluster)
    created = devin.create_session(
        prompt, title=f"[flaky-fix] {cluster.id}", tags=cluster.labels,
        playbook_id=prim["playbook_id"], snapshot_id=prim["snapshot_id"],
        knowledge_ids=prim["knowledge_ids"], structured_output_schema=FIX_OUTPUT_SCHEMA,
        mock_cluster_id=cluster.id,
    )
    prim_str = primitives_summary(prim)
    db.update_remediation(rem.id, session_id=created["session_id"],
                          session_url=created["session_url"], status=Status.RUNNING,
                          attempts=max(rem.attempts, 1), primitives=prim_str)
    events.log(events.Event.SESSION_CREATED, f"dispatched Devin for {cluster.id}",
               remediation_id=rem.id, cluster_id=cluster.id, session_url=created["session_url"])
    events.log(events.Event.PRIMITIVES_ATTACHED, prim_str,
               remediation_id=rem.id, cluster_id=cluster.id)
    if rem.issue_number:
        github.add_comment(rem.issue_number, f"Devin remediation started: {created['session_url']}")
        github.add_labels(rem.issue_number, ["status:devin-running"])


def promote_queued() -> None:
    """Fire QUEUED remediations up to the concurrency cap (cost governance)."""
    while _active_session_count() < config.max_active_sessions:
        nxt = next((r for r in db.list_active()
                    if r.status == Status.QUEUED and not r.session_id), None)
        if not nxt:
            return
        _fire(nxt)


# --------------------------------------------------------------------------- #
# Poll loop (reconciliation)
# --------------------------------------------------------------------------- #
def poll_once() -> int:
    """Advance every active remediation by one step, then promote any QUEUED
    work that now fits under the concurrency cap. Returns how many were active."""
    active = db.list_active()
    for rem in active:
        if not rem.session_id:
            continue  # QUEUED with no session yet -> handled by promote_queued()
        try:
            _advance(rem)
        except Exception as exc:  # one bad row must not stall the others
            events.log("poll_error", str(exc), remediation_id=rem.id, cluster_id=rem.cluster_id)
    promote_queued()
    return len(active)


def _advance(rem: Remediation) -> None:
    if not rem.session_id:
        return
    snap = devin.get_session(rem.session_id)
    status = snap["status"]

    if devin.is_blocked(status):
        if rem.status != Status.FEEDBACK:
            events.log(events.Event.SESSION_BLOCKED, "Devin is blocked / needs input",
                       remediation_id=rem.id, cluster_id=rem.cluster_id)
            if rem.issue_number:
                github.add_comment(rem.issue_number,
                                   f"Devin needs input -- see {rem.session_url}")
            db.update_remediation(rem.id, status=Status.RUNNING)
        return

    if devin.is_failed(status):
        _finish(rem, Status.FAILED, "Devin session failed/abandoned")
        events.log(events.Event.SESSION_FAILED, "session abandoned",
                   remediation_id=rem.id, cluster_id=rem.cluster_id)
        if rem.issue_number:
            github.add_comment(rem.issue_number, "Automated remediation failed -- needs a human.")
        return

    if not devin.is_finished(status):
        if rem.status == Status.QUEUED:
            db.update_remediation(rem.id, status=Status.RUNNING)
        return

    # Finished: there should be a PR.
    pr_url = snap.get("pr_url") or rem.pr_url
    if not pr_url:
        _finish(rem, Status.ESCALATED, "session finished without opening a PR")
        events.log(events.Event.ESCALATED, "finished, no PR -> human",
                   remediation_id=rem.id, cluster_id=rem.cluster_id)
        return

    pr = github.get_pr(pr_number_from_url(pr_url))
    head_sha = pr["head_sha"] if pr else None
    if not _needs_validation(rem, head_sha):
        return  # already judged this exact PR state

    escalated = _devin_escalated(snap)
    _validate_and_route(rem, pr_url, head_sha, escalated)


def _needs_validation(rem: Remediation, head_sha: Optional[str]) -> bool:
    if rem.verdict == Verdict.PENDING:
        return True
    last = (rem.verdict_detail or {}).get("_validated_sha")
    # Re-validate only when a new commit (new PR state) has appeared since.
    return head_sha is not None and head_sha != last


def _devin_escalated(snap: dict) -> bool:
    so = (snap.get("raw") or {}).get("structured_output") or {}
    return bool(so.get("escalate"))


def _validate_and_route(rem: Remediation, pr_url: str, head_sha: Optional[str],
                        escalated: bool) -> None:
    cluster = get_cluster(rem.cluster_id)
    db.update_remediation(rem.id, status=Status.VALIDATING, pr_url=pr_url)
    events.log(events.Event.VALIDATION_STARTED, "independent re-derivation started",
               remediation_id=rem.id, cluster_id=rem.cluster_id, pr_url=pr_url)

    v = validate(cluster, pr_url, escalated=escalated)
    detail = v.to_dict()
    detail["_validated_sha"] = head_sha

    events.log(events.Event.CI_OBSERVED, f"GitHub CI reported: {v.ci_status}",
               remediation_id=rem.id, cluster_id=rem.cluster_id, ci_status=v.ci_status)
    events.log(events.Event.VERDICT, v.summary, remediation_id=rem.id,
               cluster_id=rem.cluster_id, verdict=v.verdict, ci_status=v.ci_status,
               seeds_run=v.seeds_run)

    db.update_remediation(
        rem.id, verdict=v.verdict, verdict_detail=detail, ci_status=v.ci_status,
        seeds_run=v.seeds_run, summary=v.summary,
        pr_url=pr_url, pr_number=pr_number_from_url(pr_url),
    )

    if v.verdict == Verdict.STABILIZED:
        _finish(rem, Status.STABILIZED, v.summary)
        memory.record(cluster.id, cluster.root_cause_class, cluster.root_cause,
                      cluster.leaker, cluster.fix_note, v.summary)
        events.log(events.Event.STABILIZED, "PR ready for human review",
                   remediation_id=rem.id, cluster_id=rem.cluster_id, pr_url=pr_url)
        if rem.issue_number:
            github.add_comment(rem.issue_number, f"Independently verified -- STABILIZED.\n\n{v.summary}\n\nPR: {pr_url}")
            github.remove_label(rem.issue_number, "status:devin-running")
            github.add_labels(rem.issue_number, ["status:stabilized"])
        return

    if v.verdict == Verdict.NEEDS_HUMAN_REVIEW:
        _finish(rem, Status.ESCALATED, v.summary)
        events.log(events.Event.ESCALATED, "routed to human (product bug / escalation)",
                   remediation_id=rem.id, cluster_id=rem.cluster_id)
        if rem.issue_number:
            github.add_comment(rem.issue_number, f"Routed to a human.\n\n{v.summary}\n\nPR: {pr_url}")
            github.add_labels(rem.issue_number, ["status:needs-human"])
        return

    if v.verdict == Verdict.INCONCLUSIVE:
        # Leave non-terminal; the next poll retries once the env settles.
        db.update_remediation(rem.id, status=Status.RUNNING)
        return

    # Retryable: cheat_detected / still_flaky / regressed.
    if v.verdict == Verdict.CHEAT_DETECTED:
        events.log(events.Event.CHEAT_CAUGHT, v.summary, remediation_id=rem.id,
                   cluster_id=rem.cluster_id,
                   patterns=v.results.get("diff_scan", {}).get("forbidden_patterns"))

    if rem.attempts >= MAX_CORRECTION_ROUNDS:
        _finish(rem, Status.ESCALATED, f"max correction rounds reached; {v.summary}")
        events.log(events.Event.ESCALATED, "max self-correction rounds -> human",
                   remediation_id=rem.id, cluster_id=rem.cluster_id)
        if rem.issue_number:
            github.add_comment(rem.issue_number, f"Could not auto-stabilize in {MAX_CORRECTION_ROUNDS} rounds -- needs a human.\n\n{v.summary}")
            github.add_labels(rem.issue_number, ["status:needs-human"])
        return

    # Send the validator's evidence back to the SAME session and let it retry.
    followup = build_followup(cluster, v.verdict, v.summary)
    devin.send_message(rem.session_id, followup)
    db.update_remediation(rem.id, status=Status.FEEDBACK, attempts=rem.attempts + 1)
    events.log(events.Event.FEEDBACK_SENT,
               f"verdict={v.verdict}; sent correction (round {rem.attempts + 1})",
               remediation_id=rem.id, cluster_id=rem.cluster_id, verdict=v.verdict)


def _finish(rem: Remediation, status: str, summary: str) -> None:
    fields = {"status": status, "summary": summary, "finished_at": db.now_iso()}
    if rem.created_at:
        try:
            from datetime import datetime
            start = datetime.fromisoformat(rem.created_at)
            fields["duration_sec"] = (datetime.now(start.tzinfo) - start).total_seconds()
        except Exception:
            pass
    if status != Status.STABILIZED:
        fields["eng_hours_saved"] = 0  # only count hours saved when actually verified
    db.update_remediation(rem.id, **fields)

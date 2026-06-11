"""Structured event log — the spine of observability.

Every meaningful transition calls ``log()``. It does two things at once:
  1. persists the event to the ``events`` table (powers the dashboard timeline +
     the metrics aggregation), and
  2. emits a single structured line to stdout (powers `docker logs` / any log
     aggregator), so an engineering leader can answer "is this working?" from
     either the UI or the logs.

The event vocabulary is deliberately verb-like and maps 1:1 to the lifecycle in
``docs/APPROACH.md`` so the timeline reads like a narrative.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from app import db


class Event:
    # detection / dispatch
    SCAN_STARTED = "scan_started"
    ISSUE_CREATED = "issue_created"
    SESSION_CREATED = "session_created"
    PRIMITIVES_ATTACHED = "primitives_attached"
    # devin lifecycle
    SESSION_RUNNING = "session_running"
    SESSION_BLOCKED = "session_blocked"
    PR_OPENED = "pr_opened"
    SESSION_FAILED = "session_failed"
    # verification (the differentiator)
    VALIDATION_STARTED = "validation_started"
    CI_OBSERVED = "ci_observed"               # what GitHub CI reported (for the contrast)
    VERDICT = "verdict"                        # the independent re-derived verdict
    CHEAT_CAUGHT = "cheat_caught"              # forbidden pattern found in diff
    # control loop
    FEEDBACK_SENT = "feedback_sent"            # validator evidence sent back to Devin
    ESCALATED = "escalated"                    # routed to a human
    STABILIZED = "stabilized"                  # confirmed, ready for human review


def log(
    kind: str,
    message: str = "",
    remediation_id: Optional[int] = None,
    cluster_id: Optional[str] = None,
    **data: Any,
) -> None:
    """Record one lifecycle event (DB + stdout)."""
    db.insert_event(
        kind=kind,
        message=message,
        remediation_id=remediation_id,
        cluster_id=cluster_id,
        data=data or None,
    )
    line = {
        "ts": db.now_iso(),
        "event": kind,
        "remediation_id": remediation_id,
        "cluster_id": cluster_id,
        "msg": message,
    }
    if data:
        line["data"] = data
    print(json.dumps(line), file=sys.stdout, flush=True)

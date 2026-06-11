"""Persistence schema + the core domain object (a Remediation).

A *remediation* is one end-to-end unit of work: one flaky-test cluster, dispatched
to one Devin session, producing one PR, judged by one independent validator verdict
(possibly after bounded self-correction rounds).

Two tables:
  - ``remediations`` — current state of each unit (one row per cluster dispatch)
  - ``events``       — append-only history feeding the observability dashboard
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Orchestrator state machine (where a remediation is in its lifecycle)
# --------------------------------------------------------------------------- #
class Status:
    QUEUED = "queued"            # issue seen, no session yet
    RUNNING = "running"          # Devin session working
    VALIDATING = "validating"    # PR opened, validator re-deriving the verdict
    FEEDBACK = "feedback"        # validator rejected; correction sent, awaiting new PR
    STABILIZED = "stabilized"    # validator confirmed; PR ready for human review
    ESCALATED = "escalated"      # routed to a human (product bug / max retries)
    FAILED = "failed"            # session abandoned / unrecoverable
    TERMINAL = {STABILIZED, ESCALATED, FAILED}


# --------------------------------------------------------------------------- #
# Validator verdicts (the independent, re-derived judgement of a PR)
# --------------------------------------------------------------------------- #
class Verdict:
    PENDING = "pending"
    STABILIZED = "stabilized"                 # passes all gates, statistically stable
    STILL_FLAKY = "still_flaky"               # a target still fails under >=1 ordering
    CHEAT_DETECTED = "cheat_detected"         # forbidden pattern in the diff (skip/flaky/...)
    REGRESSED = "regressed"                   # targets pass but neighbours newly fail
    NEEDS_HUMAN_REVIEW = "needs_human_review" # touched product code / escalated bug
    INCONCLUSIVE = "inconclusive"             # env/build/collection failure
    # A verdict that should trigger another bounded Devin attempt:
    RETRYABLE = {STILL_FLAKY, CHEAT_DETECTED, REGRESSED}


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS remediations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      TEXT    NOT NULL,
    cluster_title   TEXT    NOT NULL,
    issue_number    INTEGER,
    issue_url       TEXT,
    session_id      TEXT,
    session_url     TEXT,
    branch          TEXT,
    pr_url          TEXT,
    pr_number       INTEGER,
    ci_status       TEXT    DEFAULT 'none',   -- what GitHub CI reports: green/red/pending/none
    status          TEXT    NOT NULL DEFAULT 'queued',
    verdict         TEXT    DEFAULT 'pending',
    verdict_detail  TEXT,                      -- JSON: full validator output
    attempts        INTEGER DEFAULT 0,         -- Devin attempts (1 + feedback rounds)
    target_count    INTEGER DEFAULT 0,
    known_bad_seeds TEXT,                       -- JSON list[int]
    seeds_run       INTEGER DEFAULT 0,          -- total orderings the validator executed
    eng_hours_saved REAL    DEFAULT 0,
    primitives      TEXT,                       -- Devin primitives attached (playbook/snapshot/knowledge)
    summary         TEXT,
    idempotency_key TEXT    UNIQUE,
    created_at      TEXT,
    updated_at      TEXT,
    finished_at     TEXT,
    duration_sec    REAL
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    remediation_id  INTEGER,
    cluster_id      TEXT,
    ts              TEXT NOT NULL,
    kind            TEXT NOT NULL,
    message         TEXT,
    data            TEXT                        -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_events_remediation ON events(remediation_id);
CREATE INDEX IF NOT EXISTS idx_remediations_status ON remediations(status);
"""


@dataclass
class Remediation:
    cluster_id: str
    cluster_title: str
    id: Optional[int] = None
    issue_number: Optional[int] = None
    issue_url: Optional[str] = None
    session_id: Optional[str] = None
    session_url: Optional[str] = None
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    ci_status: str = "none"
    status: str = Status.QUEUED
    verdict: str = Verdict.PENDING
    verdict_detail: Optional[Dict[str, Any]] = None
    attempts: int = 0
    target_count: int = 0
    known_bad_seeds: List[int] = field(default_factory=list)
    seeds_run: int = 0
    eng_hours_saved: float = 0.0
    primitives: Optional[str] = None
    summary: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_sec: Optional[float] = None

    # ---- serialization helpers ---------------------------------------- #
    def to_row(self) -> Dict[str, Any]:
        row = asdict(self)
        row["verdict_detail"] = json.dumps(self.verdict_detail) if self.verdict_detail is not None else None
        row["known_bad_seeds"] = json.dumps(self.known_bad_seeds or [])
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Remediation":
        d = dict(row)
        if d.get("verdict_detail"):
            try:
                d["verdict_detail"] = json.loads(d["verdict_detail"])
            except (TypeError, json.JSONDecodeError):
                d["verdict_detail"] = None
        try:
            d["known_bad_seeds"] = json.loads(d.get("known_bad_seeds") or "[]")
        except (TypeError, json.JSONDecodeError):
            d["known_bad_seeds"] = []
        return cls(**d)

    @property
    def is_terminal(self) -> bool:
        return self.status in Status.TERMINAL

    @property
    def ci_green_but_not_stable(self) -> bool:
        """The headline contrast: GitHub CI says green, our validator disagrees.
        This is the lie no other system in the field can surface."""
        return self.ci_status == "green" and self.verdict in (
            Verdict.STILL_FLAKY,
            Verdict.CHEAT_DETECTED,
            Verdict.REGRESSED,
        )

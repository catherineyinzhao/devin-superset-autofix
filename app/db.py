"""Thin SQLite persistence layer.

Stdlib ``sqlite3`` on purpose: zero external deps, zero setup, survives a
container restart via a mounted volume. WAL mode + connect-per-call keeps the
FastAPI request handlers and the background poller thread from stepping on each
other without an ORM or a connection pool.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from app.config import config
from app.models import CREATE_TABLES_SQL, Remediation, Status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = config.db_path
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(CREATE_TABLES_SQL)


# --------------------------------------------------------------------------- #
# Remediations
# --------------------------------------------------------------------------- #
def insert_remediation(rem: Remediation) -> Remediation:
    """Insert a new remediation. Idempotent on ``idempotency_key``: if a row
    with that key already exists, the existing row is returned unchanged."""
    ts = now_iso()
    rem.created_at = rem.created_at or ts
    rem.updated_at = ts
    row = rem.to_row()
    cols = [
        "cluster_id", "cluster_title", "issue_number", "issue_url", "session_id",
        "session_url", "branch", "pr_url", "pr_number", "ci_status", "status",
        "verdict", "verdict_detail", "attempts", "target_count", "known_bad_seeds",
        "seeds_run", "eng_hours_saved", "summary", "idempotency_key",
        "created_at", "updated_at", "finished_at", "duration_sec",
    ]
    placeholders = ", ".join(f":{c}" for c in cols)
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO remediations ({', '.join(cols)}) VALUES ({placeholders})",
            {c: row[c] for c in cols},
        )
        if cur.rowcount == 0 and rem.idempotency_key:
            # Key collision: return the row that's already there.
            existing = conn.execute(
                "SELECT * FROM remediations WHERE idempotency_key = ?",
                (rem.idempotency_key,),
            ).fetchone()
            if existing:
                return Remediation.from_row(existing)
        rem.id = cur.lastrowid
    return rem


def update_remediation(remediation_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    # JSON-encode dict/list-valued columns transparently.
    import json as _json
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[k] = _json.dumps(v)
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["_id"] = remediation_id
    with _conn() as conn:
        conn.execute(f"UPDATE remediations SET {sets} WHERE id = :_id", fields)


def _one(row: Optional[sqlite3.Row]) -> Optional[Remediation]:
    return Remediation.from_row(row) if row else None


def get_remediation(remediation_id: int) -> Optional[Remediation]:
    with _conn() as conn:
        return _one(conn.execute(
            "SELECT * FROM remediations WHERE id = ?", (remediation_id,)
        ).fetchone())


def get_by_idempotency_key(key: str) -> Optional[Remediation]:
    with _conn() as conn:
        return _one(conn.execute(
            "SELECT * FROM remediations WHERE idempotency_key = ?", (key,)
        ).fetchone())


def get_by_session_id(session_id: str) -> Optional[Remediation]:
    with _conn() as conn:
        return _one(conn.execute(
            "SELECT * FROM remediations WHERE session_id = ?", (session_id,)
        ).fetchone())


def get_by_issue(issue_number: int) -> Optional[Remediation]:
    with _conn() as conn:
        return _one(conn.execute(
            "SELECT * FROM remediations WHERE issue_number = ? ORDER BY id DESC LIMIT 1",
            (issue_number,),
        ).fetchone())


def list_remediations() -> List[Remediation]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM remediations ORDER BY created_at DESC"
        ).fetchall()
    return [Remediation.from_row(r) for r in rows]


def list_active() -> List[Remediation]:
    """Remediations the poller still needs to advance (non-terminal)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM remediations WHERE status NOT IN (?, ?, ?) ORDER BY id",
            (Status.STABILIZED, Status.ESCALATED, Status.FAILED),
        ).fetchall()
    return [Remediation.from_row(r) for r in rows]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def insert_event(
    kind: str,
    message: str = "",
    remediation_id: Optional[int] = None,
    cluster_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    import json as _json
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (remediation_id, cluster_id, ts, kind, message, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (remediation_id, cluster_id, now_iso(), kind, message,
             _json.dumps(data) if data else None),
        )


def list_events(limit: int = 200, remediation_id: Optional[int] = None) -> List[Dict[str, Any]]:
    with _conn() as conn:
        if remediation_id is not None:
            rows = conn.execute(
                "SELECT * FROM events WHERE remediation_id = ? ORDER BY id DESC LIMIT ?",
                (remediation_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def count_events(kind: str) -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM events WHERE kind = ?", (kind,)).fetchone()[0]


# --------------------------------------------------------------------------- #
# Metrics -- "if I were an engineering leader, how would I know this is working?"
# --------------------------------------------------------------------------- #
def metrics() -> Dict[str, Any]:
    rems = list_remediations()
    total = len(rems)
    by_status: Dict[str, int] = {}
    for r in rems:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    stabilized = by_status.get(Status.STABILIZED, 0)
    escalated = by_status.get(Status.ESCALATED, 0)
    failed = by_status.get(Status.FAILED, 0)
    terminal = stabilized + escalated + failed
    in_progress = total - terminal

    # The headline trust metrics, derived from the validator's own verdict events.
    with _conn() as conn:
        ci_green_lies = conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'verdict' "
            "AND json_extract(data,'$.ci_status') = 'green' "
            "AND json_extract(data,'$.verdict') != 'stabilized'"
        ).fetchone()[0]
    cheats_caught = count_events("cheat_caught")

    durations = [r.duration_sec for r in rems if r.duration_sec]
    return {
        "repo": config.github_repo,
        "total": total,
        "by_status": by_status,
        "in_progress": in_progress,
        "stabilized": stabilized,
        "escalated": escalated,
        "failed": failed,
        # success = independently verified, not "CI went green"
        "verified_rate": round(stabilized / terminal, 3) if terminal else 0.0,
        "ci_green_lies_caught": ci_green_lies,
        "cheats_caught": cheats_caught,
        "total_seeds_run": sum(r.seeds_run for r in rems),
        "eng_hours_saved": round(sum(r.eng_hours_saved for r in rems), 1),
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else 0.0,
    }

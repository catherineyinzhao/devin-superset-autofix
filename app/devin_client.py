"""Devin API v1 client.

Endpoints (https://docs.devin.ai/api-reference/overview):
  - POST   /v1/sessions               create a session
  - GET    /v1/session/{id}           poll status + structured output + PR
  - POST   /v1/session/{id}/message   send a follow-up (the self-correction loop)

We normalize Devin's raw status strings into a small, stable internal vocabulary
(running / blocked / finished / failed) so the orchestrator never has to care
about API quirks. In mock mode (DEVIN_MOCK=1) every call is served locally by
``app.mock`` -- no network, no spend, full code path.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from app import mock
from app.config import config

_RUNNING = "running"
_BLOCKED = "blocked"
_FINISHED = "finished"
_FAILED = "failed"

# Raw Devin status strings -> internal vocabulary. Defensive: covers v1 and the
# values seen across the API's lifecycle.
_STATUS_MAP = {
    "running": _RUNNING,
    "working": _RUNNING,
    "blocked": _BLOCKED,
    "suspended": _BLOCKED,
    "paused": _BLOCKED,
    "finished": _FINISHED,
    "completed": _FINISHED,
    "stopped": _FINISHED,
    "exited": _FINISHED,
    "expired": _FAILED,
    "failed": _FAILED,
    "error": _FAILED,
    "abandoned": _FAILED,
}


def normalize_status(raw: Optional[str], has_pr: bool) -> str:
    s = (raw or "").strip().lower()
    mapped = _STATUS_MAP.get(s)
    if mapped:
        # A "finished/stopped" with no PR is really a failure for our purposes.
        if mapped == _FINISHED and not has_pr:
            return _FINISHED  # still finished; orchestrator handles no-PR case
        return mapped
    # Unknown string: a PR is the strongest possible signal of completion.
    return _FINISHED if has_pr else _RUNNING


def extract_pr_url(payload: Dict[str, Any]) -> Optional[str]:
    """Devin reports PRs in several shapes across versions. Check all of them."""
    if not payload:
        return None
    # mock + some v1 responses
    if payload.get("pr_url"):
        return payload["pr_url"]
    if payload.get("pull_request_url"):
        return payload["pull_request_url"]
    pr = payload.get("pull_request")
    if isinstance(pr, dict) and pr.get("url"):
        return pr["url"]
    prs = payload.get("pull_requests")
    if isinstance(prs, list) and prs:
        first = prs[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("pr_url")
    so = payload.get("structured_output")
    if isinstance(so, dict):
        return so.get("pr_url") or so.get("pull_request_url")
    return None


class DevinClient:
    def __init__(self) -> None:
        self.mock = config.devin_mock
        self.base = config.devin_api_base
        self._headers = {
            "Authorization": f"Bearer {config.devin_api_key}",
            "Content-Type": "application/json",
        }

    # ---- create -------------------------------------------------------- #
    def create_session(
        self,
        prompt: str,
        *,
        title: Optional[str] = None,
        tags: Optional[list] = None,
        playbook_id: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        knowledge_ids: Optional[list] = None,
        structured_output_schema: Optional[dict] = None,
        session_secrets: Optional[list] = None,
        mock_cluster_id: Optional[str] = None,
        mock_attempt: int = 0,
    ) -> Dict[str, Any]:
        """Returns {session_id, session_url, is_new_session}. ``mock_*`` args are
        demo-only and ignored by the real API path."""
        if self.mock:
            session_id = f"devin-mock-{mock_cluster_id}-a{mock_attempt}"
            # A Machine Snapshot means the env is pre-built: setup is skipped, so
            # the mock session reaches a PR faster (mirrors the real ACU saving).
            mock.register_session(session_id, mock_cluster_id or "unknown", mock_attempt,
                                  fast=bool(snapshot_id))
            return {"session_id": session_id, "is_new_session": True,
                    "session_url": f"https://app.devin.ai/sessions/{session_id}"}

        body: Dict[str, Any] = {"prompt": prompt, "idempotent": True}
        if title:
            body["title"] = title
        if tags:
            body["tags"] = tags
        if playbook_id:
            body["playbook_id"] = playbook_id
        if snapshot_id:
            body["snapshot_id"] = snapshot_id
        if knowledge_ids is not None:
            body["knowledge_ids"] = knowledge_ids
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema
        if session_secrets:  # e.g. a scoped GitHub token so Devin can push natively
            body["session_secrets"] = session_secrets
        if config.devin_max_acu_limit:
            body["max_acu_limit"] = config.devin_max_acu_limit
        resp = requests.post(f"{self.base}/sessions", json=body, headers=self._headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return {
            "session_id": data.get("session_id") or data.get("id"),
            "session_url": data.get("url") or data.get("session_url"),
            "is_new_session": data.get("is_new_session"),
        }

    # ---- poll ---------------------------------------------------------- #
    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Returns a normalized dict: {status, pr_url, session_url, raw}."""
        if self.mock:
            snap = mock.advance_session(session_id)
            return {
                "status": snap["status"],
                "pr_url": snap.get("pr_url"),
                "pr_number": snap.get("pr_number"),
                "branch": snap.get("branch"),
                "session_url": snap.get("session_url"),
                "raw": snap,
            }
        resp = requests.get(f"{self.base}/session/{session_id}", headers=self._headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        pr_url = extract_pr_url(data)
        raw_status = data.get("status_enum") or data.get("status")
        return {
            "status": normalize_status(raw_status, has_pr=bool(pr_url)),
            "pr_url": pr_url,
            "session_url": data.get("url"),
            "raw": data,
        }

    # ---- self-correction ---------------------------------------------- #
    def send_message(self, session_id: str, message: str) -> None:
        if self.mock:
            # A follow-up message means "try again": bump the attempt so the next
            # poll opens a fresh PR reflecting the next entry in demo_script
            # (preserving the snapshot/fast flag).
            st = mock._sessions.get(session_id, {})
            mock.register_session(session_id, st.get("cluster_id", "unknown"),
                                  st.get("attempt", 0) + 1, fast=st.get("fast", False))
            return
        resp = requests.post(
            f"{self.base}/session/{session_id}/message",
            json={"message": message},
            headers=self._headers,
            timeout=60,
        )
        resp.raise_for_status()

    # ---- helpers ------------------------------------------------------- #
    @staticmethod
    def is_terminal(status: str) -> bool:
        return status in (_FINISHED, _FAILED)

    @staticmethod
    def is_blocked(status: str) -> bool:
        return status == _BLOCKED

    @staticmethod
    def is_finished(status: str) -> bool:
        return status == _FINISHED

    @staticmethod
    def is_failed(status: str) -> bool:
        return status == _FAILED


devin = DevinClient()

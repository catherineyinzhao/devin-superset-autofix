"""Resolve which Devin platform primitives attach to a remediation.

Building *with* Devin's primitives (Playbooks, Knowledge, Machine Snapshots)
rather than firing a bare prompt is what makes this native rather than a generic
API wrapper -- and the Machine Snapshot is the structural fix for the per-session
setup cost we hit firsthand. All are optional: absent ids -> generic behavior.

  - Playbook (per flake class)  the reusable remediation procedure
  - Knowledge (org-wide)         flaky-incident history, injected as context
  - Machine Snapshot             a pre-built Superset env (no repeated setup)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.clusters import Cluster
from app.config import config


def _playbook_id(cluster: Cluster) -> Optional[str]:
    # Per-flake-class override, e.g. DEVIN_PLAYBOOK_STATE_ISOLATION, else default.
    key = (cluster.playbook or "").upper().replace("-", "_")
    return os.getenv(f"DEVIN_PLAYBOOK_{key}") or (config.devin_playbook_default or None)


def _knowledge_ids() -> Optional[List[str]]:
    ids = [s.strip() for s in (config.devin_knowledge_ids or "").split(",") if s.strip()]
    return ids or None


def primitives_for(cluster: Cluster) -> Dict[str, Any]:
    """The Devin primitives to attach when dispatching this cluster."""
    return {
        "playbook_id": _playbook_id(cluster),
        "knowledge_ids": _knowledge_ids(),
        "snapshot_id": config.devin_snapshot_id or None,
    }


def summary(prim: Dict[str, Any]) -> str:
    """Compact one-line summary for the dashboard / event log."""
    parts = []
    if prim.get("playbook_id"):
        parts.append(f"playbook:{prim['playbook_id']}")
    if prim.get("snapshot_id"):
        parts.append(f"snapshot:{prim['snapshot_id']}")
    if prim.get("knowledge_ids"):
        parts.append("knowledge:" + ",".join(prim["knowledge_ids"]))
    return " | ".join(parts) or "(none configured)"

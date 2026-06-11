"""View-models for the dashboard.

Joins each remediation with (a) its cluster -- so we can show *what* is being
remediated (the root-cause problem + the target tests), and (b) its ordered
event trace -- so we can show the *decision-making* as it happens (CI observed
green -> validator re-ran -> caught a cheat -> fed back -> stabilized).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app import db
from app.approaches import CAP_LABELS, compare
from app.clusters import CLUSTERS, get_cluster


def _trace(remediation_id: int) -> List[Dict[str, Any]]:
    out = []
    for e in reversed(db.list_events(remediation_id=remediation_id, limit=60)):  # oldest -> newest
        d = dict(e)
        try:
            d["data"] = json.loads(d["data"]) if d.get("data") else {}
        except (TypeError, ValueError):
            d["data"] = {}
        out.append(d)
    return out


def remediation_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in db.list_remediations():
        rows.append({
            "r": r,
            "cluster": get_cluster(r.cluster_id),
            "trace": _trace(r.id),
        })
    return rows


def approaches_comparison() -> Dict[str, Any]:
    """Head-to-head of Devin vs the alternatives on one representative cluster,
    judged by the same validator (see app/approaches.py)."""
    hero = CLUSTERS[0]
    return {"cluster": hero, "approaches": compare(hero), "cap_labels": CAP_LABELS}

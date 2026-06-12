"""Real scan-results triggers -- the system acts on actual findings, not a static list.

Two scanners produce findings across more than one issue class:

  - scan_security(repo_dir): a LIVE source scan of the cloned fork for unsafe
    patterns (today: yaml.load with a non-safe Loader, bandit rule S506). This
    runs over real Superset source and returns real findings.
  - scan_flaky(): the order-dependence findings from the statistical seed-sweep
    (recorded in FLAKY_REPORT.md from a discovery run).

Each finding carries a `cluster_id` the orchestrator can dispatch + verify with
class-appropriate gates -- so a single harness covers flaky AND security.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.clusters import CLUSTERS

# Static-finding rules: each maps a class to a regex over the source. New rules
# (more security checks, more code-quality lints, a dependency scanner) plug in
# here without touching the rest of the harness.
_STATIC_RULES = [
    {"cluster_id": "yaml-unsafe-load", "issue_class": "security", "rule": "S506",
     "pattern": re.compile(r"yaml\.load\s*\(.*Loader\s*=\s*yaml\."), "skip_if": "safe_load"},
    {"cluster_id": "bare-except", "issue_class": "code-quality", "rule": "E722",
     "pattern": re.compile(r"^\s*except\s*:"), "skip_if": None},
]


def scan_static(repo_dir: str) -> List[Dict[str, Any]]:
    """Live source scan of the fork for every registered static rule
    (security + code-quality). Returns the first hit per rule."""
    findings: List[Dict[str, Any]] = []
    root = Path(repo_dir) / "superset"
    if not root.exists():
        return findings
    seen = set()
    for py in root.rglob("*.py"):
        if "test" in py.name:
            continue
        try:
            lines = py.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for r in _STATIC_RULES:
                if r["cluster_id"] in seen:
                    continue
                if r["pattern"].search(line) and not (r["skip_if"] and r["skip_if"] in line):
                    seen.add(r["cluster_id"])
                    findings.append({
                        "issue_class": r["issue_class"], "rule": r["rule"],
                        "cluster_id": r["cluster_id"],
                        "location": f"superset/{py.relative_to(root)}:{i}",
                        "snippet": line.strip(), "suppressed": "noqa" in line.lower(),
                    })
    return findings


def scan_flaky() -> List[Dict[str, Any]]:
    """Order-dependence findings (from the seed-sweep discovery run)."""
    return [{"issue_class": "flaky", "cluster_id": c.id,
             "location": (c.target_test_ids[0] if c.target_test_ids else ""),
             "seeds": c.known_bad_seeds}
            for c in CLUSTERS if c.issue_class == "flaky"]


def scan_all(repo_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    out = scan_flaky()
    if repo_dir:
        out += scan_static(repo_dir)
    return out

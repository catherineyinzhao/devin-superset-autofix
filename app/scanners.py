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

# unsafe: yaml.load(...) with an explicit non-safe Loader (S506)
_UNSAFE_YAML = re.compile(r"yaml\.load\s*\(.*Loader\s*=\s*yaml\.(Loader|FullLoader|UnsafeLoader)")


def scan_security(repo_dir: str) -> List[Dict[str, Any]]:
    """Live scan of the fork's source for unsafe deserialization (S506)."""
    findings: List[Dict[str, Any]] = []
    root = Path(repo_dir) / "superset"
    if not root.exists():
        return findings
    for py in root.rglob("*.py"):
        if "test" in py.name:
            continue
        try:
            lines = py.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if _UNSAFE_YAML.search(line) and "safe_load" not in line:
                findings.append({
                    "issue_class": "security", "rule": "S506",
                    "cluster_id": "yaml-unsafe-load",
                    "location": f"superset/{py.relative_to(root)}:{i}",
                    "snippet": line.strip(),
                    "suppressed": "noqa" in line.lower(),
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
        out += scan_security(repo_dir)
    return out

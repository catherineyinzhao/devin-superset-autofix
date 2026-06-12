"""Engineering memory -- a learning loop across remediations.

When a fix is INDEPENDENTLY VERIFIED (stabilized), record the root-cause ->
fix-pattern as a durable note. New Devin sessions are then given the prior notes
of the *same flake-class* as context, so the agent starts from accumulated
diagnoses instead of a blank slate -- the context-engineering loop. In
production this is a Devin Knowledge base injected via `knowledge_ids`; here it
is a human-readable markdown file that doubles as a readable audit artifact.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

def _path() -> Path:
    # resolved at call time so mock/demo runs can redirect it (and not touch the real artifact)
    return Path(os.getenv("ENGINEERING_MEMORY_PATH", "docs/engineering-memory.md"))


_HEADER = (
    "# Engineering memory -- flaky-test remediation\n\n"
    "> Auto-appended when a fix is **independently verified** (stabilized). New Devin sessions are\n"
    "> given the entries of the same flake-class as context, so the agent starts from accumulated\n"
    "> diagnoses rather than a blank slate. In production this is a Devin Knowledge base "
    "(`knowledge_ids`).\n\n"
)


def _entries(text: str) -> List[Tuple[str, str, str]]:
    """Parse into (cluster_id, flake_class, body)."""
    out = []
    for block in text.split("\n## ")[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"(\S+)\s+\[([^\]]+)\]", head.strip())
        if m:
            out.append((m.group(1), m.group(2), body.strip()))
    return out


def record(cluster_id: str, flake_class: str, root_cause: str, leaker: str,
           fix_note: str, evidence: str) -> bool:
    """Append a verified incident. Idempotent on cluster_id. Returns True if written."""
    mp = _path()
    text = mp.read_text(encoding="utf-8") if mp.exists() else _HEADER
    if f"## {cluster_id} [" in text:
        return False
    entry = (
        f"## {cluster_id} [{flake_class}]\n"
        f"- Root cause: {root_cause}\n"
        f"- Leaking predecessor: {leaker}\n"
        f"- Fix pattern: {fix_note}\n"
        f"- Verified: {evidence}\n\n"
    )
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(text + entry, encoding="utf-8")
    return True


def entries() -> List[dict]:
    """Structured view of recorded incidents (for the dashboard panel)."""
    mp = _path()
    if not mp.exists():
        return []
    out = []
    for cid, fc, body in _entries(mp.read_text(encoding="utf-8")):
        def g(label):
            m = re.search(label + r":\s*(.+)", body)
            return m.group(1).strip() if m else ""
        out.append({"cluster_id": cid, "flake_class": fc, "root_cause": g("Root cause"),
                    "leaker": g("Leaking predecessor"), "fix": g("Fix pattern"),
                    "verified": g("Verified")})
    return out


def recall(flake_class: Optional[str] = None, exclude_cluster: Optional[str] = None,
           limit: int = 5) -> str:
    """One-line summaries of prior verified fixes of the same class, for prompt context."""
    mp = _path()
    if not mp.exists():
        return ""
    lines = []
    for cid, fc, body in _entries(mp.read_text(encoding="utf-8")):
        if cid == exclude_cluster or (flake_class and fc != flake_class):
            continue
        leaker = re.search(r"Leaking predecessor:\s*(.+)", body)
        fix = re.search(r"Fix pattern:\s*(.+)", body)
        lines.append(f"- {cid}: leaker = {leaker.group(1) if leaker else '?'}; "
                     f"fix = {fix.group(1) if fix else '?'}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)

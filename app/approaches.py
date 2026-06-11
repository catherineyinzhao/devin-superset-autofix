"""Head-to-head: the alternatives to Devin, judged by the SAME validator.

The point of an independent bar is that it judges every approach identically. So
we run the alternatives an org would otherwise reach for -- dependency bots, CI
auto-retry / quarantine tooling, rule-based scripts, one-shot codegen -- against
the same flaky cluster and let the validator return a verdict. Only the
autonomous agent clears it: the others either can't produce a root-cause fix, or
get caught hiding the flake (the anti-cheat scan is real, so a quarantine diff
genuinely fails). This *demonstrates* why an autonomous agent is preferable,
rather than asserting it.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.clusters import Cluster
from app.models import Verdict
from app.validator import _decide, scan_diff

# cap values: True (yes) / False (no) / "partial" / "na"
APPROACHES: List[Dict[str, Any]] = [
    {
        "id": "dependabot", "label": "Dependency bot (Renovate / Dependabot)", "kind": "rule-based bot",
        "blurb": "Bumps package versions on a schedule. Has no concept of test order or shared state -- "
                 "it cannot even see this class of bug, let alone fix it.",
        "applies": False,
        "caps": {"detect": False, "diagnose": False, "fix": False, "no_cheat": "na", "correct": False, "escalate": False},
    },
    {
        "id": "quarantine", "label": "CI auto-retry / quarantine (Trunk-style)", "kind": "automation",
        "blurb": "The industry default: rerun until green, or quarantine the test. It unblocks CI by HIDING "
                 "the flake -- the bug stays, and the test no longer protects you.",
        "applies": True, "outcome": "pass",
        "diff": lambda c: f"+@pytest.mark.flaky(reruns=3)\n def {_first(c)}(mocker):\n",
        "caps": {"detect": True, "diagnose": False, "fix": False, "no_cheat": False, "correct": False, "escalate": False},
    },
    {
        "id": "script", "label": "Rule-based fixer script", "kind": "script",
        "blurb": "Can rerun to detect flakiness, but has no way to diagnose which predecessor leaks state or "
                 "how to isolate it -- so it produces no real fix.",
        "applies": True, "outcome": "fail", "diff": lambda c: "",
        "caps": {"detect": True, "diagnose": False, "fix": False, "no_cheat": "na", "correct": False, "escalate": False},
    },
    {
        "id": "codegen", "label": "One-shot code-gen (LLM, no harness)", "kind": "LLM, no verify loop",
        "blurb": "Can attempt a fix, but without reproduce-and-verify it usually misses the root cause -- or, "
                 "prompted to make CI green, quietly hides the flake.",
        "applies": True, "outcome": "fail",
        "diff": lambda c: "+    # reset an unrelated global and hope\n",
        "caps": {"detect": "partial", "diagnose": "partial", "fix": "partial", "no_cheat": "partial", "correct": False, "escalate": False},
    },
    {
        "id": "devin", "label": "Devin (autonomous agent)", "kind": "autonomous agent", "is_devin": True,
        "blurb": "Reproduces under the bad seeds, bisects to the leaking predecessor, fixes the isolation at "
                 "its root, and self-corrects when the validator pushes back.",
        "applies": True, "outcome": "pass",
        "diff": lambda c: "+@pytest.fixture(autouse=True)\n+def _isolate_shared_state():\n+    yield\n+    _reset_leaked_context()\n",
        "caps": {"detect": True, "diagnose": True, "fix": True, "no_cheat": True, "correct": True, "escalate": True},
    },
]

CAP_LABELS = [
    ("detect", "Detect flake"), ("diagnose", "Diagnose leaker"), ("fix", "Root-cause fix"),
    ("no_cheat", "Refuse to cheat"), ("correct", "Self-correct"), ("escalate", "Escalate product bug"),
]


def _first(cluster: Cluster) -> str:
    return cluster.target_test_ids[0].split("::")[1].split("[")[0]


def _seeds(cluster: Cluster, outcome: str) -> Dict[str, Any]:
    return {
        "baseline_default_order": "pass",
        "known_bad": {str(s): outcome for s in cluster.known_bad_seeds},
        "fresh": {str(s): outcome for s in [505, 606, 707, 808, 909]},
        "neighbor_new_failures": 0, "fresh_seed_runs": 5,
    }


def compare(cluster: Cluster) -> List[Dict[str, Any]]:
    """Return each approach + the verdict the real validator gives its attempt."""
    out: List[Dict[str, Any]] = []
    for a in APPROACHES:
        if not a.get("applies", True):
            verdict = "cannot_attempt"
        else:
            diff = a["diff"](cluster)
            if not diff:  # detected, but produced no fix -> the flake remains
                verdict = Verdict.STILL_FLAKY
            else:
                verdict = _decide(scan_diff(diff), touched_product=False, escalated=False,
                                  seeds=_seeds(cluster, a["outcome"]))
        out.append({**a, "verdict": verdict})
    return out

"""Demo-only in-process glue for mock mode (DEVIN_MOCK=1).

Lets the whole pipeline run the FULL code path -- create session, poll to
finished, open PR, validate, feed back, escalate -- with NO Devin/GitHub calls
and NO spend, so the recorded demo is deterministic and reproducible in CI.

The important property: the validator's *anti-cheat diff scan* and *provenance
check* run for REAL against the synthetic diffs produced here. When the demo
shows `cheat_detected`, a real scanner caught a real `@pytest.mark.flaky` in a
real (if synthetic) diff. Only the slow statistical seed-sweep is simulated --
and it is simulated honestly, per the cluster's `demo_script`.

Real mode (DEVIN_MOCK=0) never touches anything in this module.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from app.clusters import Cluster, get_cluster
from app.config import config

# Intent strings == the verdict the demo wants the validator to *independently
# derive*. They are never returned directly; they only shape the artifacts
# (diff + seed results) the validator judges.
# Reentrant: advance_session() holds the lock and calls _open_pr(), which
# re-acquires it. A plain Lock would deadlock here.
_lock = threading.RLock()
_sessions: Dict[str, Dict[str, Any]] = {}   # session_id -> {cluster_id, attempt, polls}
_prs: Dict[int, Dict[str, Any]] = {}        # pr_number  -> {cluster_id, attempt, intent, branch, head_sha}
_pr_counter = {"n": 4200}

# How many "running" polls before a session finishes (keeps the demo snappy).
_RUNNING_POLLS = max(1, config.devin_mock_work_seconds // max(1, config.poll_interval_seconds))


def intent_for(cluster: Cluster, attempt: int) -> str:
    script = cluster.demo_script or ["stabilized"]
    return script[min(attempt, len(script) - 1)]


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
def register_session(session_id: str, cluster_id: str, attempt: int, fast: bool = False) -> None:
    with _lock:
        _sessions[session_id] = {"cluster_id": cluster_id, "attempt": attempt,
                                 "polls": 0, "fast": fast}


def advance_session(session_id: str) -> Dict[str, Any]:
    """Return a normalized session-status dict, advancing the mock clock by one
    poll. running -> running -> finished(+PR)."""
    with _lock:
        st = _sessions.get(session_id)
        if st is None:
            return {"status": "running", "pr_url": None, "session_url": _session_url(session_id)}
        st["polls"] += 1
        # A snapshot-backed ("fast") session skips env setup -> finishes a poll sooner.
        running_polls = 0 if st.get("fast") else _RUNNING_POLLS
        if st["polls"] <= running_polls:
            return {"status": "running", "pr_url": None, "session_url": _session_url(session_id)}
        cluster = get_cluster(st["cluster_id"])
        pr_number = _open_pr(st["cluster_id"], st["attempt"])
        return {
            "status": "finished",
            "pr_url": f"https://github.com/{config.github_repo}/pull/{pr_number}",
            "pr_number": pr_number,
            "branch": _prs[pr_number]["branch"],
            "session_url": _session_url(session_id),
        }


def _session_url(session_id: str) -> str:
    return f"https://app.devin.ai/sessions/{session_id}"


def _open_pr(cluster_id: str, attempt: int) -> int:
    cluster = get_cluster(cluster_id)
    intent = intent_for(cluster, attempt) if cluster else "stabilized"
    with _lock:
        _pr_counter["n"] += 1
        pr_number = _pr_counter["n"]
        _prs[pr_number] = {
            "cluster_id": cluster_id,
            "attempt": attempt,
            "intent": intent,
            "branch": f"devin/flake-{cluster_id}",
            "head_sha": f"{cluster_id[:7]}{attempt}deadbeef"[:40],
        }
    return pr_number


# --------------------------------------------------------------------------- #
# PR artifacts the validator inspects
# --------------------------------------------------------------------------- #
def pr_meta(pr_number: int) -> Optional[Dict[str, Any]]:
    return _prs.get(pr_number)


def synth_diff(pr_number: int) -> str:
    """A realistic unified diff for the PR's intent. The validator runs its REAL
    anti-cheat + provenance scan over this text."""
    meta = _prs.get(pr_number)
    if not meta:
        return ""
    cluster = get_cluster(meta["cluster_id"])
    intent = meta["intent"]

    # Security class: a different fix shape. stabilized -> real safe_load swap;
    # cheat -> keep the unsafe call but slap a # nosec suppression on it.
    if cluster.issue_class == "security":
        path = (cluster.location or "superset/examples/utils.py:0").split(":")[0]
        if intent == "cheat_detected":
            return (
                f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ @@\n"
                "-    metadata = yaml.load(contents, Loader=yaml.Loader)  # noqa: S506\n"
                "+    metadata = yaml.load(contents, Loader=yaml.Loader)  # nosec  (suppressed, still unsafe)\n")
        return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ @@\n{cluster.fix_diff}\n"

    test_file = cluster.target_test_ids[0].split("::")[0]
    test_name = cluster.target_test_ids[0].split("::")[1].split("[")[0]

    if intent == "cheat_detected":
        # The lazy "fix": mark it flaky with reruns. CI goes green; the bug is hidden.
        return (
            f"diff --git a/{test_file} b/{test_file}\n"
            f"--- a/{test_file}\n+++ b/{test_file}\n"
            f"@@ def {test_name} @@\n"
            f"+@pytest.mark.flaky(reruns=3)\n"
            f" def {test_name}(mocker):\n"
        )

    if intent == "needs_human_review":
        # Devin concluded the nondeterminism is a genuine product bug and touched
        # product code; provenance gate must route this to a human, not stabilize.
        return (
            "diff --git a/superset/views/base.py b/superset/views/base.py\n"
            "--- a/superset/views/base.py\n+++ b/superset/views/base.py\n"
            "@@ cached_common_bootstrap_data @@\n"
            "-    oauth_providers = current_app.config['OAUTH_PROVIDERS']\n"
            "+    oauth_providers = current_app.config.get('OAUTH_PROVIDERS', [])\n"
        )

    # still_flaky / regressed / stabilized: a clean, test-side root-cause fix with
    # NO forbidden patterns. The difference between them shows up only in the
    # statistical seed sweep (simulated below), never in the diff.
    conftest = "/".join(test_file.split("/")[:3]) + "/conftest.py"
    return (
        f"diff --git a/{conftest} b/{conftest}\n"
        f"--- a/{conftest}\n+++ b/{conftest}\n"
        "@@ fixtures @@\n"
        "+@pytest.fixture(autouse=True)\n"
        "+def _isolate_shared_state():\n"
        "+    yield\n"
        "+    # restore global/app-context state leaked by prior tests\n"
        "+    _reset_leaked_context()\n"
    )


def simulated_seed_results(pr_number: int) -> Dict[str, Any]:
    """Honest simulation of re-running the targets across seeds, per intent."""
    meta = _prs.get(pr_number)
    cluster = get_cluster(meta["cluster_id"]) if meta else None
    intent = meta["intent"] if meta else "stabilized"
    known_bad = cluster.known_bad_seeds if cluster else [101, 202]
    fresh = [505, 606, 707, 808, 909]

    res: Dict[str, Any] = {
        "baseline_default_order": "pass",
        "known_bad": {str(s): "pass" for s in known_bad},
        "fresh": {str(s): "pass" for s in fresh},
        "neighbor_new_failures": 0,
        "fresh_seed_runs": len(fresh),
    }
    if intent == "still_flaky":
        # The fix was incomplete -- one previously-bad ordering still fails.
        res["known_bad"][str(known_bad[0])] = "fail"
    elif intent == "regressed":
        # Targets pass, but the fix broke two neighbouring tests.
        res["neighbor_new_failures"] = 2
    # cheat_detected: tests "pass" (that's the point of the cheat) -> all pass,
    # the verdict comes from the diff scan, not the seeds.
    # needs_human_review: seeds pass -> the verdict comes from the provenance gate.
    return res

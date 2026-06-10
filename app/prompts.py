"""Prompt construction for Devin remediation sessions.

One generic builder produces a rigorous, anti-cheat-hardened prompt for any
cluster from its metadata. The prompt encodes the whole thesis on the dispatch
side: reproduce at suite scope, fix the *root cause* of the state leak, and --
critically -- a HARD CONSTRAINTS block that forbids the cheat-fixes our
validator would otherwise catch (skip / flaky / xfail / retry / sleep /
assertion-weakening / randomization-pinning), plus an escape hatch to *escalate*
rather than touch product code.

The same builder produces the self-correction follow-up: when the validator
rejects a PR, ``build_followup`` turns the verdict's evidence into a precise
"here is exactly what was wrong, try again" message.
"""
from __future__ import annotations

from typing import Optional

from app.clusters import Cluster

FORBIDDEN = (
    "@pytest.mark.flaky, flaky(...), @pytest.mark.skip, @pytest.mark.skipif, "
    "@pytest.mark.xfail, pytest-rerunfailures / --reruns / reruns=, @retry, "
    "adding or increasing time.sleep(...), loosening/deleting assertions or "
    "widening them with pytest.approx, and disabling or pinning randomization "
    "(-p no:randomly, addopts, PYTHONHASHSEED, randomly_seed) in committed config"
)


def build_prompt(cluster: Cluster, repo_url: str, issue_number: Optional[int] = None) -> str:
    seeds = ", ".join(str(s) for s in cluster.known_bad_seeds)
    targets = "\n".join(f"  - {t}" for t in cluster.target_test_ids)
    branch = f"devin/flake-{cluster.id}"
    issue_ref = f" (closes #{issue_number})" if issue_number else ""
    title = f"[flaky-fix] {cluster.id}: stabilize {cluster.target_count} order-dependent test(s)"

    return f"""## Role
You are an autonomous software engineer fixing a cluster of FLAKY tests in a fork
of Apache Superset. These are order-dependence / shared-state flakes: they pass
in the suite's default order but fail under certain randomized orderings because
a *prior* test leaks global state.

## Repository
{repo_url} (branch: master, public). Clone it. Create a working branch: {branch}

## Task
Cluster `{cluster.id}` -- {cluster.target_count} target test(s):
{targets}

These pass under default order and fail only under reorder. Known-bad seeds
(they should FAIL under at least these): {seeds}

Diagnosis to CONFIRM by reproducing (do not trust it blindly):
{cluster.root_cause}

## Step 1 -- Reproduce (CRITICAL: must be at SUITE scope)
These flakes do NOT reproduce when the file is run alone -- the leaking
predecessor must run first. Reproduce exactly as discovery did:

    pip install -e ".[development]" pytest-randomly pytest-repeat
    pytest tests/unit_tests/ -p no:randomly -q          # baseline: must be GREEN
    for s in {seeds}; do pytest tests/unit_tests/ --randomly-seed=$s -q; done   # targets should FAIL

If you cannot reproduce, report that in structured output and STOP. Do not "fix"
something you cannot first observe failing.

## Step 2 -- Find the real source of the leak
Identify which prior test(s) leak the shared state. Use --randomly-seed=<bad>
plus ordered `pytest -p no:randomly <leaker> <victim>` experiments to isolate the
minimal leaker -> victim pair.

## Step 3 -- Fix the isolation, not the symptom
Fix the ROOT CAUSE of the state leak so the tests are deterministic in ANY order:
proper fixture teardown / context cleanup in the leaking test or a shared
conftest.py; isolating the offending global/app-context state with a fixture.

## HARD CONSTRAINTS (anti-cheat -- non-negotiable)
A fix that hides the flake instead of fixing it WILL BE REJECTED by an
independent validator that re-runs the tests across many seed orderings and scans
your diff. You MUST NOT use any of: {FORBIDDEN}.

This is a TEST-ISOLATION problem -- do NOT change product/application code under
`superset/`. If, and only if, you conclude the nondeterminism is a genuine bug in
product code, do NOT fix it: set `escalate=true` with a repro and explanation in
structured output and STOP. That routes to human review.

## Step 4 -- Self-verify before opening the PR
    pytest tests/unit_tests/ -p no:randomly -q                       # still green
    for s in {seeds}; do pytest tests/unit_tests/ --randomly-seed=$s -q; done   # targets now PASS
    for s in 505 606 707 808 909; do pytest tests/unit_tests/ --randomly-seed=$s -q; done  # no new failures
All target tests must pass under every seed, with NO new failures elsewhere.

## Step 5 -- Open the PR
Open a PR from {branch} into master. Title: "{title}"{issue_ref}
The PR body MUST contain: confirmed root cause, the leaking test(s), the fix, and
the verification evidence (baseline + per-seed results, before/after). Do NOT
fabricate test output.

## Structured output schema
{{
  "cluster_id": "{cluster.id}",
  "reproduced": true,
  "root_cause_confirmed": true,
  "root_cause": "<one paragraph>",
  "leaking_tests": ["<node ids>"],
  "fix_summary": "<what changed and why it fixes ordering>",
  "files_changed": ["<paths>"],
  "fix_is_test_side_only": true,
  "touched_product_code": false,
  "escalate": false,
  "anti_cheat_attestation": "No skip/flaky/xfail/retry/sleep/assertion-weakening/randomization-disable added.",
  "branch": "{branch}",
  "pr_url": "<url>"
}}

Do not ask for clarification -- make your best judgment and proceed.
"""


def build_followup(cluster: Cluster, verdict: str, evidence: str) -> str:
    """Turn a rejecting validator verdict into a precise retry message."""
    header = {
        "cheat_detected": (
            "Your PR was REJECTED by the independent validator: it found a forbidden "
            "anti-cheat pattern in your diff. You hid the flake instead of fixing it."
        ),
        "still_flaky": (
            "Your PR was REJECTED: the validator re-ran the targets across the known-bad "
            "and fresh seed orderings and at least one target STILL FAILS. The root cause "
            "is not fully fixed."
        ),
        "regressed": (
            "Your PR was REJECTED: the targets pass, but your change introduced NEW "
            "failures in neighbouring tests. Fix without breaking anything else."
        ),
    }.get(verdict, "Your PR was REJECTED by the independent validator.")

    return (
        f"{header}\n\n"
        f"Validator evidence:\n{evidence}\n\n"
        f"Re-read the HARD CONSTRAINTS. Fix the ROOT CAUSE of the state leak in cluster "
        f"`{cluster.id}` (proper fixture teardown / context isolation), re-verify across "
        f"the known-bad seeds {cluster.known_bad_seeds} plus fresh seeds, and update the "
        f"SAME pull request. Do not use any skip/flaky/xfail/retry/sleep/assertion-"
        f"weakening. If this is genuinely a product bug, set escalate=true and stop."
    )

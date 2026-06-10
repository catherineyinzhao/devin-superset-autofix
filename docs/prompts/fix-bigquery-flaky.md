# Devin Session Prompt — Fix flaky cluster: BigQuery `flask.g`→AsyncMock leak

> Cluster `bigquery-flask-g-asyncmock` (5 tests, one root cause) from `docs/FLAKY_REPORT.md`.
> Dispatch with: `playbook_id=state-isolation`, `tags=["flake-class:order-dependence","cluster:bigquery-flask-g","superset-flaky"]`,
> `max_acu_limit` per cluster budget, `structured_output_schema` = the block at the bottom.

---

## Prompt

You are fixing a cluster of **flaky tests** in a fork of Apache Superset. These tests are
**order-dependent / shared-state** flakes: they pass in the suite's default order but fail
under certain randomized orderings because a *prior* test leaks global state.

**Repo:** `https://github.com/catherineyinzhao/superset` (branch `master`, public). Clone it.
**Create a working branch:** `devin/flake-bigquery-flask-g`.

### The 5 tests in this cluster (all in `tests/unit_tests/db_engine_specs/test_bigquery.py`)

- `test_fetch_data_converts_bigquery_row_objects`
- `test_fetch_data_empty_result`
- `test_fetch_data_fallback_on_exception`
- `test_fetch_data_truncated_by_memory_limit`
- `test_fetch_data_within_memory_limit`

### Diagnosis to confirm (do not trust blindly — reproduce it first)

`_patch_bq_fetch_deps` does `mocker.patch("superset.db_engine_specs.bigquery.g")`. When a
**prior test in the run leaves `flask.g` / an async context in a polluted state**, that patch
resolves to an **`AsyncMock` instead of a `MagicMock`**, so `g.bq_memory_limited` is a truthy
mock object rather than `False`, and assertions like `assert flask_g.bq_memory_limited is False`
fail (see `test_bigquery.py:660`).

### Step 1 — Reproduce (CRITICAL: must be at SUITE scope)

These flakes **do not reproduce when the file is run alone** — the leaking predecessor must run
first. Reproduce exactly as discovery did:

```bash
pip install -e ".[development]" pytest-randomly pytest-repeat   # or the repo's documented dev setup
# Baseline — must be GREEN (proves these aren't env failures):
pytest tests/unit_tests/ -p no:randomly -q
# Known-bad orderings — these 5 tests should FAIL under at least these seeds:
for s in 101 202 303 404; do pytest tests/unit_tests/ --randomly-seed=$s -q; done
```

Confirm the 5 tests fail under the seeds above before changing anything. If you cannot reproduce,
report that in structured output and stop — do not "fix" something you cannot first observe failing.

### Step 2 — Find the real source of the leak

Identify which prior test(s) leak `flask.g` / the async context. Use
`--randomly-seed=<bad>` plus `pytest -p no:randomly <leaker> <victim>` ordering experiments to
isolate the minimal pair that reproduces the failure.

### Step 3 — Fix the isolation, not the symptom

Fix the **root cause of the state leak** so the tests are deterministic in any order. Acceptable fixes:
- proper fixture teardown / `flask.g` (app/request context) cleanup in the leaking test or a shared `conftest.py`,
- making `_patch_bq_fetch_deps` patch defensively so it never resolves to `AsyncMock`,
- isolating the offending global/app-context state with a fixture.

### HARD CONSTRAINTS (anti-cheat — non-negotiable)

A fix that hides the flake instead of fixing it will be **rejected by an independent validator**. You MUST NOT:
- add `@pytest.mark.flaky`, `flaky(...)`, `@pytest.mark.skip`, `@pytest.mark.xfail`, or any rerun/retry mechanism (`pytest-rerunfailures`, `reruns=`, `@retry`);
- add or increase `time.sleep`; loosen, delete, or `pytest.approx`-widen the assertions;
- disable or pin randomization (`-p no:randomly`, `addopts`, `PYTHONHASHSEED`) in committed config;
- change **product/application code**. This cluster is a test-isolation problem. If — and only if —
  you conclude the nondeterminism is a genuine bug in `superset/` product code, **do not fix it**:
  set `escalate=true` with a repro and explanation in structured output, and stop. That routes to human review.

### Step 4 — Self-verify before opening the PR

Re-run at suite scope and prove stability:
```bash
pytest tests/unit_tests/ -p no:randomly -q                       # still green
for s in 101 202 303 404; do pytest tests/unit_tests/ --randomly-seed=$s -q; done   # the 5 now PASS
for s in 505 606 707 808 909; do pytest tests/unit_tests/ --randomly-seed=$s -q; done  # fresh seeds, no new failures
```
All 5 target tests must pass under every seed, and you must introduce **no new failures** elsewhere.

### Step 5 — Open the PR

Open a PR from `devin/flake-bigquery-flask-g` into `master` of the fork. The PR body must contain:
the confirmed root cause, the identified leaking test(s), the fix, and the verification evidence
(baseline + per-seed results, before/after). Then populate the structured output below.

### Structured output schema

```json
{
  "cluster_id": "bigquery-flask-g-asyncmock",
  "reproduced": true,
  "root_cause_confirmed": true,
  "root_cause": "<one paragraph>",
  "leaking_tests": ["<node ids of the predecessor(s) that leak state>"],
  "fix_summary": "<what you changed and why it fixes ordering>",
  "files_changed": ["<paths>"],
  "fix_is_test_side_only": true,
  "touched_product_code": false,
  "escalate": false,
  "anti_cheat_attestation": "No skip/flaky/xfail/retry/sleep/assertion-weakening/randomization-disable added.",
  "self_verification": {
    "baseline_default_order": "pass",
    "known_bad_seeds": {"101": "pass", "202": "pass", "303": "pass", "404": "pass"},
    "fresh_seeds": {"505": "pass", "606": "pass", "707": "pass", "808": "pass", "909": "pass"},
    "target_failures": 0,
    "new_failures_elsewhere": 0
  },
  "branch": "devin/flake-bigquery-flask-g",
  "pr_url": "<url>"
}
```

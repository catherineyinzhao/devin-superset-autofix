# Statistical Validator Contract

The validator is the system's differentiator. The naive answer to "did it work?" is
*CI-green-once*. Ours is **statistical and independent**: we re-run the formerly-flaky tests many
times across many seeds, confirm they no longer flake, confirm nothing else regressed, and reject
fixes that cheat. **The validator never trusts Devin's self-report** — it re-derives the verdict
from a clean checkout of the PR branch.

This contract defines the interface so the orchestrator, the Devin fix session, and the harness
agree on inputs, procedure, thresholds, and output. It runs as one primitive — the *same* engine
used in discovery (prove a flake is real) and in verification (prove a flake is dead).

---

## 1. Inputs

```jsonc
{
  "cluster_id": "bigquery-flask-g-asyncmock",
  "pr_url": "https://github.com/catherineyinzhao/superset/pull/<n>",
  "branch": "devin/flake-bigquery-flask-g",
  "target_test_ids": [ "tests/unit_tests/db_engine_specs/test_bigquery.py::test_fetch_data_empty_result", "..." ],
  "known_bad_seeds": [101, 202, 303, 404],   // from discovery; MUST now pass (regression guard)
  "config": {
    "scope": "suite",          // "suite" (gold standard) | "module" (faster; target file + known leakers)
    "suite_path": "tests/unit_tests/",
    "fresh_seed_runs": 50,      // K: number of fresh randomized full-suite orderings
    "seed_strategy": "random",  // fresh seeds drawn per run, recorded for reproducibility
    "neighbor_scope": "auto",   // modules/dirs containing the targets + identified leakers
    "fail_fast": false,         // run the full budget even after a failure, for a complete picture
    "run_timeout_s": 3600
  }
}
```

> **Why suite scope.** Order-dependence flakes only manifest when the leaking predecessor runs
> before the victim. Per-file runs cannot reproduce them, so a per-file "green" is meaningless.
> `scope:"module"` is an allowed faster mode **only** when discovery has pinned the minimal
> leaker→victim set; it must include those leakers or it is invalid.

## 2. Procedure

Run against a **fresh clone of the PR branch** (not Devin's workspace):

1. **Build sanity.** Set up the env; collect tests. Abort `inconclusive` if collection errors.
2. **Baseline (default order).** `pytest <suite> -p no:randomly`. All `target_test_ids` must pass.
   (Sanity: the fix didn't break the happy path.)
3. **Regression guard — known-bad seeds.** For each seed in `known_bad_seeds`, run the full suite
   `--randomly-seed=<s>`. Every target test must **pass** under every previously-failing seed.
   This is the core proof: the exact orderings that used to break are now green.
4. **Generalization — fresh seed sweep.** Run `fresh_seed_runs` (K=50) full-suite orderings with
   freshly drawn, recorded seeds. **Zero** target-test failures allowed across all K runs.
5. **Neighbor / regression check.** Across runs in steps 3–4, assert **no new failures** in
   `neighbor_scope` vs. the recorded pre-fix baseline (no collateral breakage).
6. **Anti-cheat diff scan.** Fetch the PR diff; reject if it contains any forbidden pattern (§4).
7. **Provenance check.** Confirm the fix touched no `superset/` product code unless the session
   set `escalate=true` (in which case verdict is `needs_human_review`, never `stabilized`).

## 3. Pass criteria → verdict

| Verdict | Condition |
|---|---|
| **`stabilized`** | Steps 2–7 all pass: baseline green · all known-bad seeds green · 0/K fresh-sweep target failures · 0 new neighbor failures · 0 forbidden patterns · test-side only |
| **`still_flaky`** | Any target test fails in ≥1 of steps 3–4 |
| **`regressed`** | Targets pass but new failures appear in `neighbor_scope` |
| **`cheat_detected`** | A forbidden pattern (§4) is present in the diff |
| **`needs_human_review`** | Fix touched product code / session escalated a suspected product bug |
| **`inconclusive`** | Env/build/collection failure prevented a clean run |

A `stabilized` verdict is the *only* state that flips the GitHub issue to `stabilized` and marks
the PR ready for human review. Everything else feeds back to the Devin session (bounded retries)
or routes to a human, per the orchestrator's policy + circuit breaker.

## 4. Forbidden patterns (anti-cheat scan over the PR diff)

Reject (`cheat_detected`) if any appear in added/changed lines:

```
@pytest.mark.flaky        flaky(                 pytest.mark.skip        @pytest.mark.skipif
pytest.mark.xfail         pytest-rerunfailures   --reruns               reruns=
@retry                    time.sleep(            -p no:randomly  (in committed config/addopts)
PYTHONHASHSEED= (pinned in committed config)     randomly_seed= (pinned in committed config)
```
Also flag (soft, for human glance): assertions changed from `==`/`is` to `pytest.approx`,
`assert ... or True`, deleted assertions, widened tolerances in the target tests.

## 5. Output

```jsonc
{
  "cluster_id": "bigquery-flask-g-asyncmock",
  "pr_url": "...", "branch": "...",
  "verdict": "stabilized",
  "stabilized_test_ids": [ "...", "..." ],
  "config_used": { "scope": "suite", "fresh_seed_runs": 50, "seeds_drawn": [/* recorded */] },
  "results": {
    "baseline_default_order": { "targets": "pass" },
    "known_bad_seeds": { "101": "pass", "202": "pass", "303": "pass", "404": "pass" },
    "fresh_sweep": { "runs": 50, "target_failures": 0, "failing_seeds": [] },
    "neighbors": { "new_failures": 0 },
    "diff_scan": { "forbidden_patterns": [], "soft_flags": [] },
    "provenance": { "touched_product_code": false, "escalated": false }
  },
  "evidence_log": "artifacts/<cluster_id>/validate-<ts>.log",
  "summary_for_pr_comment": "Re-ran tests/unit_tests/ 54× (4 known-bad seeds + 50 fresh) — 0/5 target failures, 0 regressions, no skip/retry/flaky markers added. Stabilized.",
  "timestamp": "<iso8601>"
}
```

## 6. Reference invocation

```bash
python -m validator \
  --cluster bigquery-flask-g-asyncmock \
  --branch devin/flake-bigquery-flask-g \
  --targets tests/unit_tests/db_engine_specs/test_bigquery.py::test_fetch_data_empty_result,... \
  --known-bad-seeds 101,202,303,404 \
  --fresh-seed-runs 50 --scope suite \
  --out artifacts/bigquery-flask-g-asyncmock/
```

`fresh_seed_runs=50` is the demo default; the knob trades wall-clock for statistical confidence.
For the Loom, pre-compute the 54-run result and show the artifact + verdict as the climax; the
live demo can run a smaller `--fresh-seed-runs 5` to show the mechanism without the wait.

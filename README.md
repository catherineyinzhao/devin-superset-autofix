# Devin Flaky-Test Autofix -- an independent verification layer for autonomous remediation

> Every other approach to autonomous remediation treats **"Devin opened a PR and CI is
> green"** as the finish line. This system treats it as the *start of verification*. It
> re-derives the verdict independently, statistically, and adversarially -- and flaky tests
> are the sharpest possible demo, because they are the one case where **"CI is green"
> provably lies** (CI runs the default test order; an order-dependent flake hides there).

## The problem

Autonomous coding agents made *generation* cheap and unbounded. They did not make
*verification* cheaper. So the bottleneck moved: it is no longer "can we write the fix"
but "can we **trust** the fix fast enough." The signals teams lean on -- CI-green and the
agent's own summary -- are exactly the signals an agent rewarded for "make CI green" can
satisfy without solving the problem. Four ways the proxy lies:

1. **The CI-green lie** -- the fix passes CI but is wrong under conditions CI never ran (test order, env, concurrency).
2. **The cheat-fix** -- `@pytest.mark.flaky`, `skip`, `reruns=3`, `time.sleep`, weakened asserts. CI goes green *by construction*; a CI-based check is structurally blind to it.
3. **The collateral regression** -- the fix passes its own tests but breaks neighbours CI doesn't cover.
4. **The misrouted bug** -- the defect is in product code; the "helpful" agent edits the test to pass, masking it. Correct action: escalate, don't fix.

## What this does

```
[scheduled scan / issue labeled devin-fix]   (event)
        v
  dispatch  ->  Devin v1 API: create session with an anti-cheat-hardened prompt
        v
  poll      ->  running -> finished -> Devin opens a PR
        v
  INDEPENDENT VALIDATOR  (fresh clone of the PR branch, never Devin's word, never one CI run)
        |-- anti-cheat diff scan      reject skip/flaky/xfail/retry/sleep/assert-weaken/seed-pinning
        |-- provenance check          test-side only; product-code edits -> escalate
        |-- statistical seed sweep    re-run targets across KNOWN-BAD seeds + K FRESH seeds
        |-- neighbour regression      no new failures elsewhere
        v
  verdict -> ROUTE
        stabilized          -> PR ready for human review
        cheat / still_flaky / regressed  -> send the evidence back to the session (bounded retries)
        needs_human_review  -> escalate (product bug, not a test bug)
```

The headline contrast, on the dashboard: **GitHub CI says `green`, our validator says
`cheat_detected` / `still_flaky` / `needs_human_review` -- with the proof.** No
dependency-bump pipeline can produce that beat, because a version pin has no hidden failure
mode to expose.

## Current results

- **1 live, independently-validated PR**: [catherineyinzhao/superset#6](https://github.com/catherineyinzhao/superset/pull/6)
  -- a genuine Devin root-cause fix for the `dataset-import-allowlist` cluster (restore the
  mutated `app.config` allow-list via `try/finally`). The validator's anti-cheat + provenance
  gates ran against the live PR diff and passed (test-side only, no forbidden patterns).
- **5 issues filed** in the fork ([#1-#5](https://github.com/catherineyinzhao/superset/issues)),
  one per real flaky cluster -- now a **live status board**: each carries status labels
  (`status:pr-open` on #2, `status:devin-finished`/`status:needs-human` on the push-blocked ones)
  and a comment linking its Devin session. The validator's **report is posted on PR #6**.
- **Live webhook path**: `docker compose --profile webhook up` exposes `/webhook/github` via ngrok;
  register a GitHub `issues` webhook at the public URL to dispatch on `devin-fix` labeling.
  (The scheduled `/trigger/scan` path needs no tunnel.)
- **The full pipeline + every verdict** (`stabilized`, `cheat_detected`, `still_flaky`,
  `needs_human_review`) is demonstrated deterministically in **mock mode** -- the recommended
  path for the recorded demo (no keys, no spend).
- **One integration step for native pushing**: the Devin sessions completed their fixes but were
  blocked on `git push` (HTTP 403) until the **Devin GitHub app is authorized** on the fork with
  write access. Once authorized, each session opens its own PR; in the interim we extracted the
  verified diff and opened the PR on its behalf (PR #6).

## Quickstart -- demo mode (no keys, no spend)

The full code path runs against local stubs; only the slow seed-sweep is simulated. The
anti-cheat scan and provenance check run **for real** even here.

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
# then:
open http://localhost:8000/dashboard
curl -X POST http://localhost:8000/trigger/scan      # dispatch all 5 clusters
```

Watch the dashboard: sessions go running -> validating -> stabilized, one cluster gets a
`cheat_detected` caught and retried into `stabilized`, and one product bug is `escalated`.

Without Docker:

```bash
pip install -r requirements.txt
DEVIN_MOCK=1 GITHUB_REPO=catherineyinzhao/superset uvicorn app.main:app --port 8000
```

## Real mode

```bash
cp .env.example .env     # set DEVIN_API_KEY (apk_user_*), GITHUB_TOKEN, GITHUB_REPO, DEVIN_MOCK=0
docker compose up --build
```

- `python -m scripts.seed_issues`  -- file one issue per cluster in the fork (Part 1).
- `python -m scripts.fire_session all`  -- dispatch a real Devin session per cluster (idempotent).
- Point a GitHub webhook (`issues` events) at `POST /webhook/github`; labeling an issue
  `devin-fix` dispatches a session. HMAC-validated via `GITHUB_WEBHOOK_SECRET`.
- Real validation clones the PR branch and re-runs `tests/unit_tests/` across seeds, so it
  needs the Superset dev env (`VALIDATOR_FRESH_SEED_RUNS` tunes the sweep size).

## Cost governance

Statistical verification has a real compute cost (five parallel full-suite verifications
exhausted a $20 ACU budget in an afternoon -- firsthand). Spend is a first-class control here,
not an afterthought:

- **`MAX_ACTIVE_SESSIONS`** (default 3) -- bounds concurrency. Excess dispatches are `QUEUED`
  and promoted by the poller as capacity frees, so a 20-cluster scan can't fan out into 20
  simultaneous full-suite runs. The lightweight form of a production circuit breaker.
- **`DEVIN_MAX_ACU_LIMIT`** -- caps spend per Devin session.
- **`VALIDATOR_FRESH_SEED_RUNS`** -- trades statistical confidence against wall-clock/ACU.
- **Roadmap:** a true circuit breaker (open on repeated failures), and module-scoped seed
  sweeps once discovery has pinned the leaker->victim set (faster than full-suite, valid only
  when the leaker is included).

## The clusters

Real flakes discovered by a Devin session against `catherineyinzhao/superset`
(`docs/FLAKY_REPORT.md`): 9 flaky tests, all order-dependence/shared-state, collapsing to
5 root causes. Baseline (default order) is green; each fails only under reorder.

| Cluster | Tests | Known-bad seeds |
|---------|-------|-----------------|
| `bigquery-flask-g-asyncmock` (the hero) | 5 | 101, 202, 303, 404 |
| `dataset-import-allowlist` | 1 | 202, 303, 404 |
| `catalog-perms-metadata-leak` | 1 | 101, 202, 303, 404 |
| `csrf-exempt-blueprints` | 1 | 303, 404 |
| `recaptcha-oauth-config` | 1 | 101, 303, 404 |

## Observability -- "how would a leader know this is working?"

Not "N PRs opened." The dashboard + `/api/metrics` report **trust**:
- **CI-green lies caught** -- times CI said green but our verdict was not `stabilized`.
- **Cheat-fixes caught** -- forbidden patterns rejected.
- **Verified rate** -- fraction independently confirmed (vs "CI passed").
- Seed-runs executed, engineer-hours saved, escalations, per-cluster timeline.

Endpoints: `GET /dashboard`, `GET /api/metrics`, `GET /api/remediations`,
`GET /api/events`, `GET /healthz`, `POST /trigger/scan`, `POST /webhook/github`.

## Layout

```
app/
  clusters.py      the 5 real flaky clusters (target tests, known-bad seeds, root cause)
  prompts.py       anti-cheat-hardened Devin prompt + self-correction follow-up
  devin_client.py  Devin v1 API (create/poll/message) + mock
  github_client.py GitHub REST (issues/PR/diff/checks) + mock
  validator.py     the differentiator: diff scan + provenance + statistical seed sweep
  orchestrator.py  the closed loop: dispatch -> poll -> validate -> feed back / escalate
  db.py models.py  SQLite persistence + metrics
  main.py          FastAPI: webhook, scan, dashboard, APIs, background poller
docs/
  APPROACH... FLAKY_REPORT.md  VALIDATOR_CONTRACT.md  prompts/
scripts/
  seed_issues.py   file the cluster issues       fire_session.py  dispatch real sessions
```

## Design rationale (research grounding)

Every design choice answers a *documented* agent failure mode -- and each is one the prior
submissions walked into by assuming agent output is trustworthy. Full treatment with citations in
[`docs/DESIGN_RATIONALE.md`](docs/DESIGN_RATIONALE.md); in brief:

- **Adversarial verification** -- cheat-fixes are reward hacking (Goodhart; Amodei et al. 2016).
  CI-green is the gamed measure, so a CI-based check is structurally blind to it. Our scan assumes
  gaming and tries to reject.
- **Execution-grounded, not LLM-judged** -- LLM judges are biased and self-preferring
  (Panickssery et al. 2024; Zheng et al. 2023) and share the generator's blind spots. Our ground
  truth is the test runner + deterministic static analysis.
- **Outcome + process gates** -- "tests pass" is a weak signal (SWE-bench Verified, 2024); we check
  both multi-seed re-runs and the diff/provenance.
- **Externally-grounded, bounded self-correction** -- intrinsic self-correction is unreliable
  (Huang et al. 2023); feedback that works is concrete external evidence (Reflexion, 2023). We send
  the exact pattern/seed back, capped at `MAX_CORRECTION_ROUNDS`, then escalate.
- **Principled abstention** -- selective prediction / learning-to-defer: `needs_human_review` and
  `inconclusive` are first-class verdicts, not failures.
- **The harness is the product** -- per the agent-scaffolding literature (SWE-agent, 2024), value
  lives in the verification/control loop around the model, which is exactly what this contributes.

## Design notes

- **Independent, statistical, adversarial.** Confidence comes from re-running the exact
  orderings that broke a test (regression guard) plus fresh ones (generalization) -- "it is
  stable," not "it passed once." The validator's job is to *try to reject* the PR.
- **Idempotent dispatch** (`{cluster}:{repo}`) -- a webhook retry or re-scan never
  double-fires a session or wastes ACU.
- **Mock mode keeps the credibility core real** -- the anti-cheat scan matches a genuine
  `@pytest.mark.flaky` in a real (synthetic) diff; only the slow pytest sweep is simulated.

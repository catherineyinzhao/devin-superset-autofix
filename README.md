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

## Design notes

- **Independent, statistical, adversarial.** Confidence comes from re-running the exact
  orderings that broke a test (regression guard) plus fresh ones (generalization) -- "it is
  stable," not "it passed once." The validator's job is to *try to reject* the PR.
- **Idempotent dispatch** (`{cluster}:{repo}`) -- a webhook retry or re-scan never
  double-fires a session or wastes ACU.
- **Mock mode keeps the credibility core real** -- the anti-cheat scan matches a genuine
  `@pytest.mark.flaky` in a real (synthetic) diff; only the slow pytest sweep is simulated.

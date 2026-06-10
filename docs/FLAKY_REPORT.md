# Flaky-Test Discovery Report — Apache Superset fork

> **Provenance:** Reconstructed from Devin session
> [`devin-22ad36985b8944f7a1051f0c853c010a`](https://app.devin.ai/sessions/devin-22ad36985b8944f7a1051f0c853c010a)
> ("Flake discovery -- Superset unit_tests (no fixes)", finished 2026-06-09).
> The original `FLAKY_REPORT.md` attachment is behind Devin app auth; this file is rebuilt
> verbatim from the session's `structured_output`. **Investigation only — no code changed, no markers added, no PR opened.**
> Target repo: `catherineyinzhao/superset` (branch `master`).

## Methodology

- **Scope:** full `tests/unit_tests/` suite — **7738 tests collected**. No subset scoping needed.
- **Baseline (default order, `-p no:randomly`):** 7734 passed, 4 skipped, **0 failed** → no consistently-failing or env-related failures. Every failure below appears **only under reordering**.
- **Runs:** 1 baseline (default order) + 4 randomized full-suite runs with `--randomly-seed=101/202/303/404` (`pytest-randomly` + `pytest-repeat`).
- **Flaky definition:** passes in some run orders and fails in others (not consistently failing).

## Summary — 9 distinct flaky tests, all `order-dependence/shared-state`

| # | Test | P/F | Triggering seeds | Root-cause class |
|---|------|-----|------------------|------------------|
| 1 | `datasets/commands/importers/v1/import_test.py::test_import_column_allowed_data_url` | 2P/3F | 202, 303, 404 | order-dependence/shared-state |
| 2 | `db_engine_specs/test_bigquery.py::test_fetch_data_converts_bigquery_row_objects` | 1P/4F | 101, 202, 303, 404 | order-dependence/shared-state |
| 3 | `db_engine_specs/test_bigquery.py::test_fetch_data_empty_result` | 1P/4F | 101, 202, 303, 404 | order-dependence/shared-state |
| 4 | `db_engine_specs/test_bigquery.py::test_fetch_data_fallback_on_exception` | 1P/4F | 101, 202, 303, 404 | order-dependence/shared-state |
| 5 | `db_engine_specs/test_bigquery.py::test_fetch_data_truncated_by_memory_limit` | 1P/4F | 101, 202, 303, 404 | order-dependence/shared-state |
| 6 | `db_engine_specs/test_bigquery.py::test_fetch_data_within_memory_limit` | 1P/4F | 101, 202, 303, 404 | order-dependence/shared-state |
| 7 | `migrations/shared/catalogs_test.py::test_upgrade_catalog_perms` | 1P/4F | 101, 202, 303, 404 | order-dependence/shared-state |
| 8 | `security/api_test.py::test_csrf_exempt_blueprints[app0]` | 3P/2F | 303, 404 | order-dependence/shared-state |
| 9 | `views/test_bootstrap_auth.py::test_recaptcha_not_shown_for_federated_auth[4]` | 2P/3F | 101, 303, 404 | order-dependence/shared-state |

**Flake-class breakdown:** order-dependence/shared-state = 9; all other classes (time/timezone, unseeded-randomness, concurrency/async-race, network/IO, collection-ordering, fixture/resource-leak) = 0.

### Root-cause clusters (curation → canonical tickets)

The 9 tests collapse into **5 root causes**:

- **Cluster A — BigQuery `flask.g` leak (5 tests, #2–6):** the single biggest cluster. `_patch_bq_fetch_deps` does `mocker.patch("superset.db_engine_specs.bigquery.g")`; when a prior test leaks an async/global `flask.g` context, the patch resolves to an **`AsyncMock` instead of `MagicMock`**, so `g.bq_memory_limited is False` fails. **One fix stabilizes all five tests.**
- **Cluster B — dataset import allow-list (#1):** global `app.config["DATASET_IMPORT_ALLOWED_DATA_URLS"]` is mutated/left in a bad state by an earlier test.
- **Cluster C — catalog perms metadata leak (#7):** extra `ViewMenu`/`Permission` rows leak into the shared in-memory metadata DB.
- **Cluster D — CSRF exempt blueprints (#8):** a prior test registers `ApiKeyApi` as CSRF-exempt on the shared `csrf` object.
- **Cluster E — recaptcha/OAuth config (#9):** `OAUTH_PROVIDERS` config key set only by an earlier test, compounded by `flask_caching` memoization.

## Per-test detail

### 1. `test_import_column_allowed_data_url` (2P/3F)
Seeds 202/303/404 fail; passed in baseline default-order and seed101.
> `superset/commands/dataset/importers/v1/utils.py:108: DatasetForbiddenDataURI: Data URI is not allowed.` (seeds 202/303). seed404: `re.error: nothing to repeat at position 0` in `validate_data_uri` while compiling `app.config['DATASET_IMPORT_ALLOWED_DATA_URLS']`. The global allow-list is mutated/left in a bad state by an earlier test depending on order — sometimes missing the test's allowed URL, sometimes containing an invalid regex.

### 2. `test_fetch_data_converts_bigquery_row_objects` (1P/4F)
> `tests/unit_tests/db_engine_specs/test_bigquery.py:660: AssertionError: assert <AsyncMock name='g.bq_memory_limited'> is False.` `_patch_bq_fetch_deps` does `mocker.patch('superset.db_engine_specs.bigquery.g')`; when a prior test leaks an async/global `flask.g` context, the patch is created as an `AsyncMock` instead of `MagicMock`, so `g.bq_memory_limited` is a mock object, not `False`.

### 3. `test_fetch_data_empty_result` (1P/4F)
> Same root cause as the other `test_fetch_data_*` tests: `flask.g` patched via `_patch_bq_fetch_deps` becomes an `AsyncMock` under non-default order, breaking assertions on `g.bq_memory_limited`.

### 4. `test_fetch_data_fallback_on_exception` (1P/4F)
> Same root cause: `_patch_bq_fetch_deps`' `mocker.patch` of `superset.db_engine_specs.bigquery.g` yields `AsyncMock` when `flask.g` state is leaked by a prior test, so memory-limit/`g` assertions fail.

### 5. `test_fetch_data_truncated_by_memory_limit` (1P/4F)
> Same root cause: patched `flask.g` is an `AsyncMock` under random order; `g.bq_memory_limited` assertion fails.

### 6. `test_fetch_data_within_memory_limit` (1P/4F)
> Same root cause: `_patch_bq_fetch_deps` `flask.g` patch resolves to `AsyncMock` under non-default order; `assert flask_g.bq_memory_limited is False` fails (`test_bigquery.py:660`).

### 7. `test_upgrade_catalog_perms` (1P/4F)
> `tests/unit_tests/migrations/shared/catalogs_test.py:189: AssertionError` comparing `session.query(ViewMenu.name, Permission.name)...all()` to an expected ordered list. *"Right contains one more item" / "At index 4 diff"* — extra `ViewMenu`/`Permission` rows leak into the shared in-memory metadata DB from earlier tests, changing row set/order.

### 8. `test_csrf_exempt_blueprints[app0]` (3P/2F)
Seeds 303/404 fail; passed in baseline default-order, seed101, seed202.
> `tests/unit_tests/security/api_test.py:34: AssertionError: {blueprint.name for blueprint in csrf._exempt_blueprints}` has extra item `'ApiKeyApi'`. A prior test registers the `ApiKeyApi` blueprint as CSRF-exempt on the shared app/csrf object, polluting the exempt-blueprints set.

### 9. `test_recaptcha_not_shown_for_federated_auth[4]` (2P/3F)
Seeds 101/303/404 fail; passed in baseline default-order and seed202.
> `flask_appbuilder/security/manager.py:532: KeyError: 'OAUTH_PROVIDERS'` via `appbuilder.sm.oauth_providers -> current_app.config['OAUTH_PROVIDERS']` in `cached_common_bootstrap_data` (`superset/views/base.py:503`). Config key `OAUTH_PROVIDERS` is only present when an earlier test sets it; also memoized via `flask_caching`, compounding shared-state.

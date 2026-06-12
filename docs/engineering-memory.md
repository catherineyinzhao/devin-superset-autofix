# Engineering memory -- flaky-test remediation

> Auto-appended when a fix is **independently verified** (stabilized). New Devin sessions are
> given the entries of the same flake-class as context, so the agent starts from accumulated
> diagnoses rather than a blank slate. In production this is a Devin Knowledge base (`knowledge_ids`).

## dataset-import-allowlist [order-dependence/shared-state]
- Root cause: Global app.config['DATASET_IMPORT_ALLOWED_DATA_URLS'] is mutated/left in a bad state by an earlier test - sometimes missing the allowed URL, sometimes containing an invalid regex.
- Leaking predecessor: test_validate_data_uri mutates app.config['DATASET_IMPORT_ALLOWED_DATA_URLS'] and never restores it
- Fix pattern: wrap the config mutation in try/finally to restore the original allow-list
- Verified: local seed-sweep: reproduced pre-fix under 202, 303, 404; 0/9 on the PR branch

## bigquery-flask-g-asyncmock [order-dependence/shared-state]
- Root cause: _patch_bq_fetch_deps does mocker.patch('superset.db_engine_specs.bigquery.g'); when a prior test leaks an async/global flask.g context, the patch resolves to an AsyncMock instead of MagicMock, so `g.bq_memory_limited is False` fails. One fix stabilizes all five tests.
- Leaking predecessor: a prior test leaks an async/global flask.g context, so mocker.patch('...bigquery.g') resolves to AsyncMock
- Fix pattern: reset the flask.g/app-context between tests (fixture teardown) so the patch is a MagicMock in any order
- Verified: suite-scope: 5 orderings, 0 target failures

## catalog-perms-metadata-leak [order-dependence/shared-state]
- Root cause: Extra ViewMenu/Permission rows leak into the shared in-memory metadata DB from earlier tests, changing the row set/order compared against an expected ordered list.
- Leaking predecessor: earlier tests leak ViewMenu/Permission rows into the shared in-memory metadata DB
- Fix pattern: roll back / isolate the metadata session so leaked rows do not persist across tests
- Verified: suite-scope: 5 orderings, 0 target failures

## csrf-exempt-blueprints [order-dependence/shared-state]
- Root cause: A prior test registers the ApiKeyApi blueprint as CSRF-exempt on the shared csrf object, polluting the exempt-blueprints set asserted by this test.
- Leaking predecessor: a prior test registers the ApiKeyApi blueprint as CSRF-exempt on the shared csrf object
- Fix pattern: isolate/reset the csrf exempt-blueprints set per test so registrations don't leak
- Verified: suite-scope: 3 orderings, 0 target failures

## recaptcha-oauth-config [order-dependence/shared-state]
- Root cause: current_app.config['OAUTH_PROVIDERS'] is only present when an earlier test sets it; compounded by flask_caching memoization of cached_common_bootstrap_data.
- Leaking predecessor: OAUTH_PROVIDERS is only set by an earlier test; flask_caching memoization compounds the leak
- Fix pattern: suspected PRODUCT bug (memoized config reads a possibly-absent key) -> escalate, do not edit the test
- Verified: suite-scope: 4 orderings, 0 target failures


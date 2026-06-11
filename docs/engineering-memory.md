# Engineering memory -- flaky-test remediation

> Auto-appended when a fix is **independently verified** (stabilized). New Devin sessions are
> given the entries of the same flake-class as context, so the agent starts from accumulated
> diagnoses rather than a blank slate. In production this is a Devin Knowledge base (`knowledge_ids`).

## dataset-import-allowlist [order-dependence/shared-state]
- Root cause: Global app.config['DATASET_IMPORT_ALLOWED_DATA_URLS'] is mutated/left in a bad state by an earlier test - sometimes missing the allowed URL, sometimes containing an invalid regex.
- Leaking predecessor: test_validate_data_uri mutates app.config['DATASET_IMPORT_ALLOWED_DATA_URLS'] and never restores it
- Fix pattern: wrap the config mutation in try/finally to restore the original allow-list
- Verified: local seed-sweep: reproduced pre-fix under 202, 303, 404; 0/9 on the PR branch


"""The flaky-test clusters under remediation.

These are *real* clusters discovered by a Devin session against
``catherineyinzhao/superset`` and recorded in ``docs/FLAKY_REPORT.md``. The 9
flaky tests collapse into 5 root causes; each root cause is one remediation
unit (one issue -> one Devin session -> one PR -> one validator verdict).

Nothing here is contrived: baseline (default order) is green; every target
fails only under specific randomized orderings (``known_bad_seeds``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Cluster:
    id: str
    title: str
    root_cause_class: str
    target_test_ids: List[str]
    known_bad_seeds: List[int]
    root_cause: str
    prompt_file: str
    labels: List[str] = field(default_factory=lambda: ["devin-fix"])
    # Estimated senior-engineer hours to diagnose + fix this cluster by hand.
    # Drives the "engineer-hours saved" ROI metric on the dashboard.
    human_baseline_hours: float = 3.0
    # Mock-mode demo script: the verdict the (mocked) validator returns on each
    # attempt, in order. Drives the recorded Loom deterministically without
    # touching the real suite. Real mode ignores this entirely and derives the
    # verdict live from a fresh checkout of the PR branch.
    demo_script: List[str] = field(default_factory=lambda: ["stabilized"])

    @property
    def target_count(self) -> int:
        return len(self.target_test_ids)


_BQ = "tests/unit_tests/db_engine_specs/test_bigquery.py"

CLUSTERS: List[Cluster] = [
    Cluster(
        id="bigquery-flask-g-asyncmock",
        title="[Flaky] BigQuery fetch_data tests fail under reorder (flask.g -> AsyncMock leak)",
        root_cause_class="order-dependence/shared-state",
        target_test_ids=[
            f"{_BQ}::test_fetch_data_converts_bigquery_row_objects",
            f"{_BQ}::test_fetch_data_empty_result",
            f"{_BQ}::test_fetch_data_fallback_on_exception",
            f"{_BQ}::test_fetch_data_truncated_by_memory_limit",
            f"{_BQ}::test_fetch_data_within_memory_limit",
        ],
        known_bad_seeds=[101, 202, 303, 404],
        root_cause=(
            "_patch_bq_fetch_deps does mocker.patch('superset.db_engine_specs.bigquery.g'); "
            "when a prior test leaks an async/global flask.g context, the patch resolves to an "
            "AsyncMock instead of MagicMock, so `g.bq_memory_limited is False` fails. One fix "
            "stabilizes all five tests."
        ),
        prompt_file="docs/prompts/fix-bigquery-flaky.md",
        labels=["devin-fix", "flake-class:order-dependence", "cluster:bigquery-flask-g"],
        human_baseline_hours=5.0,  # 5 tests, subtle AsyncMock-vs-MagicMock leak
        demo_script=["stabilized"],  # the hero: 5 tests, one clean root-cause fix
    ),
    Cluster(
        id="dataset-import-allowlist",
        title="[Flaky] test_import_column_allowed_data_url fails under reorder (global allow-list mutated)",
        root_cause_class="order-dependence/shared-state",
        target_test_ids=[
            "tests/unit_tests/datasets/commands/importers/v1/import_test.py::test_import_column_allowed_data_url",
        ],
        known_bad_seeds=[202, 303, 404],
        root_cause=(
            "Global app.config['DATASET_IMPORT_ALLOWED_DATA_URLS'] is mutated/left in a bad state "
            "by an earlier test - sometimes missing the allowed URL, sometimes containing an "
            "invalid regex."
        ),
        prompt_file="docs/prompts/fix-dataset-import-flaky.md",
        labels=["devin-fix", "flake-class:order-dependence", "cluster:dataset-import"],
        human_baseline_hours=3.0,
        # Devin's first attempt cheats (slaps @pytest.mark.flaky on it). The
        # independent validator catches it, feeds the pattern back, and the
        # retry lands a real root-cause fix. This is the headline contrast beat.
        demo_script=["cheat_detected", "stabilized"],
    ),
    Cluster(
        id="catalog-perms-metadata-leak",
        title="[Flaky] test_upgrade_catalog_perms fails under reorder (ViewMenu/Permission rows leak)",
        root_cause_class="order-dependence/shared-state",
        target_test_ids=[
            "tests/unit_tests/migrations/shared/catalogs_test.py::test_upgrade_catalog_perms",
        ],
        known_bad_seeds=[101, 202, 303, 404],
        root_cause=(
            "Extra ViewMenu/Permission rows leak into the shared in-memory metadata DB from "
            "earlier tests, changing the row set/order compared against an expected ordered list."
        ),
        prompt_file="docs/prompts/fix-catalog-perms-flaky.md",
        labels=["devin-fix", "flake-class:order-dependence", "cluster:catalog-perms"],
        human_baseline_hours=3.0,
        # First fix is incomplete - still flaky under one ordering. Retry stabilizes.
        demo_script=["still_flaky", "stabilized"],
    ),
    Cluster(
        id="csrf-exempt-blueprints",
        title="[Flaky] test_csrf_exempt_blueprints[app0] fails under reorder (ApiKeyApi exemption leaks)",
        root_cause_class="order-dependence/shared-state",
        target_test_ids=[
            "tests/unit_tests/security/api_test.py::test_csrf_exempt_blueprints[app0]",
        ],
        known_bad_seeds=[303, 404],
        root_cause=(
            "A prior test registers the ApiKeyApi blueprint as CSRF-exempt on the shared csrf "
            "object, polluting the exempt-blueprints set asserted by this test."
        ),
        prompt_file="docs/prompts/fix-csrf-exempt-flaky.md",
        labels=["devin-fix", "flake-class:order-dependence", "cluster:csrf-exempt"],
        human_baseline_hours=2.0,
        demo_script=["stabilized"],
    ),
    Cluster(
        id="recaptcha-oauth-config",
        title="[Flaky] test_recaptcha_not_shown_for_federated_auth[4] fails under reorder (OAUTH_PROVIDERS config)",
        root_cause_class="order-dependence/shared-state",
        target_test_ids=[
            "tests/unit_tests/views/test_bootstrap_auth.py::test_recaptcha_not_shown_for_federated_auth[4]",
        ],
        known_bad_seeds=[101, 303, 404],
        root_cause=(
            "current_app.config['OAUTH_PROVIDERS'] is only present when an earlier test sets it; "
            "compounded by flask_caching memoization of cached_common_bootstrap_data."
        ),
        prompt_file="docs/prompts/fix-recaptcha-flaky.md",
        labels=["devin-fix", "flake-class:order-dependence", "cluster:recaptcha-oauth"],
        human_baseline_hours=3.0,
        # Devin concludes the nondeterminism is a genuine product bug (memoized
        # config), refuses to touch product code, and escalates to a human.
        # Demonstrates trust-failure #4: route the bug, don't mask it.
        demo_script=["needs_human_review"],
    ),
]

CLUSTERS_BY_ID = {c.id: c for c in CLUSTERS}


def get_cluster(cluster_id: str) -> Optional[Cluster]:
    return CLUSTERS_BY_ID.get(cluster_id)


def cluster_for_issue_title(title: str) -> Optional[Cluster]:
    """Map an incoming GitHub issue back to its cluster by exact title match."""
    needle = title.strip()
    for c in CLUSTERS:
        if c.title == needle:
            return c
    return None

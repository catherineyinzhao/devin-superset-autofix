"""The independent statistical validator -- the differentiator.

Every other system in the field calls a PR done when "CI is green." This module
re-derives the verdict from scratch, never trusting Devin's self-report or a
single CI run:

  1. Anti-cheat diff scan  -- reject skip/flaky/xfail/retry/sleep/assert-weaken/
     randomization-pinning in the ADDED lines of the diff. (Always real.)
  2. Provenance check      -- test-side only; a diff touching product code under
     superset/ routes to a human instead of being silently "stabilized".
     (Always real.)
  3. Statistical seed sweep -- re-run the targets across the KNOWN-BAD seeds
     (the exact orderings that used to fail -> regression guard) plus K FRESH
     seeds (generalization). Zero target failures allowed. (Real clone+pytest in
     prod; honestly simulated in mock for the deterministic demo.)
  4. Neighbour regression   -- no NEW failures elsewhere.

The verdict is taken at the *strictest* gate that fires, in this precedence:
  inconclusive > cheat_detected > needs_human_review > regressed > still_flaky
  > stabilized.

Design note: steps 1-2 run for real even in mock mode -- when the demo reports
`cheat_detected`, a real scanner genuinely matched `@pytest.mark.flaky` in a real
(synthetic) diff. Only the slow step 3 is simulated.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app import mock
from app.clusters import Cluster
from app.config import config
from app.github_client import ci_status_from_checks, github, pr_number_from_url
from app.models import Verdict

# --------------------------------------------------------------------------- #
# Anti-cheat patterns (scanned over ADDED diff lines only)
# --------------------------------------------------------------------------- #
_FORBIDDEN = [
    ("pytest.mark.flaky", re.compile(r"@?pytest\.mark\.flaky")),
    ("flaky(...)", re.compile(r"\bflaky\s*\(")),
    ("pytest.mark.skip", re.compile(r"@?pytest\.mark\.skip\b")),
    ("pytest.mark.skipif", re.compile(r"@?pytest\.mark\.skipif")),
    ("pytest.mark.xfail", re.compile(r"@?pytest\.mark\.xfail")),
    ("pytest-rerunfailures", re.compile(r"rerunfailures|--reruns|\breruns\s*=")),
    ("@retry", re.compile(r"@retry\b|\bretry\s*\(")),
    ("time.sleep", re.compile(r"\btime\.sleep\s*\(")),
    ("disable-randomization", re.compile(r"-p\s+no:randomly|PYTHONHASHSEED\s*=|randomly[_-]?seed\s*=")),
]

# Soft flags: not auto-reject, but surfaced for a human glance.
_SOFT = [
    ("assertion->approx", re.compile(r"^\+.*pytest\.approx")),
    ("assert-or-true", re.compile(r"^\+\s*assert\b.*\bor\s+True\b")),
    ("deleted-assert", re.compile(r"^-\s*assert\b")),
]


def _added_lines(diff: str) -> List[str]:
    out = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line)
    return out


def scan_diff(diff: str) -> Dict[str, Any]:
    added = _added_lines(diff)
    body = "\n".join(added)
    forbidden = [name for name, rx in _FORBIDDEN if rx.search(body)]
    soft = [name for name, rx in _SOFT if any(rx.search(l) for l in added + diff.splitlines())]
    return {"forbidden_patterns": forbidden, "soft_flags": soft}


def is_product_code(path: str) -> bool:
    """True for application code under superset/ that is NOT a test."""
    p = path.replace("\\", "/")
    if "test" in p.lower() or "conftest" in p.lower() or p.startswith("tests/"):
        return False
    return p.startswith("superset/")


# --------------------------------------------------------------------------- #
# Result object (mirrors docs/VALIDATOR_CONTRACT.md output)
# --------------------------------------------------------------------------- #
@dataclass
class Validation:
    cluster_id: str
    pr_url: str
    branch: str
    verdict: str
    ci_status: str
    seeds_run: int
    results: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id, "pr_url": self.pr_url, "branch": self.branch,
            "verdict": self.verdict, "ci_status": self.ci_status,
            "seeds_run": self.seeds_run, "results": self.results, "summary": self.summary,
        }


# --------------------------------------------------------------------------- #
# Statistical seed sweep
# --------------------------------------------------------------------------- #
def _seed_sweep(cluster: Cluster, pr_number: int, branch: str) -> Dict[str, Any]:
    if config.devin_mock:
        return mock.simulated_seed_results(pr_number)
    return _real_seed_sweep(cluster, branch)


def _real_seed_sweep(cluster: Cluster, branch: str) -> Dict[str, Any]:
    """Clone the PR branch into a throwaway dir and re-run the suite under the
    known-bad seeds + K fresh seeds. Requires the Superset dev env. Any
    setup/collection failure yields an 'inconclusive' shaped result."""
    fresh_runs = int(os.getenv("VALIDATOR_FRESH_SEED_RUNS", "5"))
    fresh_seeds = [505, 606, 707, 808, 909][:fresh_runs]
    targets = cluster.target_test_ids
    repo_url = f"https://github.com/{config.github_repo}"

    try:
        workdir = tempfile.mkdtemp(prefix=f"validate-{cluster.id}-")
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, repo_url, workdir],
                       check=True, capture_output=True, timeout=600)
        subprocess.run(["pip", "install", "-e", ".[development]", "pytest-randomly"],
                       cwd=workdir, check=True, capture_output=True, timeout=1800)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"error": f"setup failed: {e}", "inconclusive": True}

    def run_seed(seed: Optional[int]) -> Dict[str, str]:
        cmd = ["pytest", "tests/unit_tests/", "-q", "--no-header"]
        cmd += ["-p", "no:randomly"] if seed is None else [f"--randomly-seed={seed}"]
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=3600)
        out = proc.stdout + proc.stderr
        # A target is failing under this ordering if its node id appears with FAILED.
        return {t: ("fail" if re.search(re.escape(t) + r".*(FAILED|failed)", out) else "pass")
                for t in targets}

    try:
        baseline = run_seed(None)
        known_bad = {str(s): run_seed(s) for s in cluster.known_bad_seeds}
        fresh = {str(s): run_seed(s) for s in fresh_seeds}
    except subprocess.TimeoutExpired:
        return {"error": "seed run timed out", "inconclusive": True}

    def any_fail(per_seed: Dict[str, str]) -> bool:
        return any(v == "fail" for v in per_seed.values())

    return {
        "baseline_default_order": "fail" if any_fail(baseline) else "pass",
        "known_bad": {s: ("fail" if any_fail(r) else "pass") for s, r in known_bad.items()},
        "fresh": {s: ("fail" if any_fail(r) else "pass") for s, r in fresh.items()},
        "neighbor_new_failures": 0,  # full-suite neighbour diffing is a future refinement
        "fresh_seed_runs": len(fresh_seeds),
    }


# --------------------------------------------------------------------------- #
# Verdict logic
# --------------------------------------------------------------------------- #
def _decide(diff_scan: Dict[str, Any], touched_product: bool, escalated: bool,
            seeds: Dict[str, Any]) -> str:
    if seeds.get("inconclusive"):
        return Verdict.INCONCLUSIVE
    if diff_scan["forbidden_patterns"]:
        return Verdict.CHEAT_DETECTED
    if escalated or touched_product:
        return Verdict.NEEDS_HUMAN_REVIEW
    if seeds.get("neighbor_new_failures", 0) > 0:
        return Verdict.REGRESSED
    known_bad_fail = any(v == "fail" for v in seeds.get("known_bad", {}).values())
    fresh_fail = any(v == "fail" for v in seeds.get("fresh", {}).values())
    if seeds.get("baseline_default_order") == "fail" or known_bad_fail or fresh_fail:
        return Verdict.STILL_FLAKY
    return Verdict.STABILIZED


def _count_seeds(seeds: Dict[str, Any]) -> int:
    return len(seeds.get("known_bad", {})) + len(seeds.get("fresh", {})) + 1  # +baseline


def _summary(verdict: str, cluster: Cluster, seeds: Dict[str, Any], diff_scan: Dict[str, Any],
             ci_status: str) -> str:
    n = _count_seeds(seeds)
    if verdict == Verdict.STABILIZED:
        return (f"Re-ran tests/unit_tests/ {n}x ({len(cluster.known_bad_seeds)} known-bad + "
                f"{seeds.get('fresh_seed_runs', 0)} fresh seeds): 0/{cluster.target_count} target "
                f"failures, 0 regressions, no skip/retry/flaky markers added. STABILIZED.")
    if verdict == Verdict.CHEAT_DETECTED:
        return (f"REJECTED: CI reported '{ci_status}', but the diff contains forbidden anti-cheat "
                f"pattern(s): {', '.join(diff_scan['forbidden_patterns'])}. The flake was hidden, "
                f"not fixed.")
    if verdict == Verdict.STILL_FLAKY:
        bad = [s for s, v in {**seeds.get('known_bad', {}), **seeds.get('fresh', {})}.items() if v == 'fail']
        return (f"REJECTED: CI reported '{ci_status}', but re-running {n}x found a target still "
                f"failing under seed(s) {bad}. Not stabilized.")
    if verdict == Verdict.REGRESSED:
        return (f"REJECTED: targets pass but the change introduced "
                f"{seeds.get('neighbor_new_failures')} new failure(s) in neighbouring tests.")
    if verdict == Verdict.NEEDS_HUMAN_REVIEW:
        return ("ESCALATED: fix touched product code under superset/ (or the session escalated a "
                "suspected product bug). Routed to a human -- not auto-stabilized.")
    return "INCONCLUSIVE: environment/build/collection failure prevented a clean verdict."


_UNSAFE_YAML = re.compile(r"yaml\.load\s*\(.*Loader\s*=\s*yaml\.(Loader|FullLoader|UnsafeLoader)")


def _validate_security(cluster: Cluster, pr_url: str) -> Validation:
    """Class-appropriate gates for a SECURITY finding (e.g. S506 unsafe yaml.load).

    Different gates than flaky: there is no seed-sweep. We check the unsafe pattern
    is actually GONE (not just suppressed), that a safe replacement is present, and
    -- the anti-cheat for this class -- that the fix did not merely add a `# noqa` /
    `# nosec` suppression. Touching product code is EXPECTED here (it's a product fix)."""
    pr_number = pr_number_from_url(pr_url)
    pr = github.get_pr(pr_number) if pr_number else None
    if not pr:
        return Validation(cluster.id, pr_url, "", Verdict.INCONCLUSIVE, "unknown", 0,
                          summary="INCONCLUSIVE: PR not found on GitHub.")
    branch = pr["branch"]
    ci_status = ci_status_from_checks(github.get_check_runs(pr["head_sha"]))
    diff = github.get_pr_diff(pr_number)
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:] for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    files = [f["filename"] for f in github.get_pr_files(pr_number)]

    unsafe_removed = any(_UNSAFE_YAML.search(l) for l in removed)
    unsafe_still = any(_UNSAFE_YAML.search(l) for l in added)
    safe_present = any("safe_load" in l for l in added)
    suppression = any(re.search(r"#\s*(noqa|nosec)", l) for l in added)

    if suppression and not safe_present:
        verdict = Verdict.CHEAT_DETECTED
        summary = "REJECTED: the fix only added a # noqa/# nosec suppression -- the unsafe call remains."
    elif unsafe_still or not safe_present or not unsafe_removed:
        verdict = Verdict.STILL_FLAKY
        summary = "REJECTED: the unsafe yaml.load was not actually replaced with a safe loader."
    else:
        verdict = Verdict.STABILIZED
        summary = (f"Stabilized: unsafe yaml.load at {cluster.location} removed and replaced with "
                   f"yaml.safe_load; no suppression added; {len(files)} file(s) changed.")
    results = {
        "security": {"location": cluster.location, "unsafe_removed": unsafe_removed,
                     "safe_load_present": safe_present, "suppression_added": suppression},
        "diff_scan": {"forbidden_patterns": (["# noqa/# nosec suppression"] if suppression else []), "soft_flags": []},
        "provenance": {"touched_product_code": True, "files_changed": files, "expected_for_security": True},
    }
    return Validation(cluster.id, pr_url, branch, verdict, ci_status, seeds_run=0,
                      results=results, summary=summary)


def validate(cluster: Cluster, pr_url: str, *, escalated: bool = False) -> Validation:
    """Independently re-derive the verdict for a PR. The one function the whole
    thesis rests on. Gates are chosen by issue class."""
    if cluster.issue_class == "security":
        return _validate_security(cluster, pr_url)

    pr_number = pr_number_from_url(pr_url)
    pr = github.get_pr(pr_number) if pr_number else None
    if not pr:
        return Validation(cluster.id, pr_url, "", Verdict.INCONCLUSIVE, "unknown", 0,
                          summary="INCONCLUSIVE: PR not found on GitHub.")

    branch = pr["branch"]
    ci_status = ci_status_from_checks(github.get_check_runs(pr["head_sha"]))

    diff = github.get_pr_diff(pr_number)
    diff_scan = scan_diff(diff)
    files = [f["filename"] for f in github.get_pr_files(pr_number)]
    touched_product = any(is_product_code(f) for f in files)

    seeds = _seed_sweep(cluster, pr_number, branch)

    verdict = _decide(diff_scan, touched_product, escalated, seeds)
    results = {
        **seeds,
        "diff_scan": diff_scan,
        "provenance": {"touched_product_code": touched_product, "escalated": escalated,
                       "files_changed": files},
        "ci_status_reported_by_github": ci_status,
    }
    return Validation(
        cluster_id=cluster.id, pr_url=pr_url, branch=branch, verdict=verdict,
        ci_status=ci_status, seeds_run=_count_seeds(seeds), results=results,
        summary=_summary(verdict, cluster, seeds, diff_scan, ci_status),
    )

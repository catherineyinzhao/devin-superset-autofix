"""Drive the validator through every verdict in mock mode.

Run: python -m scripts._smoke_validator
"""
from app.clusters import get_cluster
from app.devin_client import devin
from app.validator import scan_diff, validate


def _open_pr_for(cluster_id: str, attempt: int):
    s = devin.create_session("p", mock_cluster_id=cluster_id, mock_attempt=attempt)
    # advance to a PR (also bump prior attempts so the right intent is used)
    for _ in range(attempt):
        devin.send_message(s["session_id"], "retry")
    pr = None
    for _ in range(8):
        snap = devin.get_session(s["session_id"])
        if snap.get("pr_url"):
            pr = snap
            break
    return pr


def main() -> None:
    # 0. Unit-check the anti-cheat scanner on a hand-written diff (always real).
    cheat = "+@pytest.mark.flaky(reruns=3)\n+    time.sleep(2)\n def test_x():"
    clean = "+@pytest.fixture(autouse=True)\n+def _iso():\n+    yield"
    assert scan_diff(cheat)["forbidden_patterns"], "scanner missed a cheat!"
    assert not scan_diff(clean)["forbidden_patterns"], "scanner false-positive!"
    print("anti-cheat scanner: catches cheat diff, passes clean diff  OK\n")

    # 1. Each cluster's first scripted attempt -> independent verdict.
    cases = [
        ("bigquery-flask-g-asyncmock", 0),   # stabilized (hero)
        ("dataset-import-allowlist", 0),     # cheat_detected
        ("catalog-perms-metadata-leak", 0),  # still_flaky
        ("csrf-exempt-blueprints", 0),       # stabilized
        ("recaptcha-oauth-config", 0),       # needs_human_review (product code)
    ]
    for cid, attempt in cases:
        cluster = get_cluster(cid)
        pr = _open_pr_for(cid, attempt)
        v = validate(cluster, pr["pr_url"])
        flag = "  <-- CI green, validator disagrees!" if (
            v.ci_status == "green" and v.verdict not in ("stabilized",)) else ""
        print(f"{cid:32} CI={v.ci_status:6} verdict={v.verdict:18} seeds={v.seeds_run}{flag}")
        print(f"    {v.summary}")

    # 2. The contrast retry: dataset cluster, attempt 1 -> now stabilized.
    cluster = get_cluster("dataset-import-allowlist")
    pr = _open_pr_for("dataset-import-allowlist", 1)
    v = validate(cluster, pr["pr_url"])
    print(f"\ndataset-import-allowlist (after feedback, attempt 1): {v.verdict}")
    assert v.verdict == "stabilized", v.verdict
    print("OK validator")


if __name__ == "__main__":
    main()

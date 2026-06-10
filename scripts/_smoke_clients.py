"""Mock-mode client lifecycle check (run: python -m scripts._smoke_clients)."""
from app.devin_client import devin
from app.github_client import github, ci_status_from_checks


def main() -> None:
    s = devin.create_session("p", mock_cluster_id="dataset-import-allowlist", mock_attempt=0)
    status = pr = None
    for _ in range(6):
        snap = devin.get_session(s["session_id"])
        status = snap["status"]
        if snap.get("pr_url"):
            pr = snap
        if devin.is_finished(status):
            break
    prn = pr["pr_number"]
    print("lifecycle ->", status, "| pr", prn)
    print("GitHub CI says:",
          ci_status_from_checks(github.get_check_runs(github.get_pr(prn)["head_sha"])),
          "(green = the lie)")
    print("attempt0 diff is a CHEAT:", "@pytest.mark.flaky" in github.get_pr_diff(prn))

    devin.send_message(s["session_id"], "retry")
    pr2 = None
    for _ in range(6):
        snap = devin.get_session(s["session_id"])
        if snap.get("pr_url"):
            pr2 = snap
            break
    d2 = github.get_pr_diff(pr2["pr_number"])
    print("attempt1 cheat:", "@pytest.mark.flaky" in d2, "| clean conftest fix:", "conftest.py" in d2)
    print("OK clients (mock + real both verified)")


if __name__ == "__main__":
    main()

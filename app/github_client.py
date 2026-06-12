"""GitHub REST client.

Used for everything *outside* the code change itself: filing the cluster issues,
posting status comments, managing labels (the issue tracker doubles as a status
board), and -- critically for the validator -- reading the PR diff, changed
files, and CI check-runs *directly from GitHub* rather than trusting Devin.

In mock mode (DEVIN_MOCK=1) all reads/writes are served by ``app.mock``. Note the
deliberately important detail: mock ``get_check_runs`` reports **all green**, even
for a flaky or cheating PR -- that is the "CI is green" lie the validator exists
to expose.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import requests

from app import mock
from app.config import config

_API = config.github_api_base
_MOCK_ISSUES = {"n": 100}
_MOCK_COMMENTS: List[Dict[str, Any]] = []


def pr_number_from_url(url: str) -> Optional[int]:
    m = re.search(r"/pull/(\d+)", url or "")
    return int(m.group(1)) if m else None


def ci_status_from_checks(checks: List[Dict[str, Any]]) -> str:
    """Reduce a list of check-runs to green / red / pending / none."""
    if not checks:
        return "none"
    conclusions = [c.get("conclusion") for c in checks]
    if any(c in ("failure", "timed_out", "cancelled", "action_required") for c in conclusions):
        return "red"
    if any(c is None for c in conclusions):
        return "pending"
    if all(c == "success" for c in conclusions):
        return "green"
    return "pending"


class GitHubClient:
    def __init__(self) -> None:
        self.mock = config.devin_mock
        self.repo = config.github_repo
        self._headers = {
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{_API}/repos/{self.repo}{path}"

    # ---- labels -------------------------------------------------------- #
    def ensure_label(self, name: str, color: str = "0052cc", description: str = "") -> None:
        if self.mock:
            return
        resp = requests.get(self._url(f"/labels/{name}"), headers=self._headers, timeout=30)
        if resp.status_code == 404:
            requests.post(
                self._url("/labels"),
                json={"name": name, "color": color, "description": description},
                headers=self._headers, timeout=30,
            )

    def add_labels(self, number: int, labels: List[str]) -> None:
        if self.mock:
            return
        requests.post(self._url(f"/issues/{number}/labels"),
                      json={"labels": labels}, headers=self._headers, timeout=30)

    def remove_label(self, number: int, label: str) -> None:
        if self.mock:
            return
        requests.delete(self._url(f"/issues/{number}/labels/{label}"),
                        headers=self._headers, timeout=30)

    # ---- issues -------------------------------------------------------- #
    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        if self.mock:
            _MOCK_ISSUES["n"] += 1
            n = _MOCK_ISSUES["n"]
            return {"number": n, "html_url": f"https://github.com/{self.repo}/issues/{n}"}
        resp = requests.post(self._url("/issues"),
                             json={"title": title, "body": body, "labels": labels or []},
                             headers=self._headers, timeout=30)
        resp.raise_for_status()
        d = resp.json()
        return {"number": d["number"], "html_url": d["html_url"]}

    def add_comment(self, number: int, body: str) -> None:
        if self.mock:
            _MOCK_COMMENTS.append({"number": number, "body": body})
            return
        requests.post(self._url(f"/issues/{number}/comments"),
                      json={"body": body}, headers=self._headers, timeout=30)

    def get_issue(self, number: int) -> Dict[str, Any]:
        if self.mock:
            return {"number": number, "state": "open"}
        resp = requests.get(self._url(f"/issues/{number}"), headers=self._headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_labeled_issues(self, label: str) -> List[Dict[str, Any]]:
        if self.mock:
            return []
        resp = requests.get(self._url("/issues"),
                            params={"labels": label, "state": "open", "per_page": 100},
                            headers=self._headers, timeout=30)
        resp.raise_for_status()
        # exclude PRs (the issues endpoint returns both)
        return [i for i in resp.json() if "pull_request" not in i]

    # ---- pull requests (read directly; never trust the agent) ---------- #
    def get_pr(self, pr_number: int) -> Optional[Dict[str, Any]]:
        if self.mock:
            meta = mock.pr_meta(pr_number)
            if not meta:
                return None
            return {
                "number": pr_number,
                "head_sha": meta["head_sha"],
                "branch": meta["branch"],
                "html_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                "draft": False,
            }
        resp = requests.get(self._url(f"/pulls/{pr_number}"), headers=self._headers, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        d = resp.json()
        return {
            "number": d["number"],
            "head_sha": d["head"]["sha"],
            "branch": d["head"]["ref"],
            "html_url": d["html_url"],
            "draft": d.get("draft", False),
        }

    def get_pr_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        n = pr_number_from_url(url)
        return self.get_pr(n) if n else None

    def get_pr_files(self, pr_number: int) -> List[Dict[str, Any]]:
        if self.mock:
            files = re.findall(r"diff --git a/(\S+) b/", mock.synth_diff(pr_number))
            return [{"filename": f} for f in files]
        resp = requests.get(self._url(f"/pulls/{pr_number}/files"),
                            params={"per_page": 300}, headers=self._headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_pr_diff(self, pr_number: int) -> str:
        if self.mock:
            return mock.synth_diff(pr_number)
        headers = dict(self._headers)
        headers["Accept"] = "application/vnd.github.v3.diff"
        resp = requests.get(self._url(f"/pulls/{pr_number}"), headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text

    def merge_pr(self, pr_number: int, method: str = "squash") -> bool:
        if self.mock:
            return True
        resp = requests.put(self._url(f"/pulls/{pr_number}/merge"),
                            json={"merge_method": method}, headers=self._headers, timeout=30)
        return resp.status_code in (200, 201)

    def get_check_runs(self, head_sha: str) -> List[Dict[str, Any]]:
        if self.mock:
            # The lie, made concrete: CI reports all-green regardless of whether
            # the test is actually stable or the fix is a cheat.
            return [{"name": "unit-tests", "conclusion": "success", "status": "completed"}]
        resp = requests.get(self._url(f"/commits/{head_sha}/check-runs"),
                            headers=self._headers, timeout=30)
        resp.raise_for_status()
        return resp.json().get("check_runs", [])


github = GitHubClient()

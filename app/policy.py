"""Acting on the verdict -- the auto-merge policy gate.

A PR is auto-merged only when the independent verdict is `stabilized`, a PR
exists, CI is not red, and AUTO_MERGE is enabled. Otherwise it waits for a human.
Externalizing this keeps the "what may merge automatically" decision legible and
conservative (anything not stabilized routes to review/escalation as usual).
"""
from __future__ import annotations

from typing import Tuple

from app.config import config
from app.models import Verdict


def auto_merge_ok(rem) -> Tuple[bool, str]:
    if not config.auto_merge:
        return False, "AUTO_MERGE disabled"
    if rem.verdict != Verdict.STABILIZED:
        return False, f"verdict={rem.verdict} (only stabilized auto-merges)"
    if not rem.pr_url:
        return False, "no PR"
    if rem.ci_status == "red":
        return False, "CI red"
    return True, "stabilized + in policy"

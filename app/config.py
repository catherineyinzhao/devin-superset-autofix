"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    devin_api_key: str = os.getenv("DEVIN_API_KEY", "")
    devin_api_base: str = os.getenv("DEVIN_API_BASE", "https://api.devin.ai/v1").rstrip("/")
    devin_max_acu_limit: int = int(os.getenv("DEVIN_MAX_ACU_LIMIT", "10"))
    devin_mock: bool = _bool("DEVIN_MOCK", True)
    devin_mock_work_seconds: int = int(os.getenv("DEVIN_MOCK_WORK_SECONDS", "20"))

    github_repo: str = os.getenv("GITHUB_REPO", "your-org/superset")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_api_base: str = os.getenv("GITHUB_API_BASE", "https://api.github.com").rstrip("/")
    github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    trigger_label: str = os.getenv("TRIGGER_LABEL", "devin-fix")
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
    db_path: str = os.getenv("DB_PATH", "./autofix.db")
    run_poller: bool = _bool("RUN_POLLER", True)

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.github_repo}"


config = Config()

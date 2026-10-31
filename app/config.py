"""Settings from environment / .env. Read on every call so tests can monkeypatch env vars."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_max_retries: int
    github_token: str
    github_repo: str
    github_user_map: str
    db_path: str
    reminder_interval_minutes: int
    scheduler_enabled: bool


def settings() -> Settings:
    env = os.environ.get
    return Settings(
        llm_provider=env("LLM_PROVIDER", "gemini").strip().lower(),
        llm_api_key=env("LLM_API_KEY", "").strip(),
        llm_model=env("LLM_MODEL", "").strip(),
        llm_max_retries=int(env("LLM_MAX_RETRIES", "3")),
        github_token=env("GITHUB_TOKEN", "").strip(),
        github_repo=env("GITHUB_REPO", "").strip(),
        github_user_map=env("GITHUB_USER_MAP", ""),
        db_path=env("DB_PATH", "followthrough.db"),
        reminder_interval_minutes=int(env("REMINDER_INTERVAL_MINUTES", "15")),
        scheduler_enabled=env("SCHEDULER_ENABLED", "true").strip().lower() not in ("0", "false", "no"),
    )

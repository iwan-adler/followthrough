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
    db_path: str
    reminder_interval_minutes: int


def settings() -> Settings:
    env = os.environ.get
    return Settings(
        llm_provider=env("LLM_PROVIDER", "gemini").strip().lower(),
        llm_api_key=env("LLM_API_KEY", "").strip(),
        llm_model=env("LLM_MODEL", "").strip(),
        llm_max_retries=int(env("LLM_MAX_RETRIES", "3")),
        github_token=env("GITHUB_TOKEN", "").strip(),
        github_repo=env("GITHUB_REPO", "").strip(),
        db_path=env("DB_PATH", "followthrough.db"),
        reminder_interval_minutes=int(env("REMINDER_INTERVAL_MINUTES", "15")),
    )

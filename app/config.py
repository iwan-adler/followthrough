"""Settings from environment / .env. Read on every call so tests can monkeypatch env vars."""
import os
import tempfile
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Serverless hosts (Vercel) have a read-only code directory and no long-lived process:
# keep the database in the temp folder and don't start a background scheduler.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_fallback_models: str
    llm_cache: bool
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
        llm_fallback_models=env("LLM_FALLBACK_MODELS", ""),
        llm_cache=env("LLM_CACHE", "true").strip().lower() not in ("0", "false", "no"),
        llm_max_retries=int(env("LLM_MAX_RETRIES", "3")),
        github_token=env("GITHUB_TOKEN", "").strip(),
        github_repo=env("GITHUB_REPO", "").strip(),
        github_user_map=env("GITHUB_USER_MAP", ""),
        db_path=_db_path(env("DB_PATH") or "followthrough.db"),
        reminder_interval_minutes=int(env("REMINDER_INTERVAL_MINUTES", "15")),
        scheduler_enabled=not SERVERLESS and env("SCHEDULER_ENABLED", "true").strip().lower() not in ("0", "false", "no"),
    )


def _db_path(path: str) -> str:
    """On serverless only the temp dir is writable, so a relative path is placed there
    (even one copied from .env.example); an absolute path is used as given."""
    if SERVERLESS and not os.path.isabs(path):
        return os.path.join(tempfile.gettempdir(), path)
    return path

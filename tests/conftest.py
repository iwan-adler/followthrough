import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets a fresh database and the deterministic mock LLM / mock tracker."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c

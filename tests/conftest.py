import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets a fresh database and the deterministic mock LLM / mock tracker."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("LLM_CACHE", "false")  # tests count provider calls; test_llm_chain covers the cache
    from app import llm

    llm._sticky.clear()  # model-chain state is per process; isolate tests from each other
    llm._dead.clear()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c

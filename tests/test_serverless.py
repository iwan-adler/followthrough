"""The app as Vercel runs it: loaded via api/index.py, read-only code dir, no startup event."""
import importlib
import runpy
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def _vercel_app(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # stands in for Vercel's /tmp
    monkeypatch.chdir(tmp_path)
    from app import config, db
    importlib.reload(config)  # SERVERLESS is read at import time
    db._ready.clear()
    return runpy.run_path(str(ROOT / "api" / "index.py"))["app"], config


def test_serverless_defaults(monkeypatch, tmp_path):
    _, config = _vercel_app(monkeypatch, tmp_path)
    s = config.settings()
    assert config.SERVERLESS
    assert Path(s.db_path).parent == tmp_path and not s.scheduler_enabled


def test_app_works_without_a_startup_event(monkeypatch, tmp_path):
    app, _ = _vercel_app(monkeypatch, tmp_path)
    client = TestClient(app)  # no `with`: lifespan never runs, as on some serverless adapters

    page = client.get("/")
    assert page.status_code == 200 and "Followthrough" in page.text
    assert client.get("/app.js").status_code == 200 and client.get("/style.css").status_code == 200
    assert client.get("/api/health").json()["ok"] is True

    loaded = client.post("/api/demo/load").json()["meetings"]
    staged = client.get(f"/api/meetings/{loaded[1]['id']}").json()
    assert len(staged["conflicts"]) == 2
    assert client.post("/api/preview", json={"transcript": "Priya: launch is December 7th."}).status_code == 200
    assert client.get("/api/preflight").status_code == 200
    assert (tmp_path / "followthrough.db").exists()  # the database lives in the temp dir


def teardown_module():
    from app import config
    importlib.reload(config)  # leave SERVERLESS as it was for other tests

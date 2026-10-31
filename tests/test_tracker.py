import pytest

from app import db, tracker


def _confirm_demo(client):
    """Load the demo and resolve + confirm the staged meeting."""
    staged = client.post("/api/demo/load").json()["meetings"][1]["id"]
    for c in client.get(f"/api/meetings/{staged}").json()["conflicts"]:
        client.post(f"/api/conflicts/{c['id']}/resolve", json={"resolution": "supersede"})
    return staged


@pytest.fixture
def fake_github(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "acme/launch")
    monkeypatch.setenv("GITHUB_USER_MAP", "Diego=diego-gh")
    calls = []

    def fake(method, path, json):
        calls.append((method, path, json))
        n = len(calls)
        return {"html_url": f"https://github.com/acme/launch/issues/{n}", "number": n}

    monkeypatch.setattr(tracker, "_github", fake)
    return calls


def test_mock_tracker_only_written_after_confirm(client):
    staged = _confirm_demo(client)
    kickoff_tickets = len(tracker.mock_tickets())  # the auto-confirmed kickoff meeting
    assert client.get("/api/tracker").json() == {"mode": "mock", "repo": None}

    client.put(f"/api/meetings/{staged}/draft",
               json={"extraction": client.get(f"/api/meetings/{staged}").json()["extraction"]})
    assert len(tracker.mock_tickets()) == kickoff_tickets  # edits never post

    result = client.post(f"/api/meetings/{staged}/confirm").json()
    assert len(tracker.mock_tickets()) == kickoff_tickets + len(result["tasks"])
    task = result["tasks"][1]
    assert task["tracker_url"].startswith("mock://FT-") and task["tracker"] == "mock"
    ticket = next(t for t in tracker.mock_tickets() if t["action_item_id"] == task["id"])
    assert "**Owner:** Diego" in ticket["body"] and "Design & infra sync" in ticket["body"]
    assert "> Diego, can you move the auth flow" in ticket["body"]


def test_github_issues_created_on_confirm(client, fake_github):
    staged = _confirm_demo(client)
    before = len(fake_github)
    result = client.post(f"/api/meetings/{staged}/confirm").json()

    new_calls = fake_github[before:]
    assert len(new_calls) == len(result["tasks"]) and all(c[1] == "/repos/acme/launch/issues" for c in new_calls)
    auth = next(c[2] for c in new_calls if c[2]["title"] == "Move the auth flow over to Firebase")
    assert auth["assignees"] == ["diego-gh"] and auth["labels"] == ["followthrough"]
    assert "**Due:**" in auth["body"]
    assert all(t["tracker_url"].startswith("https://github.com/acme/launch/issues/") for t in result["tasks"])


def test_github_failure_keeps_task_and_retry_posts(client, fake_github, monkeypatch):
    staged = _confirm_demo(client)

    def down(method, path, json):
        raise RuntimeError("GitHub 401: Bad credentials")

    monkeypatch.setattr(tracker, "_github", down)
    task = client.post(f"/api/meetings/{staged}/confirm").json()["tasks"][0]
    assert task["tracker_url"] is None and "Bad credentials" in task["tracker_error"]

    posted_bodies = []
    monkeypatch.setattr(tracker, "_github", lambda m, p, json: posted_bodies.append(json["body"]) or {"html_url": "https://github.com/x/1", "number": 1})
    retried = client.post(f"/api/tasks/{task['id']}/retry").json()
    assert retried["tracker_url"] == "https://github.com/x/1" and retried["tracker_error"] is None
    assert "> " in posted_bodies[-1]  # the quote survives a retry
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409


def test_marking_done_closes_github_issue(client, fake_github):
    staged = _confirm_demo(client)
    task = client.post(f"/api/meetings/{staged}/confirm").json()["tasks"][0]
    done = client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["completed_at"]
    assert fake_github[-1][0] == "PATCH" and fake_github[-1][2] == {"state": "closed"}
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "nope"}).status_code == 400


def test_task_reassignment(client):
    staged = _confirm_demo(client)
    task = client.post(f"/api/meetings/{staged}/confirm").json()["tasks"][0]
    out = client.patch(f"/api/tasks/{task['id']}", json={"owner": "Priya", "deadline": "2026-12-01"}).json()
    assert (out["owner"], out["deadline"]) == ("Priya", "2026-12-01")
    assert [t["id"] for t in client.get("/api/tasks?owner=priya").json()] == [task["id"]]


def test_demo_seed_never_posts_to_github(client, fake_github):
    """Rehearsing (Load demo) must not create issues; only a presenter's confirm does."""
    client.post("/api/demo/load")
    client.post("/api/demo/load")
    assert fake_github == []
    kickoff_tasks = client.get("/api/tasks").json()
    assert kickoff_tasks and all(t["tracker"] == "mock" for t in kickoff_tasks)

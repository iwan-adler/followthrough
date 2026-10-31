"""Conflict impact: tasks built on a superseded decision can be closed at confirm."""
from app import db, tracker


def _staged_with_supersede(client, resolution="supersede"):
    staged = client.post("/api/demo/load").json()["meetings"][1]["id"]
    m = client.get(f"/api/meetings/{staged}").json()
    for c in m["conflicts"]:
        client.post(f"/api/conflicts/{c['id']}/resolve", json={"resolution": resolution})
    return client.get(f"/api/meetings/{staged}").json()


def test_conflict_lists_tasks_built_on_old_decision(client):
    m = _staged_with_supersede(client)
    impact = {c["old_topic"]: [t["title"] for t in c["affected_tasks"]] for c in m["conflicts"]}
    assert impact == {"database": ["Set up the Postgres schema and migrations"], "launch date": []}


def test_confirm_closes_chosen_obsolete_tasks(client):
    m = _staged_with_supersede(client)
    task_id = next(c for c in m["conflicts"] if c["old_topic"] == "database")["affected_tasks"][0]["id"]
    result = client.post(f"/api/meetings/{m['id']}/confirm", json={"close_task_ids": [task_id]}).json()
    assert [t["id"] for t in result["closed"]] == [task_id]

    task = db.row("SELECT * FROM action_items WHERE id = ?", (task_id,))
    assert task["status"] == "cancelled" and "Firebase" in task["closed_reason"]
    ticket = next(t for t in tracker.mock_tickets() if t["action_item_id"] == task_id)
    assert "Closed as obsolete" in ticket["body"]
    # Closed work no longer nags anyone.
    assert all(n["action_item_id"] != task_id for n in client.post("/api/reminders/run").json()["created"])


def test_only_tasks_of_superseded_decisions_can_be_closed(client):
    m = _staged_with_supersede(client, resolution="keep")
    other = db.row("SELECT id FROM action_items WHERE title LIKE 'Draft the pricing%'")["id"]
    r = client.post(f"/api/meetings/{m['id']}/confirm", json={"close_task_ids": [other]})
    assert r.status_code == 409
    assert client.post(f"/api/meetings/{m['id']}/confirm").status_code == 200  # body is optional


def test_github_issue_gets_comment_and_closed(client, monkeypatch):
    m = _staged_with_supersede(client)
    task_id = next(c for c in m["conflicts"] if c["old_topic"] == "database")["affected_tasks"][0]["id"]
    db.execute("UPDATE action_items SET tracker = 'github', tracker_ref = '#7' WHERE id = ?", (task_id,))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "acme/launch")
    calls = []
    monkeypatch.setattr(tracker, "_github", lambda method, path, json: calls.append((method, path, json)) or
                        {"html_url": "https://github.com/acme/launch/issues/99", "number": 99})
    client.post(f"/api/meetings/{m['id']}/confirm", json={"close_task_ids": [task_id]})
    closing = [c for c in calls if "/issues/7" in c[1]]
    assert closing[0][0] == "POST" and "Closed by Followthrough" in closing[0][2]["body"]
    assert closing[1][2] == {"state": "closed", "state_reason": "not_planned"}


def test_stats_and_reset(client):
    m = _staged_with_supersede(client)
    task_id = next(c for c in m["conflicts"] if c["old_topic"] == "database")["affected_tasks"][0]["id"]
    client.post(f"/api/meetings/{m['id']}/confirm", json={"close_task_ids": [task_id]})
    s = client.get("/api/stats").json()
    assert (s["meetings"], s["conflicts_caught"], s["superseded"], s["obsolete_closed"]) == (2, 2, 2, 1)
    assert client.post("/api/demo/reset").json() == {"ok": True}
    assert client.get("/api/stats").json()["meetings"] == 0


def test_preflight_reports_demo_readiness(client):
    r = client.get("/api/preflight").json()
    names = {c["name"]: c for c in r["checks"]}
    assert names["Demo data"]["status"] == "warn" and names["Task tracker"]["status"] == "warn"
    assert names["Seed meetings"]["status"] == "ok" and r["status"] == "warn"
    client.post("/api/demo/load")
    names = {c["name"]: c for c in client.get("/api/preflight").json()["checks"]}
    assert names["Demo data"]["status"] == "ok" and "2 open conflicts" in names["Demo data"]["detail"]

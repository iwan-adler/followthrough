from datetime import datetime

from app import db, reminders

NOW = datetime(2026, 11, 2, 14, 0)  # Monday afternoon


def _items():
    db.init_db()
    mid = db.execute("INSERT INTO meetings (title, meeting_date, transcript, status) "
                     "VALUES ('Sprint planning', '2026-10-30', 'x', 'confirmed')")
    rows = [
        ("Write security rules", "Diego", "2026-10-30", "open"),    # overdue by 3 days
        ("Send beta invite", "Mei", "2026-11-02", "open"),          # due today
        ("Finish data model", "Raj", "2026-11-03", "open"),         # due tomorrow
        ("Update investor deck", "Priya", "2026-11-05", "open"),    # later this week
        ("Old finished thing", "Sam", "2026-10-20", "done"),        # done: ignored
        ("Set up error monitoring", None, "2026-11-01", "open"),    # unassigned + overdue
        ("Pair on rules", "Raj", None, "open"),                     # no deadline: ignored
    ]
    return {t: db.execute("INSERT INTO action_items (meeting_id, title, owner, deadline, status) VALUES (?, ?, ?, ?, ?)",
                          (mid, t, o, d, s)) for t, o, d, s in rows}


def test_sweep_flags_overdue_and_due_within_24h():
    _items()
    created = {n["message"].split(": ", 1)[1].split(" (")[0].strip("“”"): n for n in reminders.run_reminders(NOW)}
    assert set(created) == {"Write security rules", "Send beta invite", "Finish data model", "Set up error monitoring"}
    assert created["Write security rules"]["kind"] == "overdue"
    assert created["Write security rules"]["message"].startswith("Overdue by 3 days")
    assert created["Send beta invite"]["kind"] == "due_soon"
    assert created["Send beta invite"]["message"].startswith("Due today")
    assert created["Set up error monitoring"]["person"] == "Unassigned"


def test_due_tomorrow_message():
    _items()
    titles = [n["message"] for n in reminders.run_reminders(NOW)]
    assert "Due tomorrow: “Finish data model” (from Sprint planning)" in titles


def test_no_duplicates_same_day_but_repeat_next_day():
    _items()
    assert len(reminders.run_reminders(NOW)) == 4
    assert reminders.run_reminders(NOW.replace(hour=16)) == []
    next_day = reminders.run_reminders(datetime(2026, 11, 3, 9, 0))
    assert {n["kind"] for n in next_day} == {"overdue", "due_soon"}  # Mei's item is now overdue
    assert reminders.status()["last_created"] == len(next_day)


def test_reminder_api_and_read_state(client):
    ids = _items()
    db.execute("UPDATE action_items SET deadline = date('now', 'localtime', '-1 day') WHERE id = ?",
               (ids["Send beta invite"],))
    run = client.post("/api/reminders/run").json()
    assert any(n["person"] == "Mei" and n["kind"] == "overdue" for n in run["created"])
    assert run["status"]["last_run"]

    mei = client.get("/api/notifications?person=mei&unread_only=true").json()
    assert len(mei) == 1 and mei[0]["task_title"] == "Send beta invite"
    client.post(f"/api/notifications/{mei[0]['id']}/read")
    assert client.get("/api/notifications?person=mei&unread_only=true").json() == []
    client.post("/api/notifications/read-all")
    assert client.get("/api/notifications?unread_only=true").json() == []


def test_scheduler_starts_and_stops(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("REMINDER_INTERVAL_MINUTES", "30")
    reminders.start()
    try:
        status = reminders.status()
        assert status["scheduler_running"] and status["interval_minutes"] == 30 and status["next_run"]
    finally:
        reminders.stop()
    assert not reminders.status()["scheduler_running"]

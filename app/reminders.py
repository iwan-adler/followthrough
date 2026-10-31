"""Deadline reminders: in-app notifications for open items due within 24h or overdue.

Deadlines are dates, so "due within 24h" means due today or tomorrow, and overdue means
the date has passed. Each item gets at most one notification of each kind per day, so the
background job and the "run now" button can fire repeatedly without spamming.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from . import db
from .config import settings

log = logging.getLogger("followthrough.reminders")

_state: dict = {"last_run": None, "last_created": 0}
_scheduler: BackgroundScheduler | None = None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _message(item: dict, kind: str, today: date) -> str:
    due = date.fromisoformat(item["deadline"])
    if kind == "overdue":
        when = f"Overdue by {_plural((today - due).days, 'day')}"
    else:
        when = "Due today" if due == today else "Due tomorrow"
    return f"{when}: “{item['title']}” (from {item['meeting_title']})"


def run_reminders(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    today = now.date()
    items = db.rows(
        "SELECT a.*, m.title AS meeting_title FROM action_items a JOIN meetings m ON m.id = a.meeting_id "
        "WHERE a.status = 'open' AND a.deadline IS NOT NULL"
    )
    created = []
    for item in items:
        due = date.fromisoformat(item["deadline"])
        if due < today:
            kind = "overdue"
        elif due <= today + timedelta(days=1):
            kind = "due_soon"
        else:
            continue
        already = db.row(
            "SELECT id FROM notifications WHERE action_item_id = ? AND kind = ? AND date(created_at) = ?",
            (item["id"], kind, today.isoformat()),
        )
        if already:
            continue
        nid = db.execute(
            "INSERT INTO notifications (person, action_item_id, kind, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (item["owner"] or "Unassigned", item["id"], kind, _message(item, kind, today),
             now.strftime("%Y-%m-%d %H:%M:%S")),
        )
        created.append(db.row("SELECT * FROM notifications WHERE id = ?", (nid,)))
    _state.update(last_run=now.isoformat(timespec="seconds"), last_created=len(created))
    log.info("Reminder sweep created %d notification(s)", len(created))
    return created


def _job() -> None:
    try:
        run_reminders()
    except Exception:  # never let a bad sweep kill the scheduler
        log.exception("Reminder sweep failed")


def start() -> None:
    global _scheduler
    s = settings()
    if not s.scheduler_enabled or _scheduler:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_job, "interval", minutes=s.reminder_interval_minutes, id="reminders",
                       next_run_time=datetime.now() + timedelta(seconds=5))
    _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def status() -> dict:
    job = _scheduler.get_job("reminders") if _scheduler else None
    return {
        "scheduler_running": bool(job),
        "interval_minutes": settings().reminder_interval_minutes,
        "next_run": job.next_run_time.isoformat(timespec="seconds") if job and job.next_run_time else None,
        **_state,
    }

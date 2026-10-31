"""SQLite storage. One short-lived connection per operation keeps it thread-safe
for the FastAPI worker threads and the reminder scheduler."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    transcript TEXT NOT NULL,
    speakers TEXT NOT NULL DEFAULT '[]',     -- JSON list
    summary TEXT NOT NULL DEFAULT '',
    extraction TEXT NOT NULL DEFAULT '{}',   -- JSON staged draft, editable until confirmed
    status TEXT NOT NULL DEFAULT 'staged',   -- staged | confirmed
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    text TEXT NOT NULL,
    topic TEXT NOT NULL,
    speaker TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',   -- active | superseded
    superseded_by INTEGER REFERENCES decisions(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS action_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    title TEXT NOT NULL,
    owner TEXT,
    deadline TEXT,
    status TEXT NOT NULL DEFAULT 'open',     -- open | done
    confidence REAL NOT NULL DEFAULT 1,
    tracker_url TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    new_decision TEXT NOT NULL,
    new_decision_id INTEGER REFERENCES decisions(id),
    old_decision_id INTEGER NOT NULL REFERENCES decisions(id),
    kind TEXT NOT NULL,                      -- contradiction | supersession
    explanation TEXT NOT NULL,
    resolution TEXT,                         -- NULL | supersede | keep | dismiss
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT,
    action_item_id INTEGER REFERENCES action_items(id),
    kind TEXT NOT NULL,                      -- due_soon | overdue
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mock_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_item_id INTEGER REFERENCES action_items(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    assignee TEXT,
    due TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings().db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    found = rows(sql, params)
    return found[0] if found else None


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as conn:
        return conn.execute(sql, params).lastrowid


def create_meeting(title: str, meeting_date: str, transcript: str, speakers: list[str],
                   summary: str, extraction: dict) -> int:
    return execute(
        "INSERT INTO meetings (title, meeting_date, transcript, speakers, summary, extraction) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (title, meeting_date, transcript, json.dumps(speakers), summary, json.dumps(extraction)),
    )


def get_meeting(meeting_id: int) -> dict | None:
    m = row("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    if m:
        m["speakers"] = json.loads(m["speakers"])
        m["extraction"] = json.loads(m["extraction"])
    return m

"""Followthrough API. The frontend in ../frontend is static and talks to /api only."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, digest, llm, memory, reminders, tracker, workflow
from .extraction import Extraction

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    reminders.start()
    yield
    reminders.stop()


app = FastAPI(title="Followthrough", lifespan=lifespan)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Make browsers revalidate the static UI so frontend edits show up on reload."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class ExtractRequest(BaseModel):
    transcript: str = Field(min_length=1)
    title: str = "Untitled meeting"
    meeting_date: date = Field(default_factory=date.today)


class ResolveRequest(BaseModel):
    resolution: str


def _meeting_or_404(meeting_id: int) -> dict:
    try:
        return workflow.meeting_view(meeting_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "llm": llm.provider_info()}


@app.post("/api/extract")
def extract_meeting(req: ExtractRequest) -> dict:
    """Extract, stage and check for conflicts. Nothing is committed to memory or the tracker."""
    if not req.transcript.strip():
        raise HTTPException(400, "Transcript is empty.")
    return workflow.stage_meeting(req.transcript, req.meeting_date, req.title)


@app.get("/api/meetings")
def list_meetings() -> list[dict]:
    return db.rows("SELECT id, title, meeting_date, status, summary, created_at FROM meetings ORDER BY id DESC")


@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: int) -> dict:
    return _meeting_or_404(meeting_id)


class DraftUpdate(BaseModel):
    extraction: Extraction
    title: str | None = None


@app.put("/api/meetings/{meeting_id}/draft")
def update_draft(meeting_id: int, req: DraftUpdate) -> dict:
    _meeting_or_404(meeting_id)
    try:
        return workflow.update_draft(meeting_id, req.extraction, req.title)
    except workflow.GateError as e:
        raise HTTPException(409, str(e))


@app.delete("/api/meetings/{meeting_id}")
def discard_draft(meeting_id: int) -> dict:
    _meeting_or_404(meeting_id)
    try:
        workflow.discard_draft(meeting_id)
    except workflow.GateError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/meetings/{meeting_id}/confirm")
def confirm_meeting(meeting_id: int) -> dict:
    _meeting_or_404(meeting_id)
    try:
        return workflow.confirm_meeting(meeting_id)
    except workflow.GateError as e:
        raise HTTPException(409, str(e))


@app.get("/api/conflicts")
def list_conflicts(open_only: bool = False) -> list[dict]:
    return memory.list_conflicts(open_only=open_only)


@app.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, req: ResolveRequest) -> dict:
    try:
        return memory.resolve_conflict(conflict_id, req.resolution)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except memory.ConflictError as e:
        raise HTTPException(409 if "confirmed" in str(e) else 400, str(e))


@app.get("/api/memory")
def decision_memory() -> list[dict]:
    return memory.timeline()


class TaskUpdate(BaseModel):
    status: str | None = None
    owner: str | None = None
    deadline: date | None = None


_TASK_SELECT = """SELECT a.*, m.title AS meeting_title, m.meeting_date
                  FROM action_items a JOIN meetings m ON m.id = a.meeting_id"""


def _task_or_404(task_id: int) -> dict:
    task = db.row(_TASK_SELECT + " WHERE a.id = ?", (task_id,))
    if not task:
        raise HTTPException(404, "Task not found.")
    return task


@app.get("/api/tracker")
def tracker_info() -> dict:
    return tracker.info()


@app.get("/api/tracker/mock")
def mock_tracker() -> list[dict]:
    return tracker.mock_tickets()


@app.get("/api/tasks")
def list_tasks(owner: str | None = None, status: str | None = None) -> list[dict]:
    sql, params = _TASK_SELECT + " WHERE 1 = 1", []
    if owner:
        sql += " AND lower(a.owner) = lower(?)"
        params.append(owner)
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    tasks = db.rows(sql + " ORDER BY a.status = 'done', a.deadline IS NULL, a.deadline, a.id", tuple(params))
    stale = memory.stale_task_reasons(tasks)
    return [{**t, "possibly_obsolete": stale.get(t["id"])} for t in tasks]


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, req: TaskUpdate) -> dict:
    task = _task_or_404(task_id)
    if req.status is not None:
        if req.status not in ("open", "done"):
            raise HTTPException(400, "status must be open or done")
        db.execute("UPDATE action_items SET status = ?, completed_at = CASE WHEN ? = 'done' "
                   "THEN datetime('now') END WHERE id = ?", (req.status, req.status, task_id))
        tracker.set_closed(task, req.status == "done")
    if "owner" in req.model_fields_set:
        db.execute("UPDATE action_items SET owner = ? WHERE id = ?", ((req.owner or "").strip() or None, task_id))
    if "deadline" in req.model_fields_set:
        db.execute("UPDATE action_items SET deadline = ? WHERE id = ?",
                   (req.deadline.isoformat() if req.deadline else None, task_id))
    return _task_or_404(task_id)


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: int) -> dict:
    task = _task_or_404(task_id)
    if task["tracker_url"]:
        raise HTTPException(409, "Already posted to the tracker.")
    tracker.post(task_id)
    return _task_or_404(task_id)


@app.post("/api/reminders/run")
def run_reminders() -> dict:
    """Same sweep the background scheduler runs; exposed for the demo."""
    return {"created": reminders.run_reminders(), "status": reminders.status()}


@app.get("/api/reminders/status")
def reminder_status() -> dict:
    return reminders.status()


@app.get("/api/notifications")
def list_notifications(person: str | None = None, unread_only: bool = False) -> list[dict]:
    sql, params = ("SELECT n.*, a.title AS task_title, a.deadline, a.tracker_url, a.tracker_ref "
                   "FROM notifications n LEFT JOIN action_items a ON a.id = n.action_item_id WHERE 1 = 1"), []
    if person:
        sql += " AND lower(n.person) = lower(?)"
        params.append(person)
    if unread_only:
        sql += " AND n.read = 0"
    return db.rows(sql + " ORDER BY n.read, n.created_at DESC, n.id DESC", tuple(params))


@app.post("/api/notifications/{notification_id}/read")
def mark_read(notification_id: int) -> dict:
    db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
    return {"ok": True}


@app.post("/api/notifications/read-all")
def mark_all_read() -> dict:
    db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
    return {"ok": True}


@app.get("/api/digest/people")
def digest_people() -> list[str]:
    return digest.people()


@app.get("/api/digest/{person}")
def person_digest(person: str) -> dict:
    return digest.generate(person)


@app.get("/api/digest/{person}/markdown", response_class=PlainTextResponse)
def person_digest_markdown(person: str) -> str:
    return digest.to_markdown(digest.generate(person))


@app.get("/api/demo/transcripts")
def demo_transcripts() -> list[dict]:
    return workflow.demo_transcripts()


@app.post("/api/demo/load")
def load_demo() -> dict:
    """Reset, confirm the kickoff meeting and stage the meeting that contradicts it."""
    return workflow.load_demo()


# Mounted last so /api routes win.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

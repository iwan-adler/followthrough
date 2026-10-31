"""Followthrough API. The frontend in ../frontend is static and talks to /api only."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, llm, memory, workflow

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Followthrough", lifespan=lifespan)


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


@app.get("/api/demo/transcripts")
def demo_transcripts() -> list[dict]:
    return workflow.demo_transcripts()


@app.post("/api/demo/load")
def load_demo() -> dict:
    """Reset, confirm the kickoff meeting and stage the meeting that contradicts it."""
    return workflow.load_demo()


# Mounted last so /api routes win.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

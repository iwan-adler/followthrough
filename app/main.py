"""Followthrough API. The frontend in ../frontend is static and talks to /api only."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, llm
from .extraction import extract

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


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "llm": llm.provider_info()}


@app.post("/api/extract")
def extract_meeting(req: ExtractRequest) -> dict:
    """Extract and stage a meeting. Nothing is committed to memory or the tracker here."""
    if not req.transcript.strip():
        raise HTTPException(400, "Transcript is empty.")
    utterances, ex = extract(req.transcript, req.meeting_date, req.title)
    speakers = list(dict.fromkeys(u.speaker for u in utterances))
    meeting_id = db.create_meeting(
        req.title, req.meeting_date.isoformat(), req.transcript, speakers, ex.summary, ex.model_dump()
    )
    return {
        "meeting_id": meeting_id,
        "title": req.title,
        "meeting_date": req.meeting_date.isoformat(),
        "speakers": speakers,
        "utterances": [{"speaker": u.speaker, "text": u.text} for u in utterances],
        "extraction": ex.model_dump(),
        "llm_source": llm.provider_info()["last_source"],
    }


@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: int) -> dict:
    m = db.get_meeting(meeting_id)
    if not m:
        raise HTTPException(404, "Meeting not found.")
    return m


# Mounted last so /api routes win.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

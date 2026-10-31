"""Transcript -> decisions + action items (owners, deadlines, confidence, flags)."""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from . import llm
from .dates import DATE_PHRASE_RE, parse_iso, resolve_date
from .speech import (
    UNKNOWN, Utterance, clean_speech, format_for_llm, guess_topic, parse_transcript, speakers,
)


class ExtractedDecision(BaseModel):
    text: str = Field(description="The decision as a short self-contained statement, final intent only")
    topic: str = Field(description="Short lowercase subject reused across meetings, e.g. 'launch date'")
    speaker: str = Field(description="Who stated or confirmed the decision")
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", description="Short quote from the transcript supporting it")


class ExtractedAction(BaseModel):
    title: str = Field(description="Imperative task title, e.g. 'Draft the migration plan'")
    owner: str | None = Field(default=None, description="Responsible person's name, or null")
    deadline_text: str | None = Field(default=None, description="Deadline phrase as spoken (after corrections)")
    deadline: str | None = Field(default=None, description="Resolved deadline YYYY-MM-DD, or null")
    speaker: str = Field(default=UNKNOWN, description="Who raised the task")
    confidence: float = Field(ge=0, le=1)
    evidence: str = ""
    needs_owner: bool = False
    needs_deadline: bool = False


class Extraction(BaseModel):
    summary: str = Field(description="Two-sentence summary of the meeting")
    decisions: list[ExtractedDecision] = []
    action_items: list[ExtractedAction] = []


SYSTEM_PROMPT = """You are Followthrough, an expert meeting analyst that turns spoken meetings into tracked work.
The transcript is REAL SPEECH (dictated with Wispr Flow or transcribed from a call): it has filler words,
digressions, self-corrections and half-finished sentences. Rules:
1. Self-corrections: always keep the FINAL intent. "Friday... no wait, Monday" means Monday.
   "Mongo, sorry, Postgres" means Postgres. Never output an abandoned value.
2. Ignore filler (um, uh, like, you know), small talk, jokes and digressions.
3. A decision is something the group settled on (a choice, date, price, policy). Skip ideas that were
   rejected, still open, or merely floated ("maybe we could...").
4. An action item is a concrete task someone will do. owner = the responsible person using the speaker
   names given: "I'll do X" -> the speaker; "Sam, can you X?" -> Sam. If nobody owns it, owner = null.
5. deadline_text = the deadline phrase as finally said (e.g. "next Friday"). deadline = that date resolved
   to YYYY-MM-DD against the meeting date. Both null if no deadline was given.
6. topic = a short lowercase noun phrase naming WHAT a decision is about, reusable across meetings
   (e.g. "launch date", "database", "pricing", "hosting"). Same subject -> same topic.
7. confidence 0-1: lower for hedged speech ("probably", "I think", "let's tentatively").
8. Write decision text and task titles so they make sense without the transcript."""


def build_prompt(utterances: list[Utterance], meeting_date: date, title: str) -> str:
    return (
        f"Meeting: {title}\n"
        f"Meeting date: {meeting_date.isoformat()} ({meeting_date.strftime('%A')})\n"
        f"Speakers: {', '.join(speakers(utterances))}\n\n"
        f"Transcript:\n{format_for_llm(utterances)}"
    )


# --- deterministic mock ------------------------------------------------------------

_DECISION_CUES = re.compile(
    r"\b(we decided|decided|decision is|let'?s go with|we'?re going with|going with|we'?ll go with|"
    r"agreed|we agree|final answer|locking in|lock in|settled on|it'?s official|we'?re launching|"
    r"we'?ll launch|launch is|launching on|we'?ll use|we'?re using|we will use|pricing is|price is|"
    r"we'?re moving to|moving to|switching to|we'?ll switch|instead of|is going to be|will be priced)\b",
    re.I,
)
_STRONG_CUES = re.compile(r"\b(decided|final|agreed|locking in|lock in|official|settled)\b", re.I)
_HEDGES = re.compile(r"\b(probably|maybe|i think|tentatively|might|for now|roughly)\b", re.I)
_SELF_OWNER = re.compile(r"\b(i'?ll|i will|i can|i'?m going to|i'?m gonna|let me|i'?ll take|i'?ll own)\b", re.I)
_ASKED_OWNER = re.compile(r"\b([A-Z][a-z]+),? (?:can you|could you|would you|please|will you)\b")
_NAMED_OWNER = re.compile(r"\b([A-Z][a-z]+) (?:will|is going to|to|should|needs to|can|owns|is on|takes)\b")
_ACTION_CUES = re.compile(
    r"\b(i'?ll|i will|i can|i'?m going to|let me|can you|could you|please|needs to|will|should|"
    r"action item|to-?do|follow up|make sure|take care of|owns|is on)\b",
    re.I,
)
_NOT_NAMES = {"We", "I", "It", "That", "This", "There", "They", "You", "Someone", "Somebody",
              "Everyone", "Nobody", "Which", "What", "And", "But", "So", "Then", "Also", "Okay", "Ok"}
_LEAD_INS = re.compile(r"^(?:and|but|so|also|then|okay|ok|alright|right|yeah|well)[, ]+", re.I)


def _owner_for(sentence: str, speaker: str, people: set[str]) -> str | None:
    if m := _ASKED_OWNER.search(sentence):
        if m[1] not in _NOT_NAMES:
            return m[1]
    if m := _NAMED_OWNER.search(sentence):
        if m[1] not in _NOT_NAMES and (m[1] in people or not people):
            return m[1]
    if _SELF_OWNER.search(sentence):
        return None if speaker == UNKNOWN else speaker
    return None


def _task_title(sentence: str, owner: str | None, date_phrase: str | None) -> str:
    t = sentence
    if date_phrase:
        t = t.replace(date_phrase, " ")
    t = re.sub(
        r"^.*?\b(?:i'?ll|i will|i can|i'?m going to|i'?m gonna|let me|can you|could you|would you|"
        r"will you|please|will|is going to|needs to|should|to)\b\s*",
        "", t, count=1, flags=re.I,
    ) or t
    if owner:
        t = re.sub(rf"\b{re.escape(owner)}\b,?\s*", "", t)
    t = re.sub(r"\b(?:okay|right|yeah)\??$", "", t.strip(" ,.?!"), flags=re.I).strip(" ,.?!")
    t = re.sub(r"^(?:also|just|go ahead and)\s+", "", t, flags=re.I)
    return (t[:1].upper() + t[1:]) if t else sentence


def _decision_text(sentence: str) -> str:
    t = _LEAD_INS.sub("", sentence.strip(" ,"))
    t = re.sub(r"^(?:so |okay |alright )*(?:we decided|decided|the decision is)( that)?\s*", "", t, flags=re.I)
    return (t[:1].upper() + t[1:]).rstrip(" ,.") if t else sentence


def mock_extract(utterances: list[Utterance], meeting_date: date) -> Extraction:
    people = {s for s in speakers(utterances) if s != UNKNOWN}
    decisions: list[ExtractedDecision] = []
    actions: list[ExtractedAction] = []
    for u in utterances:
        cleaned = clean_speech(u.text)
        for raw in re.split(r"(?<=[.!?])\s+", cleaned):
            sentence = _LEAD_INS.sub("", raw.strip())
            if len(sentence.split()) < 3:
                continue
            hedged = bool(_HEDGES.search(sentence))
            if _DECISION_CUES.search(sentence):
                conf = 0.9 if _STRONG_CUES.search(sentence) else 0.75
                decisions.append(ExtractedDecision(
                    text=_decision_text(sentence), topic=guess_topic(sentence), speaker=u.speaker,
                    confidence=round(conf - (0.2 if hedged else 0), 2), evidence=sentence,
                ))
                continue
            owner = _owner_for(sentence, u.speaker, people)
            is_self = bool(_SELF_OWNER.search(sentence))
            if _ACTION_CUES.search(sentence) and (owner or is_self or "?" not in sentence):
                m = DATE_PHRASE_RE.search(sentence)
                phrase = m[0] if m else None
                actions.append(ExtractedAction(
                    title=_task_title(sentence, owner, phrase), owner=owner,
                    deadline_text=phrase, speaker=u.speaker,
                    confidence=round((0.8 if owner else 0.6) - (0.15 if hedged else 0), 2),
                    evidence=sentence,
                ))
    who = ", ".join(sorted(people)) or "an unattributed speaker"
    summary = (f"Meeting with {who}: {len(decisions)} decision(s) and {len(actions)} action item(s) captured."
               + (f" Key decision: {decisions[0].text}." if decisions else ""))
    return Extraction(summary=summary, decisions=decisions, action_items=actions)


# --- post-processing (applies to LLM and mock output alike) ----------------------------

_NO_OWNER = {"", "unknown", "none", "null", "n/a", "tbd", "team", "everyone", "someone", "us", "we"}


def _match_person(name: str | None, people: list[str]) -> str | None:
    if not name or name.strip().lower() in _NO_OWNER:
        return None
    name = name.strip()
    for p in people:
        if p.lower() == name.lower() or p.split()[0].lower() == name.split()[0].lower():
            return p
    return name


def postprocess(ex: Extraction, utterances: list[Utterance], meeting_date: date) -> Extraction:
    people = [s for s in speakers(utterances) if s != UNKNOWN]
    seen: set[str] = set()
    actions = []
    for a in ex.action_items:
        key = a.title.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        a.owner = _match_person(a.owner, people)
        # Prefer the deterministic resolver; fall back to the model's ISO date.
        resolved = resolve_date(a.deadline_text, meeting_date) or parse_iso(a.deadline)
        a.deadline = resolved.isoformat() if resolved else None
        a.needs_owner = a.owner is None
        a.needs_deadline = a.deadline is None
        a.confidence = round(min(max(a.confidence, 0.0), 1.0), 2)
        actions.append(a)
    for d in ex.decisions:
        d.topic = (d.topic or guess_topic(d.text)).strip().lower()
        d.speaker = _match_person(d.speaker, people) or UNKNOWN
    ex.action_items = actions
    return ex


def extract(transcript: str, meeting_date: date, title: str = "Untitled meeting") -> tuple[list[Utterance], Extraction]:
    utterances = parse_transcript(transcript)
    if not utterances:
        return [], Extraction(summary="Empty transcript.")
    ex = llm.generate_json(
        system=SYSTEM_PROMPT,
        prompt=build_prompt(utterances, meeting_date, title),
        schema=Extraction,
        mock=lambda: mock_extract(utterances, meeting_date),
    )
    return utterances, postprocess(ex, utterances, meeting_date)

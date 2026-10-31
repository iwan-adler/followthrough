# Followthrough

**Meeting-to-execution agent.** Dictate or paste a meeting, and Followthrough turns it into
tracked work: decisions, action items with owners and deadlines, GitHub issues, reminders and a
weekly digest per person. It also **catches decisions that quietly contradict earlier ones**,
using memory that spans meetings.

Teams rarely lose work because nobody wrote it down. They lose it because meeting 2 silently
undoes meeting 1 ("launch is Dec 7th", when everyone was told Nov 23rd) and the tasks built on
the old decision keep going. Followthrough flags that moment and asks a human before anything is
posted.

## What it does

| Brief | Where |
|---|---|
| Transcript ingestion with speaker attribution | `Name:` lines, timestamps, WebVTT/SRT exports; unlabeled text becomes **Unknown** (`app/speech.py`) |
| Real speech (Wispr Flow) | Self-corrections resolved to the final intent ("Friday… no wait, Monday" → Monday), filler dropped; the Review screen shows each correction it made |
| Decisions, action items, owners, deadlines | LLM extraction with confidence plus **needs owner / needs deadline** flags; relative dates resolved against the meeting date (`app/extraction.py`, `app/dates.py`) |
| Conflict detection across meetings | Topic/keyword prefilter, then a batched LLM judgement: contradiction, supersession or none, with a one-line explanation. Old and new shown side by side with **Supersede old / Keep old / Dismiss** (`app/memory.py`) |
| Confirmation before posting | Drafts are editable inline; Confirm is blocked while conflicts are open, and a final dialog lists exactly what goes where (`app/workflow.py`) |
| Tracker integration | One GitHub issue per action item, or a local mock tracker with the same UI (`app/tracker.py`) |
| Reminders | APScheduler sweep for items overdue or due within 24h, plus **Run reminders now** (`app/reminders.py`) |
| Weekly digest per person | Open items, due in the next 7 days, decisions that affect them, unresolved conflicts; LLM narrative with a template fallback (`app/digest.py`) |
| Extra | **"May be obsolete"**: tasks that mention something a later decision replaced (a Postgres task after the move to Firebase) are flagged in Tasks and the digest |

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
cp .env.example .env                               # optional: add keys, see below
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Open http://localhost:8000. With no keys at all, everything still works: a deterministic mock
LLM and a local mock tracker carry the demo. The badge at the top right shows which LLM is live.

Run the tests:

```bash
.venv/Scripts/python -m pytest -q
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` or `groq` (both have free tiers) |
| `LLM_API_KEY` | _empty_ | Free-tier key. Empty = deterministic mock |
| `LLM_MODEL` | provider default | `gemini-2.5-flash` / `llama-3.3-70b-versatile` |
| `LLM_MAX_RETRIES` | `3` | Retries on invalid JSON or network errors, then falls back to the mock |
| `GITHUB_TOKEN` | _empty_ | Token with Issues write access. Empty = local mock tracker |
| `GITHUB_REPO` | _empty_ | `owner/repo` for created issues |
| `GITHUB_USER_MAP` | _empty_ | Optional `Priya=priya-gh,Raj=rajdev` to set issue assignees |
| `DB_PATH` | `followthrough.db` | SQLite file |
| `REMINDER_INTERVAL_MINUTES` | `15` | Background reminder sweep interval |
| `SCHEDULER_ENABLED` | `true` | Turn the background scheduler off |

Free keys: Gemini at https://aistudio.google.com/apikey, Groq at https://console.groq.com/keys.

## 3-minute demo script

**Before you start:** server running, browser on New Meeting, Wispr Flow ready.

1. **The problem (0:00–0:15).** "Decisions change in meetings without anyone saying so, and the
   work built on the old decision keeps going."
2. **The hero moment (0:15–1:15).** Click **Load demo meetings**. This confirms a kickoff meeting
   (launch Nov 23rd, Postgres, $25/seat) and stages a later sync. Review opens with **two
   contradictions**, old and new side by side: *"Product kickoff set the database to Postgres;
   this meeting says Firebase without acknowledging the change."* Point out that the unchanged
   pricing was **not** flagged. Confirm is disabled. Click **Supersede old** on both.
3. **The gate (1:15–1:40).** Click **Confirm**. The dialog lists every task, its owner and due
   date, the tracker it goes to, and which decisions become superseded. Click **Post**. Tasks
   opens with the GitHub issue links (or FT-n mock tickets), grouped by overdue and upcoming.
   Diego's Postgres task now says **⚠ may be obsolete**.
4. **Live voice (1:40–2:25).** On New Meeting, hold the Wispr Flow hotkey and speak the sprint
   planning recap below. Mess it up on purpose ("Wednesday… actually no, Tuesday"). Press
   Ctrl+Enter. Review shows **Speech cleanup**: utterances, filler words dropped, and
   `Wednesday → Tuesday`. Fix an owner inline, then confirm. (Fallback: **Insert sample
   transcript**.)
5. **Follow-through (2:25–3:00).** Reminders: **Run reminders now** shows overdue and due-soon
   items per person. Digest: pick **Diego**. "What changed" shows Postgres → Firebase, and "Heads
   up" says the Postgres task may be obsolete.

**Lines to dictate for step 4** (say "Priya colon" etc., or just talk and let it be Unknown):

> Priya: Okay, sprint planning, let's, um, go around. Raj?
> Raj: So I'll finish the Firebase data model by Monday. And, uh, I'll pair with Diego on the security rules.
> Diego: I'm going to write the security rules by Wednesday... actually no, Tuesday, so Raj can review them.
> Priya: Sam, can you finalize the onboarding checklist design by tomorrow?
> Mei: I'll send the beta invite email by Monday.

## Architecture

```
 Wispr Flow / paste / .txt .vtt .srt
               │
               ▼
 ┌──────────────────────────┐    frontend/ (static HTML/JS/CSS, no build)
 │ speech.py                │    talks only to /api/*: set window.FOLLOWTHROUGH_API
 │ speakers · self-correct  │    to serve it from anywhere
 │ filler · voice report    │
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐   ┌──────────────────────────────┐
 │ extraction.py            │──▶│ llm.py                       │
 │ decisions · actions      │   │ Gemini | Groq, JSON mode,    │
 │ dates.py (deterministic) │   │ schema validation, retries,  │
 └────────────┬─────────────┘   │ mock fallback                │
              ▼                 └──────────────▲───────────────┘
 ┌──────────────────────────┐                  │
 │ memory.py                │──────────────────┤ judge (batched)
 │ prefilter → LLM judge    │                  │
 │ conflicts · supersession │                  │
 └────────────┬─────────────┘                  │
              ▼                                │
   STAGED draft ── inline edits re-check conflicts
              │
   CONFIRM GATE (blocked while conflicts are open, final dialog)
              │
      ┌───────┴────────┬───────────────────┐
      ▼                ▼                   ▼
 decisions →      tracker.py          reminders.py (APScheduler)
 memory           GitHub Issues | mock      │
      │                │                   ▼
      └────────┬───────┴────────────▶ digest.py ─────────────┘ narrative via llm.py
               ▼
            SQLite: meetings · decisions · action_items · conflicts · notifications · mock_tracker
```

### Design choices

- **The LLM only does language work.** Dates ("next Friday"), value comparison, reminder
  windows and digest facts are deterministic. LLMs are unreliable at weekday arithmetic, and
  the digest must never invent a task.
- **One batched judge call per meeting** keeps within free-tier rate limits. The prefilter means
  only plausible pairs are sent, and any old-decision ID not in the candidate set is discarded.
- **Commit, then post.** Confirming writes to memory first, then calls the tracker. A GitHub
  outage never loses confirmed work; the task shows **Retry**.
- **Resolutions are stored on the draft and applied at confirm.** Editing a decision's text
  re-asks the question; unrelated edits keep your answer.
- **Topics are reused.** Topics already in memory are passed to extraction, so "launch date"
  stays "launch date" across meetings.

## Project layout

```
app/        FastAPI app: main.py (routes), workflow.py (stage/confirm), extraction.py,
            speech.py, dates.py, memory.py, tracker.py, reminders.py, digest.py, llm.py, db.py
frontend/   index.html, app.js, style.css (no build step)
seed/       three scripted product meetings + meetings.json
tests/      pytest suite incl. test_e2e.py (LLM and GitHub mocked at the network edge)
```

## API overview

`POST /api/extract` stage a meeting · `PUT /api/meetings/{id}/draft` save edits ·
`POST /api/conflicts/{id}/resolve` · `POST /api/meetings/{id}/confirm` the gate ·
`GET /api/memory` · `GET/PATCH /api/tasks` · `POST /api/tasks/{id}/retry` ·
`POST /api/reminders/run` · `GET /api/notifications` · `GET /api/digest/{person}[/markdown]` ·
`POST /api/demo/load`. Interactive docs at http://localhost:8000/docs.

## Limitations

- No auth; single team, single SQLite file.
- The no-key mock is rule-based. It handles the demo meetings and common speech patterns; a
  free Gemini or Groq key handles arbitrary meetings far better.
- Deadlines are dates, not times, so "due within 24h" means due today or tomorrow.

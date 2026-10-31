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

**Beyond the brief**

| Feature | What it does |
|---|---|
| **Live dictation preview** | While you speak into Wispr Flow, an instant local pass shows decisions, tasks, corrections and filler cut, and warns **⚠ Contradicts Product kickoff** *before* you press Extract (`workflow.preview`) |
| **Recap-style speech** | One flowing paragraph with inline `Name:` labels, or third-person recaps ("Diego said he'll…", "Mei volunteered to…") get the right owners |
| **Conflict impact** | Each conflict lists the open tasks built on the old decision. Superseding lets you close them as obsolete in the same confirm, with a comment on the GitHub issue (`memory.conflict_impact`) |
| **Spoken edits** | "make Sam own the launch announcement and push it to Friday" edits the draft: the LLM plans operations, the app applies them deterministically (`app/commands.py`) |
| **Ask your memory** | "When did we switch to Firebase, and who decided?" is answered from stored decisions only, with checked citations (`app/ask.py`) |
| **"May be obsolete"** | Tasks that mention something a later decision replaced are flagged in Tasks and the digest |
| **Decision map** | Memory shows topics × meetings: active decisions, superseded ones greyed, arrows to what replaced them. Click any decision for who made it, when, what replaced it and why it was flagged |
| **Checkable extraction** | On Review, hovering a decision or task highlights the transcript line it came from, with the matching words marked |

**Keyboard:** <kbd>Ctrl</kbd>+<kbd>K</kbd> opens a command palette (jump anywhere, run demo actions, open a person's digest, or ask memory).
<kbd>1</kbd>–<kbd>6</kbd> switch sections. On Review, <kbd>S</kbd> / <kbd>K</kbd> / <kbd>D</kbd> resolve the next open conflict.
<kbd>Ctrl</kbd>+<kbd>Enter</kbd> extracts from the dictation box. Light and dark themes follow the system, or toggle from the palette.

## Accuracy

Measured on 12 hand-labelled messy meetings and 6 conflict scenarios (`eval/`), live Gemini, 3 runs pooled,
no cached answers, 0 fallbacks:

| | Live model | Offline rules* |
|---|---|---|
| Action items found / correct | 100% / 100% | 100% / 94% |
| Owner · deadline correct | 100% · 100% | 100% · 100% |
| Self-corrections resolved to final intent | 100% | 100% |
| Conflicts caught · false alarms avoided | 100% · 100% | 100% · 100% |
| Decisions found / correct | 100% / 79%† | 100% / 82% |

\* The offline rules were tuned on these cases, so read that column as a regression suite.
† "Incorrect" decisions are extras we didn't label, most of them defensible ("Raj is hosting the webinar").
Full report with every miss: [`eval/RESULTS.md`](eval/RESULTS.md). Reproduce with `python eval/run_eval.py --runs 3`.

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
| `LLM_MODEL` | _empty_ | Model to try first; otherwise the built-in chain in `app/llm.py` |
| `LLM_FALLBACK_MODELS` | built-in chain | Comma-separated models to fall back through. Busy (503/429/timeout) hands over immediately, retired (404) is skipped, the last model that worked goes first |
| `LLM_MAX_RETRIES` | `3` | Passes over the chain when every model is busy, then the mock answers |
| `LLM_CACHE` | `true` | Reuse the validated answer for an identical prompt. One rehearsal makes the demo load instantly |
| `GITHUB_TOKEN` | _empty_ | Token with Issues write access. Empty = local mock tracker |
| `GITHUB_REPO` | _empty_ | `owner/repo` for created issues |
| `GITHUB_USER_MAP` | _empty_ | Optional `Priya=priya-gh,Raj=rajdev` to set issue assignees |
| `DB_PATH` | `followthrough.db` | SQLite file |
| `REMINDER_INTERVAL_MINUTES` | `15` | Background reminder sweep interval |
| `SCHEDULER_ENABLED` | `true` | Turn the background scheduler off |

Free keys: Gemini at https://aistudio.google.com/apikey, Groq at https://console.groq.com/keys.

## 3-minute demo script

**Before you start:** server running, browser on New Meeting, Wispr Flow ready. With a key set,
**rehearse once**: free-tier models are sometimes busy, and the response cache makes the second run
instant with the same real-model output. New dictation still goes to the model live. Press **Reset**
to start from the hero screen, then **Load demo meetings**, then go back to New Meeting.

1. **The problem (0:00–0:15).** "Meeting 2 quietly undoes meeting 1, and the work built on the
   old decision keeps going." The demo has already loaded a kickoff meeting: launch Nov 23rd,
   Postgres, $25/seat.
2. **Talk, and get caught live (0:15–0:50).** Hold the Wispr Flow hotkey and say the recap below.
   As you speak, the side panel counts decisions and tasks, shows `Wednesday → Tuesday`, and then
   flashes **⚠ Contradicts Product kickoff: launch was November 23rd** the moment you say
   December 7th. Press **Ctrl+Enter**.
3. **The hero moment (0:50–1:40).** Review shows the conflicts side by side with a one-line
   explanation, and the **Impact** line: Diego's *Set up the Postgres schema* task is built on the
   old decision. Confirm is disabled. Click **Supersede old**. Then say into the command bar:
   *"make Sam own the launch announcement and push it to Friday"*. The edits appear as ✓ lines.
4. **The gate (1:40–2:10).** Click **Confirm**. The dialog lists every task going to GitHub (or
   the mock tracker), what memory will change, and offers to **close the obsolete Postgres task**.
   Post. Tasks shows the new issues and the closed task with its reason. The header now reads
   *conflicts caught: 2*.
5. **Memory and follow-through (2:10–3:00).** Memory: ask *"When did we switch to Firebase, and who
   decided?"*. The answer cites both decisions, the old one struck through. Reminders: **Run
   reminders now**. Digest: **Diego** shows what changed and what's overdue.

**Lines to dictate in step 2** (flowing speech is fine; say names with a colon, or recap style):

> Priya: Okay, quick sync. Marketing wants more runway, so the launch is December 7th now.
> Diego: I'm going to write the security rules by Wednesday... actually no, Tuesday.
> Priya: Sam, can you finalize the onboarding checklist design by tomorrow?
> Mei: I'll send the launch announcement draft by Monday.

Or recap style: *"Recap of today's sync. Priya said the launch is moving to December 7th. Diego
said he'll write the security rules by Wednesday, actually no, Tuesday. Mei volunteered to send
the beta invite by Monday."*

**Fallback** if the mic misbehaves: **Insert sample transcript** fills in the sprint planning
meeting.

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
app/        FastAPI app: main.py (routes), workflow.py (stage/preview/confirm), extraction.py,
            speech.py, dates.py, memory.py, commands.py (spoken edits), ask.py (memory Q&A),
            tracker.py, reminders.py, digest.py, llm.py, db.py
frontend/   index.html, app.js, style.css (no build step)
seed/       three scripted product meetings + meetings.json
tests/      pytest suite incl. test_e2e.py (LLM and GitHub mocked at the network edge)
```

## API overview

`POST /api/preview` live read while dictating · `POST /api/extract` stage a meeting ·
`PUT /api/meetings/{id}/draft` save edits · `POST /api/meetings/{id}/command` spoken edit ·
`POST /api/conflicts/{id}/resolve` · `POST /api/meetings/{id}/confirm` the gate (optional
`close_task_ids`) · `GET /api/memory` · `POST /api/ask` · `GET/PATCH /api/tasks` ·
`POST /api/tasks/{id}/retry` · `POST /api/reminders/run` · `GET /api/notifications` ·
`GET /api/digest/{person}[/markdown]` · `GET /api/stats` · `POST /api/demo/load|reset`.
Interactive docs at http://localhost:8000/docs.

## Limitations

- No auth; single team, single SQLite file.
- The no-key mock is rule-based. It handles the demo meetings and common speech patterns; a
  free Gemini or Groq key handles arbitrary meetings far better.
- Deadlines are dates, not times, so "due within 24h" means due today or tomorrow.

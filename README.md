# Followthrough

Meeting-to-execution agent: turns spoken meeting transcripts into tracked work and catches
decisions that contradict earlier ones.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
cp .env.example .env                             # optional: add a free Gemini or Groq key
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Open http://localhost:8000. With no `LLM_API_KEY`, a deterministic mock LLM is used so the demo never dies.

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

_Full env reference, demo script and architecture are added as the build progresses._

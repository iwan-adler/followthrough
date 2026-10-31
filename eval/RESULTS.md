# Followthrough accuracy eval

12 messy spoken meetings and 6 conflict scenarios, labelled by hand (`eval/cases.json`): self-corrections of dates, prices, owners and technologies; recap-style and one-paragraph dictation; filler; a rejected proposal; hedged ideas; WebVTT; relative dates; unowned work. Conflict scenarios include true reversals and look-alikes that must **not** be flagged (unchanged values, different aspects of a topic, restatements).

| Metric | gemini (gemini-3.5-flash-lite), 3 runs pooled | Offline rules |
|---|---|---|
| Action items found (recall) | 100% | 100% |
| Action items correct (precision) | 100% | 94% |
| Owner correct | 100% | 100% |
| Deadline resolved to the right date | 100% | 100% |
| Decisions found (recall) | 100% | 100% |
| Decisions correct (precision) | 79% | 82% |
| Self-corrections resolved to final intent | 100% | 100% |
| Conflicts caught (recall) | 100% | 100% |
| Conflict flags correct (precision) | 100% | 100% |
| Conflict judgements correct (accuracy) | 100% | 100% |
| Calls that fell back to offline rules | 0 | n/a |
| Run time | 296s | 0s |

Reproduce: `python eval/run_eval.py` (uses `LLM_API_KEY` from `.env` if set; `--offline` for rules only).

**Read the offline column with care.** The offline rules were improved using these same cases, so for them this is a regression suite rather than evidence they generalise. Nothing was tuned for the live model, so its column is the honest measure. Twelve meetings is a small set; treat the numbers as directional.

## Misses: gemini (gemini-3.5-flash-lite), 3 runs pooled

- owner-self-correction: extra decision (unlabelled, counts against precision): “Diego will write the API documentation”
- double-correction: extra decision (unlabelled, counts against precision): “Raj is hosting the webinar”
- filler-heavy: extra decision (unlabelled, counts against precision): “The onboarding flow is functionally complete.”
- double-correction: extra decision (unlabelled, counts against precision): “Raj is hosting the webinar.”
- owner-self-correction: extra decision (unlabelled, counts against precision): “Diego will write the API docs”
- filler-heavy: extra decision (unlabelled, counts against precision): “The onboarding flow is nearly complete and ready for staging deployment.”
- double-correction: extra decision (unlabelled, counts against precision): “Raj is hosting the webinar”

## Misses: Offline rules

- rejected-proposal: extra decision (unlabelled, counts against precision): “We talked about moving to Kubernetes but no, not this quarter”
- rejected-proposal: extra decision (unlabelled, counts against precision): “Agreed, Heroku for now”
- hedged-vs-decided: extra task (unlabelled, counts against precision): “Probably look at caching at some point”

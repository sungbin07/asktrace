# Design notes

## The question asktrace tries to answer

"I ask an LLM dozens of questions a day. Am I actually learning, or am I asking the same
things over and over?" The raw material is the questions themselves: what they are about,
how deep they go, and how they repeat over time.

## Pipeline

```
export (Claude Code / claude.ai / ChatGPT / JSONL)
   → Question{text, asked_at, session_id}            sources/
   → Tag{concepts[], domain, depth 1-5, type}        tagger/
   → ConceptStats + WeekStats + Summary              metrics.py
   → Markdown / JSON                                 report.py
```

Everything lands in one SQLite file. Ingest is idempotent (fingerprint = source + session +
turn + text), so re-running on a fresh export only adds what is new.

### Inside Claude Code

`asktrace hook` is the ingest step for one session, run from a Claude Code `SessionEnd`
hook. It reads `transcript_path` from the hook payload on stdin and nothing else. Two rules
follow from running inside someone else's session:

- It must not be able to hurt the session. It always exits 0 (a non-zero exit shows as a
  hook error, and on a `Stop` hook exit 2 would keep Claude going), never writes to stdout,
  and reports problems on stderr. Even an argument typo in settings.json exits 0.
- It does not call a model. Tagging through `claude -p` from a hook would start a second
  Claude Code process while the first is shutting down, inside a 1.5-second default budget.
  Tagging stays a separate, explicit step; `--tag heuristic` is the offline opt-in.

## Depth scale

| depth | label  | typical question                             |
|-------|--------|----------------------------------------------|
| 1     | define | what is X?                                   |
| 2     | apply  | how do I use X? (incl. "do X" to an agent)   |
| 3     | reason | why X? X vs Y?                               |
| 4     | debug  | X fails with …, why?                         |
| 5     | design | how should I architect / trade off around X? |

Depth is about the question, not the answer. Asking a depth-5 question about a concept is
strong evidence you already hold depths 1-3 of it.

## Signals

- **Acquisition**: concepts seen for the first time, per week.
- **Deepening**: among concepts asked more than once, the share whose max depth rose after
  the first ask.
- **Forgetting**: re-asks of a concept, on a later day, at the same or lower depth than
  anything asked before. Same-day repeats are excluded: on real Claude Code transcripts an
  agent review loop produced a dozen same-day asks about one concept, which is iteration,
  not forgetting. Three "how do I mount a docker volume" questions a month apart is the clearest
  signal that something did not stick.
- **Mastery per concept** (0-1): `depth/5 × (1 − 0.5^days) × recency decay`, where `days`
  is the number of distinct days you asked about the concept. One question, however deep,
  is only half the evidence (1 day → 0.5, 2 → 0.75, 3 → 0.875). An earlier version let a
  single depth-5 question score 1.0, so on real data the top of the table was whatever you
  asked once today. A 90-day half-life discounts stale knowledge to a floor of 0.3 rather
  than zero.
- **Accumulated knowledge**: the sum of mastery over all concepts. Watch its slope, not its
  absolute value.

## What this is not

- Not a test of what you know. It measures what you *asked*, which is a proxy. Someone who
  learned X elsewhere and never asked about it is invisible here.
- Not a judgement about asking "shallow" questions. Re-asking a syntax detail is cheap and
  rational; the forgetting table exists so you can decide whether to memorize it or keep
  outsourcing it.

## Taggers

- `heuristic`: regex depth rules + keyword domains + token concepts. Deterministic, offline,
  used in tests. Concepts are noisy (surface tokens, not canonical names).
- `claude-code`: same prompt and schema, sent through `claude -p --json-schema`, so it runs
  on a Claude Pro/Max subscription with no API key. One CLI call per 25 questions.
- `claude`: one structured-output call per 25 questions, with a system prompt cached across
  batches. Produces canonical concept names, so revisits across phrasing and language
  ("git rebase" asked in Korean and English) collapse into one concept.

## Roadmap

- [ ] Local HTML dashboard with the weekly timeline and a concept map
- [ ] Concept aliasing / merging (`asktrace merge "sqlite wal" "wal mode"`)
- [ ] Spaced-repetition prompts: "you asked about X three times, want a 5-question quiz?"
- [ ] More sources: Cursor, Gemini export, Open WebUI
- [ ] Per-project views for Claude Code transcripts (`cwd`)

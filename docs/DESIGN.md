# Design notes

## The question asktrace tries to answer

"What am I exploring through AI, and how are my questions and understanding changing?"
The raw material is the question record plus the user's own reflections. Question depth
and repetition describe patterns, not proof of learning, mastery, or forgetting.

## Pipeline

```
export (Claude Code / Codex / claude.ai / ChatGPT / JSONL)
   → Question{text, asked_at, session_id}            sources/
   → Tag{concepts[], domain, depth 1-5, type}        tagger/
   → ConceptStats + WeekStats + Summary              metrics.py
   → Markdown / JSON                                 report.py
   → learning events per day → SVG                   metrics.py, grass.py
```

The same questions also feed session-based journeys, manual grouping and user-authored
reflections (`journeys.py`). A private HTML snapshot links grass dates to these records.

Everything lands in one SQLite file. Ingest is idempotent (fingerprint = source + session +
turn + text), so re-running on a fresh export only adds what is new.

### Journeys and reflections

The original `questions` and `tags` tables and question fingerprints are unchanged.
Three additive tables hold journeys, one membership per question, and one editable
reflection per journey. Old databases gain these tables through `CREATE TABLE IF NOT EXISTS`.
Questions receive default membership lazily when a journey feature is used, so ingestion
hooks remain focused on collection.

Default journey IDs are derived from source and session ID. A question without a session
ID gets its own default group. Group/split operations move membership atomically and never
rewrite questions or tags. Existing moves survive re-ingestion, while new questions join
their original session journey. User-edited group membership is marked `manual`.
Reflections stay on the original journey when questions move, including an emptied
journey. This preserves notes instead of choosing how to merge the user's prose.

Reflections hold `goal`, `learned`, and `open_question`, plus an update timestamp. They
are explicitly self-reported. Partial updates preserve omitted fields; an explicit empty
string clears a field. No inference of goal, causal links, resolution or learning is
performed. Answers and execution results are not stored by this feature.

`journey list --date` and `grass --timezone` use the same calendar. Date filtering shows
only that day's questions while indicating the whole journey's question count.
The HTML contains every recorded question in the selected window, including untagged or
repeated ones. Private day links are added only for dates with questions; default SVG
exports have no question text, notes or links. HTML escapes all imported/user-authored
text, uses no JavaScript or external assets, and is written atomically with owner-only
permissions where supported. It is a read-only snapshot; edits happen in the CLI.

### Inside Claude Code

`asktrace hook` is the ingest step for one session, run from a Claude Code `SessionEnd`
hook. It reads `transcript_path` from the hook payload on stdin and nothing else. Two rules
follow from running inside someone else's session:

- It must not be able to hurt the session. It always exits 0 (a non-zero exit shows as a
  hook error, and on a `Stop` hook exit 2 would keep Claude going), prints nothing to
  stdout, and reports problems on stderr. Even an argument typo in settings.json exits 0,
  and a half-written transcript line is skipped rather than failing the whole session.
- It does not call a model. Tagging through `claude -p` from a hook would start a second
  Claude Code process while the first is shutting down, inside a 1.5-second default budget.
  Tagging stays a separate, explicit step; `--tag heuristic` is the offline opt-in.

### Codex local sessions

The Codex reader consumes local rollout `event_msg` records: legacy `user_message` events
and paginated `item_completed` events whose item is `UserMessage`. It ignores mirrored
`response_item` records, and reads only sessions whose `source` Codex lists as interactive
(`INTERACTIVE_SESSION_SOURCES` in
[rollout](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/rollout/src/lib.rs): `cli`,
`vscode`, and the `atlas`/`chatgpt` custom sources) with a `user` or absent `thread_source`.
`exec`, `mcp`, subagent and internal sessions are skipped so model context, scripted
prompts and delegated instructions do not inflate a person's question history.

When a message starts with client context (`# Context from my IDE setup:`, `## Referenced
chats with Codex:`, or the desktop app's `## Referenced ChatGPT conversation:`, `# Files
mentioned by the user:`, `# Files pasted by the user:` and `<in-app-browser-context>`), only
the text after the first `## My request:` or `## My request for Codex:` heading is kept. This is
similar to Codex's own `strip_user_message_prefix`, which only knows `## My request for Codex:`
and matches it anywhere; here the heading is not split without that context in front, since
then it is the user's own text, and inside a browser page it is ignored. Two envelopes are not
counted: `<send_user_message_question_reply>` holds answers to a question the model asked, and
`<heartbeat>` is a prompt a Codex Desktop automation sends on a schedule. The turn counter still advances for those messages, so
the fingerprints of unwrapped messages do not change. Its synthetic fixtures follow
the upstream [protocol](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/protocol/src/protocol.rs),
[turn items](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/protocol/src/items.rs), and
[persistence policy](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/rollout/src/policy.rs).

Codex compresses rollouts that have not changed for seven days into a single zstd frame named
`<rollout>.jsonl.zst` and deletes the `.jsonl`; resuming such a session writes the `.jsonl`
back ([compression](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/rollout/src/compression.rs)).
The reader lists both forms as one entry per `.jsonl` name and, like Codex's
`existing_rollout_path`, opens the `.jsonl` first and the `.jsonl.zst` only when the plain file
is missing, also when it vanished after listing. Decompressed lines go through exactly the
same record rules as a plain file, and the session-id fallback is the `.jsonl` stem for both
forms, so a session's fingerprints do not change when it is compressed.

Decoding uses `compression.zstd` (Python 3.14+, an optional module in CPython builds) or else
the `zstandard` package from the `codex` extra, reading across frames. There is no `zstd` CLI
fallback: it would start a process per file and is not installed everywhere. Without a
decoder, compressed files are counted and skipped with one stderr line, which keeps zero
required dependencies. Decoding a damaged file stops at the damage and keeps the questions
before it; one stderr line counts files that are not zstd or, with `compression.zstd`, end
inside a frame (`zstandard` ends such a stream quietly). Codex verifies the `.zst` in a
temporary file before renaming it, so neither is expected in practice.

## Depth scale

| depth | label  | typical question                             |
|-------|--------|----------------------------------------------|
| 1     | define | what is X?                                   |
| 2     | apply  | how do I use X? (incl. "do X" to an agent)   |
| 3     | reason | why X? X vs Y?                               |
| 4     | debug  | X fails with …, why?                         |
| 5     | design | how should I architect / trade off around X? |

Depth is about the question, not the answer. Asking a depth-5 question does not establish
that the user understands the underlying concepts; they may be delegating the design.

## Signals

- **Acquisition**: concepts seen for the first time, per week.
- **Deepening**: among concepts asked more than once, the share whose max depth rose after
  the first ask.
- **Re-asking**: re-asks of a concept, on a later day, at the same or lower depth than
  anything asked before. Same-day repeats are excluded: on real Claude Code transcripts an
  agent review loop produced a dozen same-day asks about one concept, which is iteration,
  not forgetting. Later-day repeats can also reflect practice, delegation or a different
  context; the record alone cannot distinguish those explanations.
- **Exploration score per concept** (0-1, legacy JSON key `mastery`):
  `depth/5 × (1 − 0.5^days) × recency decay`, where `days`
  is the number of distinct days you asked about the concept. One question, however deep,
  is only half the evidence (1 day → 0.5, 2 → 0.75, 3 → 0.875). An earlier version let a
  single depth-5 question score 1.0, so on real data the top of the table was whatever you
  asked once today. A 90-day half-life discounts older recorded activity with a recency
  factor floored at 0.3 rather than zero.
- **Total exploration score** (legacy JSON key `accumulated_knowledge`): the sum over all
  concepts. This is a descriptive heuristic, not measured knowledge.

## Knowledge grass

`asktrace grass` borrows the GitHub contribution graph, which Korean developers call 잔디
(grass); "planting grass" means keeping it green every day. That habit has a known failure:
because the graph counts commits, people keep it green with typo fixes and empty commits.
A graph of questions asked would fail the same way, and worse: splitting one question into
five, or asking the same thing again, would earn a darker square.

So a day's value is its first-seen concepts and deeper asks, the per-day version of
acquisition and deepening above. The SVG's existing “learning events” label denotes
these question-pattern signals, not verified learning:

- +1 for each concept asked about for the first time ever;
- +1 for each concept asked about deeper than its previous maximum depth (same-day counts:
  going from "what is X" to "why does X fail" changes the recorded question depth);
- 0 for asks at the same or a lower depth. Not negative: re-asking is a rational choice
  (see below), and a graph that subtracts would punish asking.

The only way to make a day greener is to touch something new or go further than before.
Each concept is worth at most five events in its lifetime: its first ask plus each deeper
level up to 5.

Choices behind the picture:

- **Fixed shades** (1-4, 5-9, 10-19, 20+ events), not scaled to your busiest day as GitHub
  does. With relative scaling a quiet month makes small days look dark; fixed thresholds
  keep a shade meaning the same thing all year. With a model tagger a question usually
  carries two or three concepts, so level 1 is one or two questions that opened something
  and level 4 is a day of real exploration. The scale depends on the tagger: heuristic
  concepts are surface tokens, so heuristic-tagged grass runs darker.
- **Local days**. The graph is a calendar the user reads, so days follow the local time zone
  (the other metrics bucket in UTC). A Seoul morning question lands on that morning's
  square, not the previous day's.
- **Start-up bias**. The first weeks after you start look greener, because every concept is
  new to the log even if it is not new to you.
- **Privacy**. The SVG carries dates and counts only. Real concept names include internal
  project names, so no concept, domain or question text is written, and a test checks it.
  Sharing is a manual commit of the generated file; there is deliberately no GitHub Action,
  which would need your transcripts or database in CI.

## What this is not

- Not a test of what you know. It measures what you *asked*, which is a proxy. Someone who
  learned X elsewhere and never asked about it is invisible here.
- Not a judgement about asking "shallow" questions. Re-asking a syntax detail is cheap and
  rational; the revisit table exists so you can decide whether to memorize it or keep
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

Done:

- [x] Run automatically inside Claude Code (`asktrace hook` for a SessionEnd hook)
- [x] Shareable daily view: knowledge grass SVG of learning events (`asktrace grass`)
- [x] Session-based journeys, manual group/split, and self-reported reflections
- [x] Private HTML calendar with day links to questions and reflections

Next: validate whether people can find an earlier inquiry, write a useful reflection and
return to an open question the following week. Improve the friction observed in that flow.

Possible follow-ups, subject to that feedback:

- [ ] Re-tag questions a hook tagged offline (`asktrace tag --retag heuristic`)
- [ ] Concept aliasing / merging (`asktrace merge "sqlite wal" "wal mode"`)
- [ ] More sources: Cursor, Gemini export, Open WebUI
- [ ] Per-project views for Claude Code transcripts (`cwd`)

Automatic coaching, quizzes and concept maps remain ideas, not part of the current MVP.
See [the product scope](PRODUCT_DIRECTION.ko.md) for the boundary and validation questions.

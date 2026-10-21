# asktrace

**Trace what you explore with AI, and reflect on how your thinking develops.**

You ask Claude, ChatGPT, Claude Code or Codex dozens of questions a day. asktrace reads those
transcripts, groups your questions into journeys, and lets you record what you checked
and what remains open. Concept tags and activity graphs help you revisit those records.
Question patterns are clues for reflection, not proof of understanding or forgetting.

For example, revisit the questions in one investigation:

> How do I add a cache? → Is the database actually the bottleneck? →
> How can I measure time spent waiting for the external API?

Then write your own reflection:

> **Wanted to understand:** whether caching would help.
> **Checked:** the database has not been established as the cause; measure latency first.
> **Still open:** what happens under concurrent requests?

This is an illustrative, user-written reflection. asktrace preserves the questions and
your notes; it does not infer that you have learned something from asking about it.

Works offline with a heuristic tagger. For canonical concept names across languages and
phrasings, tag with Claude: through the Claude Code CLI on a Pro/Max subscription, or through
the API with a key.

## Install

```bash
pip install asktrace            # offline heuristic tagger
pip install 'asktrace[claude]'  # + Claude tagger
```

Or from source: `uv sync --extra dev`.

## Quick start

```bash
# 1. Pull your questions in (all idempotent; re-run on fresh exports any time)
asktrace ingest claude-code                      # ~/.claude/projects/**/*.jsonl
asktrace ingest codex                            # $CODEX_HOME/sessions or ~/.codex/sessions
asktrace ingest claude-export conversations.json # claude.ai → Settings → Export data
asktrace ingest chatgpt-export conversations.json
asktrace ingest jsonl my_questions.jsonl         # {"text": ..., "asked_at": "ISO8601"}

# 2. Tag them
asktrace tag                                     # offline heuristic
asktrace tag --tagger claude-code                # Claude subscription via `claude -p`, no API key
asktrace tag --tagger claude                     # Anthropic API (ANTHROPIC_API_KEY)

# 3. Look
asktrace report
asktrace report --since 2026-09-01 --format json
```

Try it on the bundled sample without touching your own data:

```bash
asktrace --db /tmp/demo.db ingest jsonl examples/sample_questions.jsonl
asktrace --db /tmp/demo.db tag
asktrace --db /tmp/demo.db report
asktrace --db /tmp/demo.db journey list
asktrace --db /tmp/demo.db grass --format html --end 2026-10-20 --out /tmp/demo.html
```

## Follow a question journey

Journeys work before tagging, offline and without a model. Start with the questions in
each source/session, then adjust those groups to match what you were investigating:

```bash
asktrace journey list
asktrace journey list --date 2026-09-02 --timezone Asia/Seoul
asktrace journey show JOURNEY_ID
```

Replace `JOURNEY_ID` with an ID from `journey list`. `show` displays the questions in time
order, including their IDs. A session group is only a starting point: asktrace does not
assume that every question in a session shares a goal. Inputs without a session ID start
as separate journeys.

Use the question IDs from `show` to split a topic out or combine related questions:

```bash
asktrace journey split JOURNEY_ID --title "Measure the bottleneck" --questions QUESTION_ID
asktrace journey group --title "Understand caching" --questions QUESTION_ID_1 QUESTION_ID_2
asktrace journey group --into JOURNEY_ID --questions QUESTION_ID
```

Each question belongs to one journey. Grouping changes neither the question IDs nor the
tags and scores. Re-ingesting does not undo your moves. New turns join their original
session's journey; you can move them later. A split must leave at least one question in
the original journey.

### Save your own reflection

```bash
asktrace review JOURNEY_ID \
  --goal "Find out whether caching would help" \
  --learned "Measured latency; most waiting was in the external API" \
  --open-question "What happens under concurrent requests?"
asktrace review JOURNEY_ID
```

The three fields describe what you wanted to understand, what you checked or understood,
and what you still want to ask. They are your own account, not an AI assessment. Omitted
fields stay unchanged; use `--open-question ""` to clear one. Moving questions keeps notes
on their original journey, even if it becomes empty, so a merge cannot discard a reflection.
Use `--format json` with `journey list`, `journey show`, or `review` for structured output.

### Open a day's questions from the grass

```bash
asktrace grass --format html --out /tmp/asktrace-journeys.html --timezone Asia/Seoul
```

Open the generated file in your browser. Select a linked day to read its question journeys
and reflections. Untagged and repeated questions remain available even when they add no
colour; an outlined square indicates a recorded day with no scored activity.

**This HTML is a private snapshot containing question text and reflections.** It has no
server, scripts or external assets. Save changes through the CLI and regenerate the file.
`--out` must end in `.html` or `.htm`, so the snapshot cannot be written under the `.svg`
name you commit to a public profile. The default SVG remains the sharing format with dates and counts only. `--timezone`
uses the same calendar in `journey list --date` and `grass`; omitting it uses system local
time. `--end` and `--weeks` set the exported window.
The window filters question dates; each included journey shows its full current reflection,
which can refer to work outside that window. Journeys with no remaining questions are
available through `journey list` and `review`, rather than on a calendar day.

## Run it automatically in Claude Code

`asktrace hook` reads a [Claude Code hook](https://code.claude.com/docs/en/hooks) payload
from stdin and ingests the one transcript it names (`transcript_path`). Add a `SessionEnd`
hook to `~/.claude/settings.json` (all projects) or a project's `.claude/settings.json`;
asktrace does not edit these files for you:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "asktrace hook", "timeout": 10 }
        ]
      }
    ]
  }
}
```

- `asktrace` has to be on the `PATH` Claude Code sees. If it is not, use the full path
  from `which asktrace`. The same goes for `ASKTRACE_DB`: Claude Code started from the
  desktop app or an IDE may not see variables set in your shell profile, so a custom
  database belongs in the command itself (`asktrace --db ~/notes/asktrace.db hook`).
- SessionEnd hooks share a 1.5-second budget unless a hook sets its own `timeout`, so the
  snippet sets 10 seconds. Ingesting one transcript usually takes well under a second.
- The hook always exits 0 and prints nothing on success. Errors go to stderr only, so a
  bad payload or a locked database never turns into a hook error in your session. To debug,
  run it by hand: `echo '{"transcript_path": "/path/to/session.jsonl"}' | asktrace hook`.
- The hook does not tag. Tagging with `--tagger claude-code` would launch `claude -p`
  while Claude Code is shutting down, so run `asktrace tag --tagger claude-code` when you
  look at your report. `asktrace hook --tag heuristic` tags the session's questions
  offline instead, but those questions keep heuristic tags: `asktrace tag` only fills in
  untagged ones.
- Ingest is idempotent, so the same command also works as a `Stop` hook (after every
  response). SessionEnd is enough for most people. Claude Code writes the transcript
  asynchronously, so the last message of a session can be missing when the hook runs;
  `asktrace ingest claude-code` picks up anything a hook missed.

## Knowledge grass

A GitHub-style contribution graph of recorded concept exploration, one square per day:

```bash
asktrace grass --out grass.svg                     # last 52 weeks, light theme
asktrace grass --out grass-dark.svg --theme dark
asktrace grass --weeks 26 > grass.svg              # no --out: SVG on stdout
asktrace grass --end 2026-06-30 --out h1.svg      # a window ending on another day
```

![asktrace knowledge grass for the bundled sample questions](docs/grass-example.svg)

A square's shade counts the day's **first-seen concepts and deeper asks** (called
“learning events” in the SVG). These are question-pattern signals, not confirmed learning:

- +1 for each concept you asked about for the first time
- +1 for each concept you asked about at a deeper level than ever before
  (define → apply → reason → debug → design)
- 0 for asking about a concept again at the same or a lower depth, however many times

Splitting one question into five, or asking the same docker question every month, does not
make a day greener. A concept is worth at most five events in total. The shades are fixed:
1-4, 5-9, 10-19 and 20+ events. Days follow your local time zone, and only tagged questions
are drawn, so run `asktrace tag` first. The example above is the bundled sample tagged
with the offline heuristic tagger, drawn with `--end 2026-10-20`.

The SVG contains dates and counts only: no concept names, domains or question text.

### Put it on your GitHub profile

Your profile README lives in the repository named after your username. Write both themes
into a clone of it and let GitHub pick the one that matches the viewer:

```bash
asktrace grass --out ~/src/<username>/asktrace-grass.svg
asktrace grass --out ~/src/<username>/asktrace-grass-dark.svg --theme dark
```

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="asktrace-grass-dark.svg">
  <img alt="Learning events per day, from asktrace" src="asktrace-grass.svg">
</picture>
```

Commit both SVGs and push. The graph changes only when you regenerate and push again;
nothing reads your database except asktrace on your own machine.

## Codex local sessions

`asktrace ingest codex` reads local, uncompressed rollout JSONL files recursively.
You can also select a sessions directory or one rollout file:

```bash
asktrace ingest codex /path/to/sessions
asktrace ingest codex /path/to/rollout.jsonl
asktrace tag
asktrace report
```

It imports user-message events from legacy rollouts and completed user-message items
from paginated rollouts. Mirrored model-input records, assistant replies and tool output
are excluded. Only sessions Codex itself treats as interactive count: the CLI, the IDE
extension and the desktop app. Sessions started by `codex exec` or by another agent through
Codex's MCP/app server, and subagent/internal threads, are skipped, because a script or a
model wrote those prompts, not you.

The IDE extension and the desktop app put context in front of what you typed (open tabs,
mentioned or pasted files, a referenced ChatGPT conversation, the page in the in-app browser)
and mark your part with a `## My request…` heading. Only your request is stored, so the question text, journey titles
and tagger input start with your words. A message that holds only pasted files and no typed
request is not counted, and neither are your answers to a question Codex asked you or the
prompts a Codex automation sends on a schedule.

Re-importing the same session does not duplicate questions. Malformed records, including an incomplete final line,
are skipped. A database filled by an earlier development build of the Codex reader (which
kept the app context, pasted-only messages and `exec` sessions) is not rewritten: re-importing
adds cleaned copies beside the old rows. Ingest Codex into a fresh database (`--db`) instead.

Archived sessions are opt-in: pass their `archived_sessions` directory explicitly.
This reader does not fetch cloud conversations, decode compressed rollouts, or reconstruct
inherited history from fork/revert references. Raw-response-only exports are not supported.
No Codex API key or CLI login is needed to read files already on your machine. Codex is
an input source here; model-backed tagging still uses the existing Claude taggers.

## What it measures

Each question gets **concepts** (e.g. `prompt caching`, `git rebase`), a **domain**, and a
**depth** from 1 to 5:

| depth | you asked…                              |
|-------|-----------------------------------------|
| 1     | what is X?                              |
| 2     | how do I use X?                         |
| 3     | why X? X vs Y?                          |
| 4     | X fails with …, why?                    |
| 5     | how should I design / trade off X?      |

From those, per concept and per week:

- **Acquisition**: concepts appearing for the first time in the imported record.
- **Deepening**: revisited concepts whose questions got deeper over time.
- **Re-ask rate**: revisits at the same or lower question depth. Repetition can reflect
  practice, delegation, a different context, or an unresolved problem.
- **Exploration score** (0-1 per concept): question depth × appearances on separate days ×
  90-day recency decay. This describes the record, not demonstrated mastery.
- **Total exploration score**: the sum of per-concept scores. JSON retains the existing
  `mastery` and `accumulated_knowledge` keys for compatibility; their interpretation is
  the same heuristic described here.

The reasoning behind each number is in [docs/DESIGN.md](docs/DESIGN.md). They are
heuristics about what you *asked*, not a test of what you know.

## Privacy

Questions, grouping choices and reflections stay in one local SQLite file
(`~/.asktrace/asktrace.db`, or `--db` / `ASKTRACE_DB`). Choosing a Claude-backed tagger sends
question text to that model. Journey grouping, reflections and HTML export do not call
a model. The `grass` SVG contains only dates and counts. Journey output and HTML include
private text; keep those files local. Dates and activity counts can themselves reveal
patterns, so review the range before sharing a graph.

## Development

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```

Adding a source: subclass `asktrace.sources.base.Source`, yield `Question`s, register it in
`asktrace/sources/__init__.py`. Adding a tagger: subclass `asktrace.tagger.base.Tagger`.

## License

MIT

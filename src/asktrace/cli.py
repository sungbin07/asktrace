"""Command line entry point: ingest → tag → report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import __version__, grass, metrics, report
from .sources import SOURCES
from .store import DEFAULT_DB, Store
from .tagger import get_tagger


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="asktrace", description=__doc__)
    p.add_argument("--version", action="version", version=f"asktrace {__version__}")
    p.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"SQLite path (default {DEFAULT_DB})"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="read questions from a chat export into the database")
    ing.add_argument("source", choices=sorted(SOURCES))
    ing.add_argument(
        "path", nargs="?", help="file or directory (claude-code defaults to ~/.claude/projects)"
    )

    tag = sub.add_parser("tag", help="tag untagged questions with concepts, domain and depth")
    tag.add_argument(
        "--tagger", choices=["heuristic", "claude", "claude-code"], default="heuristic"
    )
    tag.add_argument(
        "--model",
        help="model for the model-backed taggers (claude: default $ASKTRACE_MODEL or"
        " claude-opus-5-5; claude-code: your Claude Code default, or e.g. sonnet)",
    )
    tag.add_argument("--limit", type=int, help="tag at most N questions this run")

    rep = sub.add_parser("report", help="print knowledge-accumulation metrics")
    rep.add_argument("--format", choices=["md", "json"], default="md")
    rep.add_argument("--since", help="ISO date, e.g. 2026-01-01")
    rep.add_argument("--top", type=int, default=20)

    gr = sub.add_parser(
        "grass",
        help="draw learning events per day as a contribution-graph SVG",
        description="Write a GitHub-contribution-style SVG. A day's shade is its learning"
        " events (concepts asked for the first time plus asks deeper than before), not its"
        " question count. The SVG contains dates and counts only.",
    )
    gr.add_argument("--out", type=Path, help="SVG file to write (default: stdout)")
    gr.add_argument("--weeks", type=_weeks, default=52, help="columns, 1-520 (default 52)")
    gr.add_argument(
        "--end",
        type=date.fromisoformat,
        help="last day drawn, YYYY-MM-DD (default: today in your local time zone)",
    )
    gr.add_argument("--theme", choices=sorted(grass.THEMES), default="light")

    sub.add_parser("stats", help="how many questions are stored and tagged")

    hk = sub.add_parser(
        "hook",
        help="ingest the transcript named in a Claude Code hook payload on stdin",
        description="Run from a Claude Code SessionEnd hook. Reads the hook JSON from stdin and"
        " ingests that one transcript. Always exits 0 and prints nothing on success, so a"
        " failure here cannot disturb the Claude Code session.",
    )
    hk.add_argument(
        "--tag",
        choices=["heuristic"],
        help="also tag this session's new questions offline (default: no tagging,"
        " run `asktrace tag` later)",
    )
    return p


def _weeks(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {value!r}") from None
    if not 1 <= n <= 520:
        raise argparse.ArgumentTypeError("must be between 1 and 520 (ten years)")
    return n


COMMANDS = ("ingest", "tag", "report", "grass", "stats", "hook")


def _is_hook_call(argv: list[str]) -> bool:
    """True when the first command name in argv is `hook` (the value of --db is skipped).

    Looks for a known command rather than the first positional so that an abbreviated
    option argparse accepts (`--d PATH hook`) is still recognised as a hook call.
    """
    args = iter(argv)
    for a in args:
        if a == "--db":
            next(args, None)
        elif a in COMMANDS:
            return a == "hook"
    return False


def _hook(db: Path, tag: str | None) -> None:
    """Ingest the one transcript a Claude Code hook payload points at. Raises on bad input."""
    if sys.stdin is None or sys.stdin.isatty():
        raise ValueError("expected a Claude Code hook payload on stdin")
    try:
        # Bytes, not text: json detects UTF-8 itself, while text stdin uses the locale
        # encoding (a code page on Windows) and would garble non-ASCII transcript paths.
        payload = json.loads(getattr(sys.stdin, "buffer", sys.stdin).read())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"hook payload on stdin is not JSON ({e})") from None
    path = payload.get("transcript_path") if isinstance(payload, dict) else None
    if not isinstance(path, str) or not path:
        raise ValueError("hook payload has no transcript_path")
    transcript = Path(path).expanduser()
    if not transcript.is_file():  # a directory would make the source read every transcript
        raise ValueError(f"transcript not found: {transcript}")
    questions = list(SOURCES["claude-code"](transcript).questions())
    with Store(db) as store:
        store.add_questions(questions)
        if tag:
            # Only this session's questions: leave the rest of the backlog for `asktrace tag`.
            ids = {q.fingerprint for q in questions}
            rows = [r for r in store.untagged() if r["id"] in ids]
            if rows:
                store.add_tags(get_tagger(tag).tag([(r["id"], r["text"]) for r in rows]))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        # A mistyped hook command line must not become a failing hook.
        if e.code not in (0, None) and _is_hook_call(argv):
            return 0
        raise
    if args.cmd == "hook":
        try:
            _hook(args.db, args.tag)
        except Exception as e:  # a hook must never fail the Claude Code session
            if sys.stderr is not None:  # print(file=None) would fall back to stdout
                print(f"asktrace hook: {e}", file=sys.stderr)
        return 0
    with Store(args.db) as store:
        if args.cmd == "ingest":
            cls = SOURCES[args.source]
            src = cls(args.path) if args.path else cls()  # type: ignore[call-arg]
            n = store.add_questions(src.questions())
            print(f"ingested {n} new questions ({store.count_questions()} total)")
        elif args.cmd == "tag":
            rows = store.untagged(limit=args.limit)
            if not rows:
                print("nothing to tag")
                return 0
            tagger = get_tagger(args.tagger, model=args.model)
            items = [(r["id"], r["text"]) for r in rows]
            step = getattr(tagger, "batch_size", len(items)) or len(items)
            done = 0
            # Save after every batch so a failure late in a long run keeps earlier work.
            for start in range(0, len(items), step):
                tags = tagger.tag(items[start : start + step])
                store.add_tags(tags)
                done += len(tags)
                print(f"  {min(start + step, len(items))}/{len(items)}", file=sys.stderr)
            print(f"tagged {done}/{len(rows)} with {tagger.name}")
        elif args.cmd == "report":
            since = (
                datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
                if args.since
                else None
            )
            summary = metrics.compute(store.tagged_questions(since=since), top_n=args.top)
            print(report.to_json(summary) if args.format == "json" else report.to_markdown(summary))
        elif args.cmd == "grass":
            # Days follow your local calendar, like the graph on a GitHub profile.
            days = metrics.learning_events_by_day(store.tagged_questions(), tz=None)
            end = args.end or datetime.now().astimezone().date()
            svg = grass.render(days, end=end, weeks=args.weeks, theme=args.theme)
            untagged = store.count_questions() - store.count_tagged()
            if untagged:
                print(
                    f"note: {untagged} untagged questions are not drawn; run `asktrace tag`",
                    file=sys.stderr,
                )
            if args.out:
                args.out.write_text(svg, encoding="utf-8")
                tot = grass.totals(days, *grass.window(end, args.weeks))
                print(
                    f"wrote {args.out}: {tot['events']} learning events on"
                    f" {tot['active_days']} days in the last {grass.plural(args.weeks, 'week')}"
                )
            else:
                sys.stdout.write(svg)
        elif args.cmd == "stats":
            nq, nt = store.count_questions(), store.count_tagged()
            print(f"questions: {nq}  tagged: {nt}  db: {store.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

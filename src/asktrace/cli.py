"""Command line entry point: ingest → tag → report."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, metrics, report
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
    tag.add_argument("--tagger", choices=["heuristic", "claude"], default="heuristic")
    tag.add_argument(
        "--model", help="model id for --tagger claude (default $ASKTRACE_MODEL or claude-opus-5-5)"
    )
    tag.add_argument("--limit", type=int, help="tag at most N questions this run")

    rep = sub.add_parser("report", help="print knowledge-accumulation metrics")
    rep.add_argument("--format", choices=["md", "json"], default="md")
    rep.add_argument("--since", help="ISO date, e.g. 2026-01-01")
    rep.add_argument("--top", type=int, default=20)

    sub.add_parser("stats", help="how many questions are stored and tagged")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
            tags = tagger.tag([(r["id"], r["text"]) for r in rows])
            store.add_tags(tags)
            print(f"tagged {len(tags)}/{len(rows)} with {tagger.name}")
        elif args.cmd == "report":
            since = (
                datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
                if args.since
                else None
            )
            summary = metrics.compute(store.tagged_questions(since=since), top_n=args.top)
            print(report.to_json(summary) if args.format == "json" else report.to_markdown(summary))
        elif args.cmd == "stats":
            nq, nt = store.count_questions(), store.count_tagged()
            print(f"questions: {nq}  tagged: {nt}  db: {store.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

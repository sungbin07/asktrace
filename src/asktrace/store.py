"""SQLite persistence. One file, two tables, no ORM."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .models import Question, Tag

DEFAULT_DB = Path(os.environ.get("ASKTRACE_DB", Path.home() / ".asktrace" / "asktrace.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    asked_at TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_asked_at ON questions(asked_at);

CREATE TABLE IF NOT EXISTS tags (
    question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    concepts TEXT NOT NULL,
    domain TEXT NOT NULL,
    depth INTEGER NOT NULL,
    question_type TEXT NOT NULL,
    tagger TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    raw TEXT NOT NULL DEFAULT '{}'
);
"""


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- questions -----------------------------------------------------------

    def add_questions(self, questions: Iterable[Question]) -> int:
        """Insert questions, skipping fingerprints already present. Returns inserted count."""
        rows = [
            (
                q.fingerprint,
                q.source,
                q.session_id,
                q.turn_index,
                q.asked_at_utc.isoformat(),
                q.text,
            )
            for q in questions
        ]
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO questions"
                " (id, source, session_id, turn_index, asked_at, text) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return self.conn.total_changes - before

    def untagged(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT q.* FROM questions q LEFT JOIN tags t ON t.question_id = q.id"
            " WHERE t.question_id IS NULL ORDER BY q.asked_at"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return list(self.conn.execute(sql))

    def count_questions(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]

    def count_tagged(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    # -- tags ----------------------------------------------------------------

    def add_tags(self, tags: Iterable[Tag]) -> int:
        rows = [
            (
                t.question_id,
                json.dumps(t.concepts, ensure_ascii=False),
                t.domain,
                t.depth,
                t.question_type,
                t.tagger,
                t.confidence,
                json.dumps(t.raw, ensure_ascii=False),
            )
            for t in tags
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO tags"
                " (question_id, concepts, domain, depth, question_type, tagger, confidence, raw)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def tagged_questions(self, since: datetime | None = None) -> list[dict]:
        """Join of questions and tags, oldest first. Each dict has parsed `concepts`."""
        sql = (
            "SELECT q.id, q.source, q.session_id, q.asked_at, q.text,"
            " t.concepts, t.domain, t.depth, t.question_type, t.tagger"
            " FROM questions q JOIN tags t ON t.question_id = q.id"
        )
        params: tuple = ()
        if since is not None:
            sql += " WHERE q.asked_at >= ?"
            params = (since.isoformat(),)
        sql += " ORDER BY q.asked_at"
        out = []
        for row in self.conn.execute(sql, params):
            d = dict(row)
            d["concepts"] = json.loads(d["concepts"])
            d["asked_at"] = datetime.fromisoformat(d["asked_at"])
            out.append(d)
        return out

"""SQLite persistence for questions, tags and personal journey reflections."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .journeys import session_journey_id
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

CREATE TABLE IF NOT EXISTS journeys (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('session', 'manual')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journey_questions (
    question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    journey_id TEXT NOT NULL REFERENCES journeys(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_journey_questions_journey ON journey_questions(journey_id);
CREATE TABLE IF NOT EXISTS journey_reflections (
    journey_id TEXT PRIMARY KEY REFERENCES journeys(id) ON DELETE CASCADE,
    goal TEXT NOT NULL DEFAULT '',
    learned TEXT NOT NULL DEFAULT '',
    open_question TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
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
            "SELECT q.id, q.source, q.session_id, q.turn_index, q.asked_at, q.text,"
            " t.concepts, t.domain, t.depth, t.question_type, t.tagger"
            " FROM questions q JOIN tags t ON t.question_id = q.id"
        )
        params: tuple = ()
        if since is not None:
            sql += " WHERE q.asked_at >= ?"
            params = (since.isoformat(),)
        sql += " ORDER BY q.asked_at, q.session_id, q.turn_index"
        out = []
        for row in self.conn.execute(sql, params):
            d = dict(row)
            d["concepts"] = json.loads(d["concepts"])
            d["asked_at"] = datetime.fromisoformat(d["asked_at"])
            out.append(d)
        return out

    # -- journeys: membership is independent of tags and raw question IDs --

    def _assign_journeys(self) -> None:
        """Assign only ungrouped questions, including those in pre-journey databases.

        Deriving the default ID from source + session lets new questions join their
        original session without undoing a user's moves. Session-less inputs remain
        separate until explicitly grouped by the user.
        """
        rows = self.conn.execute(
            "SELECT q.id, q.source, q.session_id FROM questions q"
            " LEFT JOIN journey_questions jq ON jq.question_id = q.id"
            " WHERE jq.question_id IS NULL"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            journey_id = session_journey_id(row["source"], row["session_id"], row["id"])
            self.conn.execute(
                "INSERT OR IGNORE INTO journeys (id, kind, created_at) VALUES (?, 'session', ?)",
                (journey_id, now),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO journey_questions (question_id, journey_id) VALUES (?, ?)",
                (row["id"], journey_id),
            )

    def all_journeys(self) -> list[dict]:
        """All questions in their current groups, with user-authored reflections.

        Empty groups with notes remain visible so moving questions cannot lose a
        reflection. Default groups are a navigational starting point, not inferred goals.
        """
        with self.conn:
            # Keep membership and metadata in one snapshot while a hook or another
            # CLI process may be inserting questions or moving groups.
            self.conn.execute("BEGIN IMMEDIATE")
            self._assign_journeys()
            return self._read_journeys()

    def _read_journeys(self) -> list[dict]:
        groups = {}
        for row in self.conn.execute("SELECT * FROM journeys ORDER BY created_at, id"):
            groups[row["id"]] = {**dict(row), "questions": [], "reflection": None}
        for row in self.conn.execute(
            "SELECT q.*, jq.journey_id FROM questions q"
            " JOIN journey_questions jq ON jq.question_id = q.id"
            " ORDER BY q.asked_at, q.source, q.session_id, q.turn_index, q.id"
        ):
            question = dict(row)
            journey_id = question.pop("journey_id")
            question["asked_at"] = datetime.fromisoformat(question["asked_at"])
            groups[journey_id]["questions"].append(question)
        for row in self.conn.execute("SELECT * FROM journey_reflections"):
            reflection = dict(row)
            journey_id = reflection.pop("journey_id")
            groups[journey_id]["reflection"] = reflection
        return list(groups.values())

    def get_journey(self, journey_id: str) -> dict:
        for group in self.all_journeys():
            if group["id"] == journey_id:
                return group
        raise ValueError(f"unknown journey: {journey_id}")

    def group_questions(
        self,
        question_ids: Iterable[str],
        *,
        title: str | None = None,
        into: str | None = None,
        split_from: str | None = None,
    ) -> str:
        """Atomically move selected questions to a new or existing journey.

        Reflections stay with their original journeys; raw questions and tags never
        change. A split must select a proper subset of the source journey.
        """
        ids = list(dict.fromkeys(question_ids))
        if not ids:
            raise ValueError("select at least one question")
        if (title is None) == (into is None):
            raise ValueError("provide either a title for a new journey or an existing journey ID")
        if title is not None and not title.strip():
            raise ValueError("journey title cannot be empty")
        if split_from is not None and into is not None:
            raise ValueError("a split needs a new journey title")
        # Validate everything inside the transaction before moving any membership.
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assign_journeys()
            membership = dict(
                self.conn.execute("SELECT question_id, journey_id FROM journey_questions")
            )
            missing = [qid for qid in ids if qid not in membership]
            if missing:
                raise ValueError(f"unknown question: {missing[0]}")
            if (
                into is not None
                and not self.conn.execute("SELECT 1 FROM journeys WHERE id = ?", (into,)).fetchone()
            ):
                raise ValueError(f"unknown journey: {into}")
            if split_from is not None:
                original = {qid for qid, jid in membership.items() if jid == split_from}
                if not original:
                    raise ValueError(f"unknown or empty journey: {split_from}")
                if not set(ids) < original:
                    raise ValueError(
                        "a split must select some, but not all, questions in the journey"
                    )
            journey_id = into or f"j_{uuid4().hex[:16]}"
            if into is None:
                self.conn.execute(
                    "INSERT INTO journeys (id, title, kind, created_at) VALUES (?, ?, 'manual', ?)",
                    (journey_id, title.strip(), datetime.now(timezone.utc).isoformat()),
                )
            self.conn.executemany(
                "UPDATE journey_questions SET journey_id = ? WHERE question_id = ?",
                [(journey_id, qid) for qid in ids],
            )
            self.conn.executemany(
                "UPDATE journeys SET kind = 'manual' WHERE id = ?",
                [(jid,) for jid in {membership[qid] for qid in ids} | {journey_id}],
            )
        return journey_id

    def save_reflection(
        self,
        journey_id: str,
        *,
        goal: str | None = None,
        learned: str | None = None,
        open_question: str | None = None,
    ) -> dict:
        """Update only supplied fields; an explicit empty string clears a field."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assign_journeys()
            if not self.conn.execute(
                "SELECT 1 FROM journeys WHERE id = ?", (journey_id,)
            ).fetchone():
                raise ValueError(f"unknown journey: {journey_id}")
            row = self.conn.execute(
                "SELECT * FROM journey_reflections WHERE journey_id = ?", (journey_id,)
            ).fetchone()
            values = {
                key: supplied if supplied is not None else (row[key] if row else "")
                for key, supplied in (
                    ("goal", goal),
                    ("learned", learned),
                    ("open_question", open_question),
                )
            }
            self.conn.execute(
                "INSERT INTO journey_reflections"
                " (journey_id, goal, learned, open_question, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(journey_id) DO UPDATE SET"
                " goal = excluded.goal, learned = excluded.learned,"
                " open_question = excluded.open_question, updated_at = excluded.updated_at",
                (
                    journey_id,
                    values["goal"],
                    values["learned"],
                    values["open_question"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return self.get_journey(journey_id)

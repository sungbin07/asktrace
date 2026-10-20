"""Generic JSONL: one object per line with `text`, `asked_at` (ISO 8601) and optional
`session_id`, `source`. Use this to feed asktrace from any tool."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime

from ..models import Question
from .base import Source, looks_like_question


class JsonlSource(Source):
    name = "jsonl"

    def questions(self) -> Iterator[Question]:
        with self.path.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                text = str(rec.get("text", ""))
                if not looks_like_question(text):
                    continue
                yield Question(
                    text=text.strip(),
                    asked_at=datetime.fromisoformat(str(rec["asked_at"]).replace("Z", "+00:00")),
                    source=str(rec.get("source") or self.name),
                    session_id=str(rec.get("session_id", "")),
                    turn_index=int(rec.get("turn_index", i)),
                )

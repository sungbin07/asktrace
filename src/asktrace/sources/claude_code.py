"""Claude Code local transcripts: ~/.claude/projects/<project>/<session>.jsonl"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import Question
from .base import Source, looks_like_question

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def _text_of(content: object) -> str | None:
    """User message content is a string, or a list of blocks; tool results are not questions."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return None
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts) if parts else None
    return None


class ClaudeCodeSource(Source):
    name = "claude-code"

    def __init__(self, path: Path | str | None = None):
        super().__init__(path or DEFAULT_ROOT)

    def questions(self) -> Iterator[Question]:
        files = [self.path] if self.path.is_file() else sorted(self.path.rglob("*.jsonl"))
        for file in files:
            yield from self._read_file(file)

    def _read_file(self, file: Path) -> Iterator[Question]:
        turn = 0
        # A live session's transcript can end in a half-written line, possibly cut inside a
        # multi-byte character. Skip bad lines instead of losing the whole file.
        with file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") != "user" or rec.get("isMeta"):
                    continue
                if rec.get("userType") not in (None, "external"):
                    continue
                msg = rec.get("message")
                text = _text_of(msg.get("content") if isinstance(msg, dict) else None)
                if text is None:
                    continue
                turn += 1
                if not looks_like_question(text):
                    continue
                try:
                    asked_at = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
                except (KeyError, AttributeError, ValueError):
                    continue
                yield Question(
                    text=text.strip(),
                    asked_at=asked_at,
                    source=self.name,
                    session_id=rec.get("sessionId") or file.stem,
                    turn_index=turn,
                )

"""Codex local rollout JSONL under $CODEX_HOME/sessions or ~/.codex/sessions."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import Question
from .base import Source, looks_like_question


def _user_text(payload: dict) -> str | None:
    if payload.get("type") == "user_message":
        text = payload.get("message")
        return text if isinstance(text, str) else None
    if payload.get("type") != "item_completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "UserMessage":
        return None
    content = item.get("content")
    if not isinstance(content, list):
        return None
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def _internal_session(meta: dict) -> bool:
    source = meta.get("source")
    if source in ("subagent", "internal"):
        return True
    if isinstance(source, dict) and ("subagent" in source or "internal" in source):
        return True
    return meta.get("thread_source") not in (None, "user")


class CodexSource(Source):
    name = "codex"

    def __init__(self, path: Path | str | None = None):
        codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        super().__init__(path if path is not None else codex_root / "sessions")

    def questions(self) -> Iterator[Question]:
        if not self.path.exists():
            raise FileNotFoundError(
                f"Codex sessions not found: {self.path}. "
                "Pass a local rollout JSONL file or sessions directory."
            )
        files = [self.path] if self.path.is_file() else sorted(self.path.rglob("*.jsonl"))
        for file in files:
            yield from self._read_file(file)

    def _read_file(self, file: Path) -> Iterator[Question]:
        session_id = file.stem
        turn = 0
        # A running session may end in a partially written record, possibly cut inside a
        # multi-byte character. Skip bad lines instead of losing the whole directory.
        with file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue
                if rec.get("type") == "session_meta":
                    if _internal_session(payload):
                        return
                    if isinstance(payload.get("id"), str) and payload["id"]:
                        session_id = payload["id"]
                    continue
                # response_item can duplicate user input and contain injected
                # instructions. Only persisted user-facing events are evidence.
                if rec.get("type") != "event_msg":
                    continue
                text = _user_text(payload)
                if text is None:
                    continue
                turn += 1
                if not looks_like_question(text):
                    continue
                ts = rec.get("timestamp")
                if not isinstance(ts, str):
                    continue
                try:
                    asked_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                yield Question(
                    text=text.strip(),
                    asked_at=asked_at,
                    source=self.name,
                    session_id=session_id,
                    turn_index=turn,
                )

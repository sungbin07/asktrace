"""claude.ai data export: conversations.json (Settings → Privacy → Export data)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime

from ..models import Question
from .base import Source, looks_like_question


class ClaudeExportSource(Source):
    name = "claude-export"

    def questions(self) -> Iterator[Question]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for conv in data:
            session_id = conv.get("uuid") or conv.get("name", "")
            for i, msg in enumerate(conv.get("chat_messages", [])):
                if msg.get("sender") != "human":
                    continue
                text = msg.get("text") or _join_content(msg.get("content"))
                if not looks_like_question(text):
                    continue
                ts = msg.get("created_at") or conv.get("created_at")
                if not ts:
                    continue
                yield Question(
                    text=text.strip(),
                    asked_at=datetime.fromisoformat(ts.replace("Z", "+00:00")),
                    source=self.name,
                    session_id=session_id,
                    turn_index=i,
                )


def _join_content(content: object) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    )

"""ChatGPT data export: conversations.json (Settings → Data controls → Export)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone

from ..models import Question
from .base import Source, looks_like_question


class ChatGPTExportSource(Source):
    name = "chatgpt-export"

    def questions(self) -> Iterator[Question]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for conv in data:
            session_id = conv.get("conversation_id") or conv.get("id") or conv.get("title", "")
            messages = []
            for node in (conv.get("mapping") or {}).values():
                msg = node.get("message") if isinstance(node, dict) else None
                if not msg:
                    continue
                if (msg.get("author") or {}).get("role") != "user":
                    continue
                parts = (msg.get("content") or {}).get("parts") or []
                text = "\n".join(p for p in parts if isinstance(p, str))
                ts = msg.get("create_time") or conv.get("create_time")
                if ts is None:
                    continue
                messages.append((float(ts), text))
            messages.sort()
            for i, (ts, text) in enumerate(messages):
                if not looks_like_question(text):
                    continue
                yield Question(
                    text=text.strip(),
                    asked_at=datetime.fromtimestamp(ts, tz=timezone.utc),
                    source=self.name,
                    session_id=session_id,
                    turn_index=i,
                )

"""Codex local rollout JSONL under $CODEX_HOME/sessions or ~/.codex/sessions."""

from __future__ import annotations

import json
import os
import re
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


# Codex clients put app context (IDE tabs, mentioned or pasted files, a browser page) in front
# of the user's words and start those words with a heading. Codex strips the same prefix for
# display (USER_MESSAGE_BEGIN in codex-rs/protocol); the desktop app uses the shorter heading.
_CONTEXT_PREFIXES = (
    "# Context from my IDE setup:",
    "## Referenced chats with Codex:",
    "# Files mentioned by the user:",
    "# Files pasted by the user:",
    "<in-app-browser-context",
)
_REQUEST_HEADING_RE = re.compile(r"^## My request(?: for Codex)?:[ \t]*$", re.MULTILINE)
_BROWSER_CONTEXT_END = "</in-app-browser-context>"
# The desktop app sends answers to a question Codex asked in this envelope. Its question text
# was written by the model, so the reply is not a question the user asked.
_QUESTION_REPLY = ("<send_user_message_question_reply>", "</send_user_message_question_reply>")


def _request_text(text: str) -> str:
    """The user's own words in a Codex message, without the context the client added."""
    text = text.strip()
    if text.startswith(_CONTEXT_PREFIXES):
        heading = _REQUEST_HEADING_RE.search(text)
        if heading:
            # The first heading: anything after it, even the same heading again, is the user's.
            text = text[heading.end() :].strip()
        elif text.startswith("<in-app-browser-context") and _BROWSER_CONTEXT_END in text:
            text = text.split(_BROWSER_CONTEXT_END, 1)[1].strip()
    if text.startswith(_QUESTION_REPLY[0]) and text.endswith(_QUESTION_REPLY[1]):
        return ""
    return text


# The sources Codex itself lists as interactive (INTERACTIVE_SESSION_SOURCES in
# codex-rs/rollout). `exec` and `mcp` (app server) sessions are started by scripts or other
# agents, and subagent/internal threads by Codex, so their prompts are not the user's.
_INTERACTIVE_SOURCES = ("cli", "vscode", {"custom": "atlas"}, {"custom": "chatgpt"})


def _interactive_session(meta: dict) -> bool:
    # Codex reads a missing source as "vscode".
    if meta.get("source", "vscode") not in _INTERACTIVE_SOURCES:
        return False
    return meta.get("thread_source") in (None, "user")


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
                    if not _interactive_session(payload):
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
                # Count the turn before unwrapping so the turn indices, and with them the
                # fingerprints, of the other messages do not depend on this rule.
                turn += 1
                text = _request_text(text)
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
                    text=text,
                    asked_at=asked_at,
                    source=self.name,
                    session_id=session_id,
                    turn_index=turn,
                )

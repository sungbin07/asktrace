"""Readers that turn chat exports into `Question` records."""

from .base import Source, looks_like_question
from .chatgpt_export import ChatGPTExportSource
from .claude_code import ClaudeCodeSource
from .claude_export import ClaudeExportSource
from .codex import CodexSource
from .jsonl import JsonlSource

SOURCES: dict[str, type[Source]] = {
    "claude-code": ClaudeCodeSource,
    "codex": CodexSource,
    "claude-export": ClaudeExportSource,
    "chatgpt-export": ChatGPTExportSource,
    "jsonl": JsonlSource,
}

__all__ = ["SOURCES", "Source", "looks_like_question"]

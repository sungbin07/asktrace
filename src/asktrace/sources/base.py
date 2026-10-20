from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from ..models import Question

# Lines that are commands to a tool, not questions to a model.
_COMMAND_RE = re.compile(r"^\s*/[a-zA-Z]")
# Pasted tool output / system scaffolding that Claude Code wraps in tags.
_SCAFFOLD_RE = re.compile(r"^\s*<(system-reminder|command-name|local-command|task-notification)")
_URL_ONLY_RE = re.compile(r"^\s*https?://\S+\s*$")

MIN_CHARS = 8


def looks_like_question(text: str) -> bool:
    """Cheap filter: keep user turns that plausibly carry a question or request."""
    if not text or len(text.strip()) < MIN_CHARS:
        return False
    if _COMMAND_RE.match(text) or _SCAFFOLD_RE.match(text) or _URL_ONLY_RE.match(text):
        return False
    return True


class Source(ABC):
    """A reader for one export format."""

    name: str

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()

    @abstractmethod
    def questions(self) -> Iterator[Question]: ...

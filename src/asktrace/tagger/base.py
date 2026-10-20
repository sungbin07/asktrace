from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..models import Tag


class Tagger(ABC):
    name: str

    @abstractmethod
    def tag(self, questions: Sequence[tuple[str, str]]) -> list[Tag]:
        """`questions` is a sequence of (question_id, text). Returns one Tag per input."""

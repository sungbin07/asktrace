"""Core data types shared across sources, taggers, metrics and reports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEPTH_LABELS: dict[int, str] = {
    1: "define",  # "what is X?"
    2: "apply",  # "how do I use X?"
    3: "reason",  # "why X? X vs Y?"
    4: "debug",  # "X fails with ..., why?"
    5: "design",  # "how should I architect / trade off X?"
}

QUESTION_TYPES = ("factual", "howto", "conceptual", "troubleshoot", "design", "opinion", "other")


@dataclass(frozen=True)
class Question:
    """A single question the user asked an LLM."""

    text: str
    asked_at: datetime
    source: str
    session_id: str = ""
    turn_index: int = 0

    @property
    def fingerprint(self) -> str:
        """Stable id so re-ingesting the same export is idempotent."""
        key = f"{self.source}|{self.session_id}|{self.turn_index}|{self.text.strip()}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

    @property
    def asked_at_utc(self) -> datetime:
        dt = self.asked_at
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


@dataclass
class Tag:
    """What a question is about and how deep it goes."""

    question_id: str
    concepts: list[str]
    domain: str
    depth: int  # 1..5, see DEPTH_LABELS
    question_type: str  # one of QUESTION_TYPES
    tagger: str  # "heuristic" | "claude:<model>"
    confidence: float = 1.0
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.depth = max(1, min(5, int(self.depth)))
        if self.question_type not in QUESTION_TYPES:
            self.question_type = "other"
        self.concepts = _normalize_concepts(self.concepts)


def _normalize_concepts(concepts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in concepts:
        norm = " ".join(c.strip().lower().split())
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out[:8]

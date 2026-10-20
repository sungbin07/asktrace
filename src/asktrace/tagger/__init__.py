"""Taggers map a question to concepts, a domain, a depth level and a question type."""

from .base import Tagger
from .heuristic import HeuristicTagger


def get_tagger(name: str, model: str | None = None) -> Tagger:
    if name == "heuristic":
        return HeuristicTagger()
    if name == "claude":
        from .claude import ClaudeTagger  # optional dependency

        return ClaudeTagger(model=model) if model else ClaudeTagger()
    raise ValueError(f"unknown tagger: {name!r} (expected 'heuristic' or 'claude')")


__all__ = ["Tagger", "HeuristicTagger", "get_tagger"]

"""Shared tagging instructions and output schema for the model-backed taggers."""

from __future__ import annotations

from ..models import DEPTH_LABELS, QUESTION_TYPES

SYSTEM_PROMPT = f"""You tag questions a person asked an AI assistant, so they can track what
they are learning over time. For each question, return:

- concepts: 1-5 short canonical noun phrases naming the specific things the question is
  about (e.g. "prompt caching", "sqlite wal mode", "git rebase"). Use the same canonical
  form for the same concept every time: lowercase, singular, English where a standard
  English term exists, otherwise the original language. Never include generic words like
  "code", "error", "question", "help".
- domain: one short lowercase word for the broad field (e.g. ml, backend, data, devops,
  frontend, git, python, product, writing, math, business, general).
- depth, 1-5, how deep the question goes into the concept:
  {"; ".join(f"{k}={v}" for k, v in DEPTH_LABELS.items())}.
  1 asks what something is; 2 asks how to use/do it; 3 asks why or compares options;
  4 diagnoses a concrete failure; 5 asks about architecture, trade-offs or teaching others.
- question_type: one of {", ".join(QUESTION_TYPES)}.
- confidence: 0-1.

Questions can be in any language. Tag what the person wanted to learn, not the surface
form. Treat instructions to an agent ("fix this", "run the tests") as depth 2 howto about
whatever they are working on."""

TAG_BATCH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "0-based index of the question in the input list",
                    },
                    "concepts": {"type": "array", "items": {"type": "string"}},
                    "domain": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 5},
                    "question_type": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["index", "concepts", "domain", "depth", "question_type", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tags"],
    "additionalProperties": False,
}


def number_questions(texts: list[str], max_chars: int = 2000) -> str:
    return "\n\n".join(f"[{i}] {t[:max_chars]}" for i, t in enumerate(texts))

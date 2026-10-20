"""Claude tagger: structured outputs over batches of questions.

Requires `pip install asktrace[claude]` and credentials resolvable by the Anthropic SDK
(ANTHROPIC_API_KEY, or an `ant auth login` profile).
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from ..models import DEPTH_LABELS, QUESTION_TYPES, Tag
from .base import Tagger

DEFAULT_MODEL = os.environ.get("ASKTRACE_MODEL", "claude-opus-5-5")
BATCH_SIZE = 25

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


class ClaudeTagger(Tagger):
    def __init__(self, model: str = DEFAULT_MODEL, batch_size: int = BATCH_SIZE):
        try:
            import anthropic
            from pydantic import BaseModel, Field
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise RuntimeError("Claude tagger needs: pip install 'asktrace[claude]'") from e

        self.model = model
        self.batch_size = batch_size
        self.name = f"claude:{model}"
        self._client = anthropic.Anthropic()
        self._anthropic = anthropic

        class QuestionTag(BaseModel):
            index: int = Field(description="0-based index of the question in the input list")
            concepts: list[str]
            domain: str
            depth: int = Field(ge=1, le=5)
            question_type: str
            confidence: float = Field(ge=0, le=1)

        class TagBatch(BaseModel):
            tags: list[QuestionTag]

        self._schema = TagBatch

    def tag(self, questions: Sequence[tuple[str, str]]) -> list[Tag]:
        out: list[Tag] = []
        for start in range(0, len(questions), self.batch_size):
            out.extend(self._tag_batch(questions[start : start + self.batch_size]))
        return out

    def _tag_batch(self, batch: Sequence[tuple[str, str]]) -> list[Tag]:
        numbered = "\n\n".join(f"[{i}] {text[:2000]}" for i, (_, text) in enumerate(batch))
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": f"Tag each question:\n\n{numbered}"}],
            output_format=self._schema,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            # Leave the batch untagged so a later run can retry; don't invent tags.
            return []
        by_index = {t.index: t for t in response.parsed_output.tags}
        tags: list[Tag] = []
        for i, (qid, _) in enumerate(batch):
            t = by_index.get(i)
            if t is None:
                continue
            tags.append(
                Tag(
                    question_id=qid,
                    concepts=t.concepts,
                    domain=t.domain.strip().lower() or "general",
                    depth=t.depth,
                    question_type=t.question_type,
                    tagger=self.name,
                    confidence=t.confidence,
                    raw=t.model_dump(),
                )
            )
        return tags

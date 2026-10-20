"""Claude Code tagger: shells out to `claude -p`, so a Claude subscription (Pro/Max) works
without an API key. Requires the Claude Code CLI to be installed and logged in.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence

from ..models import Tag
from .base import Tagger
from .prompt import SYSTEM_PROMPT, TAG_BATCH_SCHEMA, number_questions

BATCH_SIZE = 25


class ClaudeCodeTagger(Tagger):
    def __init__(
        self,
        model: str | None = None,
        binary: str = "claude",
        batch_size: int = BATCH_SIZE,
        timeout: float = 600.0,
    ):
        self.binary = shutil.which(binary) or binary
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout
        self.name = f"claude-code:{model or 'default'}"

    def _command(self) -> list[str]:
        cmd = [
            self.binary,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(TAG_BATCH_SCHEMA),
            "--tools",
            "",
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def _run(self, prompt: str) -> dict:
        try:
            proc = subprocess.run(
                self._command(),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "Claude Code CLI not found. Install it (https://code.claude.com)"
                " or use --tagger claude with an API key"
            ) from e
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"claude -p returned non-JSON output (exit {proc.returncode}): "
                f"{(proc.stdout or proc.stderr)[:300]}"
            ) from e
        if payload.get("is_error"):
            raise RuntimeError(f"claude -p failed: {payload.get('result', '')[:300]}")
        return payload

    def tag(self, questions: Sequence[tuple[str, str]]) -> list[Tag]:
        out: list[Tag] = []
        for start in range(0, len(questions), self.batch_size):
            out.extend(self._tag_batch(questions[start : start + self.batch_size]))
        return out

    def _tag_batch(self, batch: Sequence[tuple[str, str]]) -> list[Tag]:
        prompt = (
            f"{SYSTEM_PROMPT}\n\nTag each question:\n\n{number_questions([t for _, t in batch])}"
        )
        payload = self._run(prompt)
        structured = payload.get("structured_output")
        if not isinstance(structured, dict):
            # No structured output (e.g. refusal). Leave the batch untagged for a retry.
            return []
        by_index = {t.get("index"): t for t in structured.get("tags", []) if isinstance(t, dict)}
        tags: list[Tag] = []
        for i, (qid, _) in enumerate(batch):
            t = by_index.get(i)
            if t is None:
                continue
            tags.append(
                Tag(
                    question_id=qid,
                    concepts=[str(c) for c in t.get("concepts", [])],
                    domain=str(t.get("domain", "")).strip().lower() or "general",
                    depth=int(t.get("depth", 2)),
                    question_type=str(t.get("question_type", "other")),
                    tagger=self.name,
                    confidence=float(t.get("confidence", 0.5)),
                    raw=t,
                )
            )
        return tags

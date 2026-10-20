import json
import subprocess

import pytest

from asktrace.tagger import get_tagger
from asktrace.tagger.claude_code import ClaudeCodeTagger


def _fake_run(payload: dict):
    calls = []

    def run(cmd, input, capture_output, text, timeout, check):
        calls.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return run, calls


def test_parses_structured_output(monkeypatch):
    payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "tags": [
                {
                    "index": 0,
                    "concepts": ["Prompt Caching"],
                    "domain": "ML",
                    "depth": 1,
                    "question_type": "factual",
                    "confidence": 0.9,
                },
                {
                    "index": 1,
                    "concepts": ["git rebase"],
                    "domain": "git",
                    "depth": 9,
                    "question_type": "weird",
                    "confidence": 0.7,
                },
            ]
        },
    }
    run, calls = _fake_run(payload)
    monkeypatch.setattr(subprocess, "run", run)
    tags = ClaudeCodeTagger(model="sonnet", binary="claude-fake").tag(
        [
            ("a", "prompt caching이 뭐야?"),
            ("b", "rebase conflict 이어서 하는 법"),
            ("c", "untagged"),
        ]
    )
    assert [t.question_id for t in tags] == ["a", "b"]
    assert tags[0].concepts == ["prompt caching"] and tags[0].domain == "ml"
    assert tags[1].depth == 5 and tags[1].question_type == "other"
    assert tags[0].tagger == "claude-code:sonnet"
    cmd, prompt = calls[0]
    assert "-p" in cmd and "--json-schema" in cmd and cmd[-2:] == ["--model", "sonnet"]
    assert "[0] prompt caching이 뭐야?" in prompt and "[2] untagged" in prompt


def test_error_payload_raises(monkeypatch):
    run, _ = _fake_run({"is_error": True, "result": "Not logged in · Please run /login"})
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Not logged in"):
        ClaudeCodeTagger(binary="claude-fake").tag([("a", "anything here")])


def test_batches_of_25(monkeypatch):
    run, calls = _fake_run({"is_error": False, "structured_output": {"tags": []}})
    monkeypatch.setattr(subprocess, "run", run)
    assert (
        ClaudeCodeTagger(binary="claude-fake").tag(
            [(str(i), f"q{i} long enough") for i in range(60)]
        )
        == []
    )
    assert len(calls) == 3


def test_registered_in_get_tagger():
    assert isinstance(get_tagger("claude-code"), ClaudeCodeTagger)

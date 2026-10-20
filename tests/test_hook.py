import io
import json
import sys
from pathlib import Path

import pytest

from asktrace.cli import main
from asktrace.store import Store

FIX = Path(__file__).parent / "fixtures"
TRANSCRIPT = FIX / "claude_code_session.jsonl"


def _payload(path: Path | str) -> str:
    # Shape documented for SessionEnd at https://code.claude.com/docs/en/hooks
    return json.dumps(
        {
            "session_id": "s1",
            "transcript_path": str(path),
            "cwd": "/tmp",
            "hook_event_name": "SessionEnd",
            "reason": "other",
        }
    )


def _run_hook(monkeypatch, stdin: str, *args: str) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return main(list(args))


def test_hook_ingests_the_transcript_silently(tmp_path, monkeypatch, capsys):
    db = tmp_path / "a.db"
    assert _run_hook(monkeypatch, _payload(TRANSCRIPT), "--db", str(db), "hook") == 0
    out, err = capsys.readouterr()
    assert out == "" and err == ""
    with Store(db) as store:
        assert store.count_questions() == 3
        assert store.count_tagged() == 0  # no tagging unless asked
    # Re-running at the next session end adds nothing.
    assert _run_hook(monkeypatch, _payload(TRANSCRIPT), "--db", str(db), "hook") == 0
    with Store(db) as store:
        assert store.count_questions() == 3


def test_hook_heuristic_tags_only_this_session(tmp_path, monkeypatch, capsys):
    db = tmp_path / "a.db"
    assert (
        main(["--db", str(db), "ingest", "chatgpt-export", str(FIX / "chatgpt_export.json")]) == 0
    )
    capsys.readouterr()
    args = ("--db", str(db), "hook", "--tag", "heuristic")
    assert _run_hook(monkeypatch, _payload(TRANSCRIPT), *args) == 0
    assert capsys.readouterr().out == ""
    with Store(db) as store:
        assert store.count_questions() == 5
        assert store.count_tagged() == 3
        assert {r["source"] for r in store.untagged()} == {"chatgpt-export"}


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "not json {",
        "[1, 2]",
        json.dumps({"session_id": "s1"}),
        json.dumps({"transcript_path": 42}),
        _payload("/nonexistent/session.jsonl"),
        _payload(FIX),  # a directory must not trigger a full re-ingest
    ],
)
def test_bad_payload_still_exits_zero(tmp_path, monkeypatch, capsys, stdin):
    db = tmp_path / "a.db"
    assert _run_hook(monkeypatch, stdin, "--db", str(db), "hook") == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("asktrace hook:")
    if db.exists():
        with Store(db) as store:
            assert store.count_questions() == 0


def test_bad_hook_arguments_still_exit_zero(tmp_path, monkeypatch, capsys):
    db = tmp_path / "a.db"
    args = ("--db", str(db), "hook", "--tag", "claude-code")
    assert _run_hook(monkeypatch, _payload(TRANSCRIPT), *args) == 0
    assert capsys.readouterr().out == ""
    # Other subcommands keep normal argparse errors.
    with pytest.raises(SystemExit) as e:
        main(["--db", str(db), "tag", "--tagger", "nope"])
    assert e.value.code == 2

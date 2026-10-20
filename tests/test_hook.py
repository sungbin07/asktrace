import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from asktrace.cli import _is_hook_call, main
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
    assert not db.exists()  # rejected before the database is opened


def test_bad_hook_arguments_still_exit_zero(tmp_path, monkeypatch, capsys):
    db = tmp_path / "a.db"
    args = ("--db", str(db), "hook", "--tag", "claude-code")
    assert _run_hook(monkeypatch, _payload(TRANSCRIPT), *args) == 0
    assert capsys.readouterr().out == ""
    # Other subcommands keep normal argparse errors.
    with pytest.raises(SystemExit) as e:
        main(["--db", str(db), "tag", "--tagger", "nope"])
    assert e.value.code == 2


def test_unusable_database_still_exits_zero(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    args = ("--db", str(blocker / "a.db"), "hook")
    assert _run_hook(monkeypatch, _payload(TRANSCRIPT), *args) == 0
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("asktrace hook:")


def test_closed_stderr_does_not_spill_to_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stderr", None)
    assert _run_hook(monkeypatch, "not json", "--db", str(tmp_path / "a.db"), "hook") == 0
    assert capsys.readouterr().out == ""


def test_half_written_transcript_keeps_the_good_lines(tmp_path, monkeypatch):
    good = TRANSCRIPT.read_bytes().splitlines(keepends=True)[0]  # one valid question
    partial = '{"type":"user","message":{"content":"한글'.encode()[:-1]  # cut mid-character
    transcript = tmp_path / "live.jsonl"
    transcript.write_bytes(good + b"[1, 2]\n" + b'{"type": "user", "message": 3}\n' + partial)
    db = tmp_path / "a.db"
    assert _run_hook(monkeypatch, _payload(transcript), "--db", str(db), "hook") == 0
    with Store(db) as store:
        assert store.count_questions() == 1


def test_real_process_with_non_ascii_path(tmp_path):
    folder = tmp_path / "프로젝트"
    folder.mkdir()
    transcript = folder / "s.jsonl"
    transcript.write_bytes(TRANSCRIPT.read_bytes())
    db = tmp_path / "a.db"
    proc = subprocess.run(
        [sys.executable, "-m", "asktrace.cli", "--db", str(db), "hook"],
        input=_payload(transcript).encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0 and proc.stdout == b"", proc.stderr
    with Store(db) as store:
        assert store.count_questions() == 3


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["hook"], True),
        (["--db", "x.db", "hook", "--tag", "nope"], True),
        (["--db=x.db", "hook"], True),
        (["--d", "x.db", "hook"], True),  # argparse accepts the abbreviation
        (["--db", "hook", "ingest", "jsonl"], False),  # a database file named "hook"
        (["ingest", "jsonl", "hook"], False),  # a source file named "hook"
        (["--version"], False),
    ],
)
def test_is_hook_call(argv, expected):
    assert _is_hook_call(argv) is expected

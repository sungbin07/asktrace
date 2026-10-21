import json
from pathlib import Path

import pytest

from asktrace.cli import main
from asktrace.sources import CodexSource
from asktrace.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "codex_session.jsonl"


def _write_records(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _event(text, timestamp="2026-09-01T09:00:00Z"):
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": "user_message", "message": text},
    }


def test_legacy_events_exclude_mirrored_input_and_tool_output():
    questions = list(CodexSource(FIXTURE).questions())
    assert [q.text for q in questions] == [
        "git rebase와 merge 차이가 뭐야?",
        "git rebase 하다가 conflict 났는데 이어서 하는 방법 알려줘",
    ]
    assert all(q.source == "codex" and q.session_id == "codex-demo" for q in questions)
    assert [q.turn_index for q in questions] == [1, 4]
    assert questions[1].asked_at_utc.isoformat() == "2026-09-02T01:00:00+00:00"


def test_default_path_uses_codex_home_and_recurses(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    nested = tmp_path / "sessions" / "2026" / "09" / "01"
    nested.mkdir(parents=True)
    (nested / "rollout.jsonl").write_bytes(FIXTURE.read_bytes())
    # An archive is only included when explicitly selected.
    archive = tmp_path / "archived_sessions"
    archive.mkdir()
    _write_records(archive / "old.jsonl", [_event("explain sqlite WAL mode")])
    assert len(list(CodexSource().questions())) == 2
    assert len(list(CodexSource(archive).questions())) == 1


def test_default_path_without_codex_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert CodexSource().path == tmp_path / ".codex" / "sessions"


def test_paginated_completed_user_items(tmp_path):
    path = tmp_path / "rollout.jsonl"
    user_item = {
        "type": "UserMessage",
        "id": "user-1",
        "content": [
            {"type": "text", "text": "Compare SQLite WAL"},
            {"type": "local_image", "path": "/example/image.png"},
            {"type": "text", "text": "and rollback journals."},
        ],
    }
    records = [
        {
            "type": "session_meta",
            "payload": {"id": "paginated-demo", "history_mode": "paginated", "source": "vscode"},
        }
    ]
    for kind, item in [
        ("item_started", user_item),
        ("item_completed", user_item),
        ("item_completed", {"type": "AgentMessage", "content": [{"type": "Text", "text": "hi"}]}),
        ("item_completed", {"type": "UserMessage", "content": [{"type": "local_image"}]}),
    ]:
        records.append(
            {
                "timestamp": "2026-09-01T09:00:00Z",
                "type": "event_msg",
                "payload": {"type": kind, "item": item},
            }
        )
    _write_records(path, records)
    (question,) = CodexSource(path).questions()
    assert question.text == "Compare SQLite WAL\nand rollback journals."
    assert question.session_id == "paginated-demo"


@pytest.mark.parametrize(
    "meta",
    [
        {"source": {"subagent": {"spawn": {"parent_thread_id": "parent"}}}},
        {"source": "subagent"},
        {"source": {"internal": "memory_consolidation"}},
        {"source": "internal"},
        {"source": "cli", "thread_source": "guardian_review"},
    ],
)
def test_internal_threads_do_not_count_as_human_questions(tmp_path, meta):
    path = tmp_path / "child.jsonl"
    _write_records(
        path,
        [{"type": "session_meta", "payload": {"id": "child", **meta}}, _event("review this code")],
    )
    assert list(CodexSource(path).questions()) == []


def test_malformed_records_do_not_prevent_later_valid_questions(tmp_path):
    path = tmp_path / "rollout.jsonl"
    _write_records(
        path,
        [
            None,
            [],
            {"type": "event_msg", "payload": None},
            _event(["not a string"]),
            _event("question without a timestamp", None),
            _event("question with an invalid date", "not-a-date"),
            {"type": "event_msg", "payload": {"type": "item_completed", "item": None}},
            _event("explain sqlite WAL mode"),
        ],
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type":')
    (question,) = CodexSource(path).questions()
    assert question.text == "explain sqlite WAL mode"
    assert question.session_id == "rollout"


def test_live_rollout_cut_inside_a_character_keeps_other_questions(tmp_path, capsys):
    # A running session can end mid-way through a multi-byte character. That line is
    # skipped; earlier lines and the other files in the directory are still ingested.
    (tmp_path / "done.jsonl").write_bytes(FIXTURE.read_bytes())
    live = tmp_path / "live.jsonl"
    _write_records(live, [_event("explain sqlite WAL mode")])
    cut = json.dumps(_event("트랜잭션 격리 수준이 뭐야?"), ensure_ascii=False).encode()
    with live.open("ab") as fh:
        fh.write(cut[: cut.index("격".encode()) + 1])
    (question,) = CodexSource(live).questions()
    assert question.text == "explain sqlite WAL mode"
    assert main(["--db", str(tmp_path / "codex.db"), "ingest", "codex", str(tmp_path)]) == 0
    assert "ingested 3 new questions" in capsys.readouterr().out


def test_repeat_import_copies_and_appended_repeated_questions(tmp_path):
    path = tmp_path / "original.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    (tmp_path / "copy.jsonl").write_bytes(FIXTURE.read_bytes())
    with Store(tmp_path / "questions.db") as store:
        assert store.add_questions(CodexSource(tmp_path).questions()) == 2
        assert store.add_questions(CodexSource(tmp_path).questions()) == 0
        # The same text asked in another turn is evidence, not a duplicate export.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_event("git rebase와 merge 차이가 뭐야?")) + "\n")
        assert store.add_questions(CodexSource(path).questions()) == 1
        assert store.count_questions() == 3


def test_missing_session_path_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Codex sessions not found"):
        list(CodexSource(tmp_path / "missing").questions())


def test_codex_cli_ingest_tag_report(tmp_path, capsys):
    db_args = ["--db", str(tmp_path / "codex.db")]
    assert main([*db_args, "ingest", "codex", str(FIXTURE)]) == 0
    assert "ingested 2 new questions" in capsys.readouterr().out
    assert main([*db_args, "ingest", "codex", str(FIXTURE)]) == 0
    assert "ingested 0 new questions" in capsys.readouterr().out
    assert main([*db_args, "tag"]) == 0
    assert "tagged 2/2 with heuristic" in capsys.readouterr().out
    assert main([*db_args, "report", "--format", "json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["questions"] == 2
    assert summary["concepts"] > 0

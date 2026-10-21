import json
from pathlib import Path

import pytest

from asktrace.cli import main
from asktrace.sources import CodexSource
from asktrace.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "codex_session.jsonl"
# Synthetic text in the record layout of a Codex desktop-app (paginated) rollout.
DESKTOP_FIXTURE = Path(__file__).parent / "fixtures" / "codex_desktop_context.jsonl"


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
        # Started by a script (`codex exec`) or another agent (Codex as MCP/app server).
        {"source": "exec"},
        {"source": "mcp"},
        {"source": "unknown"},
        {"source": {"custom": "some-integration"}},
    ],
)
def test_non_interactive_threads_do_not_count_as_human_questions(tmp_path, meta):
    path = tmp_path / "child.jsonl"
    _write_records(
        path,
        [{"type": "session_meta", "payload": {"id": "child", **meta}}, _event("review this code")],
    )
    assert list(CodexSource(path).questions()) == []


@pytest.mark.parametrize(
    "meta",
    [
        {"source": "cli"},
        {"source": "vscode", "thread_source": "user"},
        {},  # Codex reads a missing source as vscode
        {"source": {"custom": "atlas"}},
        {"source": {"custom": "chatgpt"}},
    ],
)
def test_interactive_threads_count_as_human_questions(tmp_path, meta):
    path = tmp_path / "session.jsonl"
    _write_records(
        path,
        [{"type": "session_meta", "payload": {"id": "s", **meta}}, _event("review this code")],
    )
    assert [q.text for q in CodexSource(path).questions()] == ["review this code"]


def test_app_context_is_dropped_and_only_the_request_is_kept():
    questions = list(CodexSource(DESKTOP_FIXTURE).questions())
    assert [(q.turn_index, q.text) for q in questions] == [
        (1, "Why does this schema need a covering index?"),
        (2, "Is this pricing table accessible to screen readers?"),
        # 3: pasted files with an empty request; 4: answers to a question Codex asked.
        (5, "explain sqlite WAL checkpoints"),
        (6, "How do I retry a locked SQLite write?"),
    ]
    assert all(q.session_id == "desktop-demo" for q in questions)


def test_messages_without_app_context_keep_their_fingerprints():
    # Fingerprints from before app context was stripped: re-importing an unwrapped
    # message must not add a second copy of it.
    assert [q.fingerprint for q in CodexSource(FIXTURE).questions()] == [
        "6ca95fd2ce014df1135c62bf",
        "e4037899ed3f651f96396926",
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        # Without app context in front, a request heading is the user's own text.
        ("## My request:\nexplain sqlite WAL mode", "## My request:\nexplain sqlite WAL mode"),
        (
            "# Notes\nkeep this\n## My request:\nexplain WAL",
            "# Notes\nkeep this\n## My request:\nexplain WAL",
        ),
        # After the first heading, everything is the user's, even the same heading.
        (
            "# Files mentioned by the user:\n\n## a.md: /x/a.md\n\n## My request:\n"
            "Rewrite this prompt:\n## My request:\nkeep both",
            "Rewrite this prompt:\n## My request:\nkeep both",
        ),
        (
            "## Referenced chats with Codex:\nThese are live references to Codex tasks.\n"
            '[{"threadId":"t-1"}]\n## My request for Codex:\nContinue the migration plan',
            "Continue the migration plan",
        ),
        (
            '<in-app-browser-context source="in-app-browser">\nA page\n</in-app-browser-context>\n'
            "What does this page load first?",
            "What does this page load first?",
        ),
        (
            "# Files mentioned by the user:\r\n\r\n## a.md: /x/a.md\r\n\r\n## My request:\r\n"
            "Why is this slow?",
            "Why is this slow?",
        ),
        (
            "## Referenced ChatGPT conversation:\nA shared chat about caching\n\n## My request:\n"
            "Which cache fits a read-heavy API?",
            "Which cache fits a read-heavy API?",
        ),
        # A heading-like line inside the browser page is page text, not the user's request.
        (
            '<in-app-browser-context source="in-app-browser">\n## My request:\nnot mine\n'
            "</in-app-browser-context>\n\n## My request:\nWhat does this page load first?",
            "What does this page load first?",
        ),
    ],
)
def test_request_heading_is_only_split_after_app_context(tmp_path, text, expected):
    path = tmp_path / "rollout.jsonl"
    _write_records(path, [_event(text)])
    assert [q.text for q in CodexSource(path).questions()] == [expected]


def test_question_reply_after_ide_context_is_not_a_question(tmp_path):
    path = tmp_path / "rollout.jsonl"
    reply = (
        "<send_user_message_question_reply>\n"
        '[{"questionItemId":"[\\"q\\",0]","question":"Which database?","answer":"SQLite"}]\n'
        "</send_user_message_question_reply>"
    )
    ide = (
        "# Context from my IDE setup:\n\n## Open tabs:\n- a.py: a.py\n\n## My request for Codex:\n"
    )
    _write_records(path, [_event(ide + reply), _event("explain sqlite WAL mode")])
    assert [(q.turn_index, q.text) for q in CodexSource(path).questions()] == [
        (2, "explain sqlite WAL mode")
    ]


def test_automation_heartbeat_is_not_a_question(tmp_path):
    path = tmp_path / "rollout.jsonl"
    heartbeat = (
        "<heartbeat>\n<automation_id>daily-review</automation_id>\n"
        "<current_time_iso>2026-09-01T09:00:00Z</current_time_iso>\n"
        "<instructions>Summarise open pull requests and failing checks.</instructions>\n"
        "</heartbeat>"
    )
    _write_records(path, [_event(heartbeat), _event("explain sqlite WAL mode")])
    assert [(q.turn_index, q.text) for q in CodexSource(path).questions()] == [
        (2, "explain sqlite WAL mode")
    ]


def test_rollout_removed_while_ingesting_is_skipped(tmp_path):
    # Codex compresses older rollouts to .zst and archives threads, deleting the .jsonl.
    _write_records(tmp_path / "a.jsonl", [_event("explain sqlite WAL mode")])
    _write_records(tmp_path / "b.jsonl", [_event("why is my write locked?")])
    questions = CodexSource(tmp_path).questions()
    first = next(questions)  # the directory is listed before the first file is read
    (tmp_path / "b.jsonl").unlink()
    assert [first.text, *(q.text for q in questions)] == ["explain sqlite WAL mode"]


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


def test_cli_without_codex_sessions_prints_one_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    assert main(["--db", str(tmp_path / "codex.db"), "ingest", "codex"]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith("asktrace ingest: Codex sessions not found")
    assert out.err.count("\n") == 1 and "Traceback" not in out.err


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


# -- compressed rollouts (.jsonl.zst) ------------------------------------------------------


def _zstd(data: bytes) -> bytes:
    try:
        from compression import zstd
    except ImportError:
        zstandard = pytest.importorskip("zstandard", reason="no zstd encoder installed")
        return zstandard.ZstdCompressor(level=3).compress(data)
    return zstd.compress(data, level=3)


def _compress(plain: Path) -> Path:
    """Do what Codex does to a cold rollout: write `<name>.jsonl.zst`, delete the `.jsonl`."""
    compressed = plain.with_name(plain.name + ".zst")
    compressed.write_bytes(_zstd(plain.read_bytes()))
    plain.unlink()
    return compressed


def _seen(questions):
    return [(q.fingerprint, q.session_id, q.turn_index, q.text) for q in questions]


def _records_bytes(records):
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode()


_CUT = json.dumps(_event("트랜잭션 격리 수준이 뭐야?"), ensure_ascii=False).encode()
_HEARTBEAT = "<heartbeat>\n<instructions>Summarise open pull requests.</instructions>\n</heartbeat>"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(FIXTURE.read_bytes(), id="legacy-events"),
        pytest.param(DESKTOP_FIXTURE.read_bytes(), id="app-context-and-question-reply"),
        pytest.param(
            _records_bytes([_event(_HEARTBEAT), _event("explain sqlite WAL mode")]),
            id="heartbeat-without-session-meta",
        ),
        pytest.param(
            _records_bytes(
                [
                    {"type": "session_meta", "payload": {"id": "x", "source": "exec"}},
                    _event("review this code"),
                ]
            ),
            id="exec-session",
        ),
        pytest.param(
            _records_bytes([_event("explain sqlite WAL mode")]) + b'{"type":',
            id="half-written-last-line",
        ),
        pytest.param(
            _records_bytes([_event("explain sqlite WAL mode")])
            + _CUT[: _CUT.index("격".encode()) + 1],
            id="cut-inside-a-character",
        ),
    ],
)
def test_compressed_rollout_gives_the_same_questions_as_plain(tmp_path, content):
    plain = tmp_path / "2026" / "09" / "01" / "rollout-2026-09-01T09-00-00-demo.jsonl"
    plain.parent.mkdir(parents=True)
    plain.write_bytes(content)
    expected = _seen(CodexSource(tmp_path).questions())
    compressed = _compress(plain)
    assert _seen(CodexSource(tmp_path).questions()) == expected
    assert _seen(CodexSource(compressed).questions()) == expected


def test_session_compressed_after_ingest_is_not_ingested_again(tmp_path, capsys):
    db_args = ["--db", str(tmp_path / "codex.db")]
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "rollout-a.jsonl").write_bytes(FIXTURE.read_bytes())  # session id from meta
    _write_records(sessions / "rollout-b.jsonl", [_event("explain sqlite WAL mode")])  # from name
    assert main([*db_args, "ingest", "codex", str(sessions)]) == 0
    assert "ingested 3 new questions (3 total)" in capsys.readouterr().out
    for plain in sorted(sessions.glob("*.jsonl")):
        _compress(plain)
    assert main([*db_args, "ingest", "codex", str(sessions)]) == 0
    assert "ingested 0 new questions (3 total)" in capsys.readouterr().out
    # Resuming a compressed session brings back the .jsonl, which Codex appends to.
    with Store(tmp_path / "codex.db") as store:
        resumed = sessions / "rollout-b.jsonl"
        _write_records(resumed, [_event("explain sqlite WAL mode"), _event("and checkpoints?")])
        assert store.add_questions(CodexSource(sessions).questions()) == 1
        resumed.unlink()
        assert store.add_questions(CodexSource(sessions).questions()) == 0
        assert store.count_questions() == 4


def test_plain_rollout_wins_over_its_compressed_copy(tmp_path):
    # Both forms exist while Codex compresses a session or restores one for a resume.
    plain = tmp_path / "rollout-a.jsonl"
    _write_records(plain, [_event("explain sqlite WAL mode")])
    compressed = plain.with_name(plain.name + ".zst")
    compressed.write_bytes(_zstd(plain.read_bytes()))
    _write_records(plain, [_event("explain sqlite WAL mode"), _event("and checkpoints?")])
    questions = [(q.turn_index, q.text) for q in CodexSource(tmp_path).questions()]
    assert questions == [(1, "explain sqlite WAL mode"), (2, "and checkpoints?")]
    assert [q.text for q in CodexSource(compressed).questions()] == [t for _, t in questions]


def test_rollout_compressed_while_ingesting_is_read_from_the_zst(tmp_path):
    _write_records(tmp_path / "a.jsonl", [_event("explain sqlite WAL mode")])
    _write_records(tmp_path / "b.jsonl", [_event("why is my write locked?")])
    questions = CodexSource(tmp_path).questions()
    first = next(questions)  # the directory is listed before the first file is read
    _compress(tmp_path / "b.jsonl")
    assert [first.text, *(q.text for q in questions)] == [
        "explain sqlite WAL mode",
        "why is my write locked?",
    ]


def test_without_a_decoder_compressed_rollouts_are_skipped_with_one_note(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("asktrace.sources.codex._zstd_decoder", lambda: None)
    _write_records(tmp_path / "rollout-new.jsonl", [_event("explain sqlite WAL mode")])
    (tmp_path / "rollout-old.jsonl.zst").write_bytes(b"compressed")
    (tmp_path / "rollout-older.jsonl.zst").write_bytes(b"compressed")
    assert main(["--db", str(tmp_path / "codex.db"), "ingest", "codex", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert "ingested 1 new questions" in out.out
    assert out.err.startswith("asktrace: skipped 2 compressed Codex rollouts (.jsonl.zst)")
    assert "asktrace[codex]" in out.err and out.err.count("\n") == 1


def test_damaged_compressed_rollout_keeps_earlier_questions(tmp_path, capsys):
    _write_records(tmp_path / "rollout-a.jsonl", [_event("explain sqlite WAL mode")])
    (tmp_path / "rollout-b.jsonl.zst").write_bytes(b"not zstd")
    # A complete frame followed by a frame cut short.
    first = _records_bytes([_event("why is my write locked?")])
    rest = _zstd(_records_bytes([_event("and checkpoints?")] * 50))
    (tmp_path / "rollout-c.jsonl.zst").write_bytes(_zstd(first) + rest[: len(rest) // 2])
    texts = [q.text for q in CodexSource(tmp_path).questions()]
    assert texts == ["explain sqlite WAL mode", "why is my write locked?"]
    err = capsys.readouterr().err
    assert err.startswith("asktrace: ") and "could not be fully decompressed" in err
    assert err.count("\n") == 1

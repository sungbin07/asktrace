import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import pytest

from asktrace import journeys, metrics
from asktrace.cli import main
from asktrace.models import Question
from asktrace.store import Store
from asktrace.tagger import HeuristicTagger


def _question(text, turn=0, *, session="session-a", source="codex", at="2026-09-01T15:30:00"):
    return Question(
        text,
        datetime.fromisoformat(at).replace(tzinfo=timezone.utc),
        source,
        session,
        turn,
    )


@pytest.fixture
def questions():
    return [
        _question("What is a cache?", 1, at="2026-09-01T14:00:00"),
        _question("What evidence would show the database is slow?", 2),
        _question("How should I measure the external API latency?", 3),
    ]


def _visible(store):
    return journeys.select(store.all_journeys(), tz=timezone.utc)


def test_session_groups_are_stable_ordered_and_source_scoped(tmp_path, questions):
    other = _question("Explain git rebase and merge", source="claude-code")
    loose = [
        _question("Explain SQLite WAL mode", session="", source="jsonl"),
        _question("Explain Python context managers", session="", source="jsonl"),
    ]
    db = tmp_path / "a.db"
    with Store(db) as store:
        store.add_questions([questions[2], other, *loose, questions[1], questions[0]])
        groups = _visible(store)
        assert len(groups) == 4
        codex = next(g for g in groups if len(g["questions"]) == 3)
        assert [q["id"] for q in codex["questions"]] == [q.fingerprint for q in questions]
        assert all(g["kind"] == "session" and g["reflection"] is None for g in groups)
        first_ids = {g["id"] for g in groups}
        assert store.count_tagged() == 0
    with Store(db) as store:
        assert {g["id"] for g in _visible(store)} == first_ids


def test_existing_database_upgrade_preserves_questions_tags_and_report(tmp_path):
    db = tmp_path / "legacy.db"
    # The two original tables, created without any journey-aware code.
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE questions (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL, asked_at TEXT NOT NULL, text TEXT NOT NULL
            );
            CREATE TABLE tags (
                question_id TEXT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
                concepts TEXT NOT NULL, domain TEXT NOT NULL, depth INTEGER NOT NULL,
                question_type TEXT NOT NULL, tagger TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0, raw TEXT NOT NULL DEFAULT '{}'
            );
        """)
        conn.execute(
            "INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-id", "claude-code", "legacy-session", 1, "2026-09-01T09:00:00+00:00",
             "What is SQLite WAL mode?"),
        )
        conn.execute(
            "INSERT INTO tags VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-id", '["sqlite wal"]', "data", 1, "factual", "heuristic", 1.0, "{}"),
        )
        before = [list(conn.execute(f"SELECT * FROM {table}")) for table in ("questions", "tags")]
    with Store(db) as store:
        (group,) = _visible(store)
        assert group["questions"][0]["id"] == "legacy-id"
        store.save_reflection(group["id"], learned="I checked how readers use WAL.")
        after = [
            [tuple(row) for row in store.conn.execute(f"SELECT * FROM {table}")]
            for table in ("questions", "tags")
        ]
        assert after == before
        summary = metrics.compute(store.tagged_questions())
        assert summary.questions == 1 and summary.concepts == 1
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with Store(db) as store:
        assert _visible(store)[0]["reflection"]["learned"] == "I checked how readers use WAL."
        assert store.count_tagged() == 1


def test_split_group_reingest_and_new_turn_preserve_membership_and_scores(tmp_path, questions):
    with Store(tmp_path / "a.db") as store:
        store.add_questions(questions)
        store.add_tags(HeuristicTagger().tag([(q.fingerprint, q.text) for q in questions]))
        original = _visible(store)[0]["id"]
        store.save_reflection(original, goal="Find the bottleneck")
        before = metrics.compute(store.tagged_questions()).to_dict()
        split = store.group_questions(
            [questions[2].fingerprint], title="Measure latency", split_from=original
        )
        assert len(store.get_journey(original)["questions"]) == 2
        assert store.get_journey(split)["questions"][0]["id"] == questions[2].fingerprint
        assert store.get_journey(original)["reflection"]["goal"] == "Find the bottleneck"
        assert metrics.compute(store.tagged_questions()).to_dict() == before
        assert store.add_questions(questions) == 0
        next_question = _question("Which measurement would falsify this hypothesis?", 4)
        store.add_questions([next_question])
        assert next_question.fingerprint in {
            q["id"] for q in store.get_journey(original)["questions"]
        }
        assert len(store.get_journey(split)["questions"]) == 1
        assert store.count_tagged() == 3


def test_manual_merge_keeps_original_reflection_even_when_empty(tmp_path, questions):
    with Store(tmp_path / "a.db") as store:
        store.add_questions(questions)
        original = _visible(store)[0]["id"]
        store.save_reflection(original, learned="The API, not the cache, needs measurement.")
        target = store.group_questions([q.fingerprint for q in questions], title="Measure first")
        groups = {g["id"]: g for g in _visible(store)}
        assert len(groups[original]["questions"]) == 0
        assert groups[original]["reflection"]["learned"]
        assert len(groups[target]["questions"]) == 3
        store.save_reflection(target, goal="Check the API")
        extra = _question("How should we monitor tail latency?", session="other", turn=1)
        store.add_questions([extra])
        assert store.group_questions([extra.fingerprint], into=target) == target
        assert len(store.get_journey(target)["questions"]) == 4
        assert store.get_journey(target)["reflection"]["goal"] == "Check the API"


@pytest.mark.parametrize(
    "operation", ["unknown-question", "unknown-target", "all", "outside", "title"]
)
def test_invalid_group_changes_are_atomic(tmp_path, questions, operation):
    with Store(tmp_path / "a.db") as store:
        store.add_questions(questions)
        original = _visible(store)[0]["id"]
        before = store.all_journeys()
        with pytest.raises(ValueError):
            if operation == "unknown-question":
                store.group_questions([questions[0].fingerprint, "missing"], title="Bad group")
            elif operation == "unknown-target":
                store.group_questions([questions[0].fingerprint], into="missing")
            elif operation == "all":
                store.group_questions(
                    [q.fingerprint for q in questions], title="All", split_from=original
                )
            elif operation == "outside":
                store.group_questions(
                    [questions[0].fingerprint], title="Wrong source", split_from="missing"
                )
            else:
                store.group_questions([questions[0].fingerprint], title="   ")
        assert store.all_journeys() == before
        assert store.count_questions() == 3


def test_failed_first_group_does_not_leave_partial_assignments(tmp_path, questions):
    with Store(tmp_path / "a.db") as store:
        store.add_questions(questions)
        with pytest.raises(ValueError, match="unknown question"):
            store.group_questions(["missing"], title="Invalid")
        assert store.conn.execute("SELECT count(*) FROM journeys").fetchone()[0] == 0
        assert store.conn.execute("SELECT count(*) FROM journey_questions").fetchone()[0] == 0


def test_reflection_partial_updates_and_explicit_clear_survive_reopening(tmp_path, questions):
    db = tmp_path / "a.db"
    with Store(db) as store:
        store.add_questions(questions)
        jid = _visible(store)[0]["id"]
        store.save_reflection(
            jid,
            goal="캐시가 필요한가?",
            learned="병목은 아직 미확인",
            open_question="어디서 측정할까?",
        )
        store.save_reflection(jid, learned="외부 API 대기를 확인했다.")
        store.save_reflection(jid, open_question="")
    with Store(db) as store:
        reflection = store.get_journey(jid)["reflection"]
        assert reflection["goal"] == "캐시가 필요한가?"
        assert reflection["learned"] == "외부 API 대기를 확인했다."
        assert reflection["open_question"] == ""
        assert store.count_questions() == 3 and store.count_tagged() == 0


def test_concurrent_partial_reflections_preserve_both_fields(tmp_path, questions):
    db = tmp_path / "a.db"
    with Store(db) as store:
        store.add_questions(questions)
        jid = _visible(store)[0]["id"]

    def save(key, value):
        with Store(db) as store:
            store.save_reflection(jid, **{key: value})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, "goal", "Check the bottleneck")
        second = pool.submit(save, "open_question", "What should I measure?")
        first.result()
        second.result()
    with Store(db) as store:
        reflection = store.get_journey(jid)["reflection"]
        assert reflection["goal"] == "Check the bottleneck"
        assert reflection["open_question"] == "What should I measure?"


def test_date_filter_uses_same_calendar_as_grass(tmp_path, questions):
    with Store(tmp_path / "a.db") as store:
        store.add_questions(questions)
        kst = ZoneInfo("Asia/Seoul")
        (group,) = journeys.select(store.all_journeys(), day=date(2026, 9, 2), tz=kst)
        assert [q["id"] for q in group["questions"]] == [q.fingerprint for q in questions[1:]]
        assert group["total_questions"] == 3
        assert all(q["asked_at"].startswith("2026-09-02T00:30:00") for q in group["questions"])
        assert journeys.select(store.all_journeys(), day=date(2026, 9, 2), tz=timezone.utc) == []


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = set()
        self.links = []
        self.cells = {}
        self.current_day = None
        self.question_days = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if "id" in attrs:
            assert attrs["id"] not in self.ids
            self.ids.add(attrs["id"])
            if attrs["id"].startswith("day-"):
                self.current_day = attrs["id"]
            if attrs["id"].startswith("question-"):
                self.question_days[attrs["id"]] = self.current_day
        if "href" in attrs:
            self.links.append(attrs["href"])
        if "data-date" in attrs:
            self.cells[attrs["data-date"]] = attrs["data-level"]


def test_private_html_links_untagged_days_and_escapes_all_user_content(tmp_path):
    text = 'Why does <script>alert("question")</script> render?'
    with Store(tmp_path / "a.db") as store:
        question = _question(text, source='<img src="https://example.com/track">')
        store.add_questions([question])
        jid = store.group_questions([question.fingerprint], title="<script>title()</script>")
        store.save_reflection(jid, learned='<iframe src="https://example.com">note</iframe>')
        html = journeys.to_html(
            store.all_journeys(), {}, end=date(2026, 9, 2), weeks=2,
            tz=ZoneInfo("Asia/Seoul"), timezone_label="Asia/Seoul",
        )
        parsed = _HTML()
        parsed.feed(html)
        assert not {"script", "iframe", "img"} & set(parsed.tags)
        assert "&lt;script&gt;" in html and "&lt;iframe" in html
        assert "#day-2026-09-02" in parsed.links
        assert all(link.startswith("#") and link[1:] in parsed.ids for link in parsed.links)
        assert parsed.cells["2026-09-02"] == "0"  # readable before any tagging
        assert "Asia/Seoul" in html and "Self-reported." in html
        assert "Content-Security-Policy" in html


def test_html_window_and_multiday_journey_keep_questions_on_the_right_day(tmp_path, questions):
    with Store(tmp_path / "a.db") as store:
        store.add_questions(
            [*questions, _question("A later question", 4, at="2026-10-01T12:00:00")]
        )
        html = journeys.to_html(
            store.all_journeys(), {}, end=date(2026, 9, 2), weeks=1, tz=ZoneInfo("Asia/Seoul")
        )
        parsed = _HTML()
        parsed.feed(html)
        assert "day-2026-09-01" in parsed.ids and "day-2026-09-02" in parsed.ids
        assert "A later question" not in html
        assert parsed.question_days == {
            f"question-{questions[0].fingerprint}": "day-2026-09-01",
            f"question-{questions[1].fingerprint}": "day-2026-09-02",
            f"question-{questions[2].fingerprint}": "day-2026-09-02",
        }


def test_private_export_does_not_use_out_of_window_question_as_title(tmp_path):
    with Store(tmp_path / "a.db") as store:
        store.add_questions([
            _question("This earlier private topic is outside the window", at="2026-08-01T12:00:00"),
            _question("Question inside the selected week", 1, at="2026-09-01T12:00:00"),
        ])
        html = journeys.to_html(
            store.all_journeys(), {}, end=date(2026, 9, 2), weeks=1, tz=timezone.utc
        )
        assert "earlier private topic" not in html
        assert "Question inside the selected week" in html


def test_cli_journey_reflection_and_private_grass_roundtrip(tmp_path, capsys, questions):
    db = tmp_path / "a.db"
    prefix = ["--db", str(db)]
    with Store(db) as store:
        store.add_questions(questions)
    assert main([*prefix, "journey", "list", "--format", "json"]) == 0
    (group,) = json.loads(capsys.readouterr().out)
    jid = group["id"]
    # Merely opening a reflection does not create a self-report.
    assert main([*prefix, "review", jid, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["reflection"] is None
    assert main([
        *prefix, "review", jid, "--goal", "Find evidence", "--open-question", "Measure?"
    ]) == 0
    assert "Find evidence" in capsys.readouterr().out
    assert main([*prefix, "review", jid, "--learned", "Measured API latency"]) == 0
    capsys.readouterr()
    assert main([*prefix, "journey", "show", jid, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["reflection"]["goal"] == "Find evidence"
    assert main([
        *prefix, "journey", "list", "--date", "2026-09-02", "--timezone", "Asia/Seoul",
        "--format", "json",
    ]) == 0
    assert len(json.loads(capsys.readouterr().out)[0]["questions"]) == 2
    output = tmp_path / "private.html"
    assert main([
        *prefix, "grass", "--format", "html", "--out", str(output), "--end", "2026-09-02",
        "--weeks", "2", "--timezone", "Asia/Seoul",
    ]) == 0
    assert "private journey snapshot" in capsys.readouterr().out
    text = output.read_text()
    assert "Measured API latency" in text and "#day-2026-09-02" in text
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
    assert main([*prefix, "grass", "--end", "2026-09-02"]) == 0
    public = capsys.readouterr().out
    assert public.startswith("<svg")
    assert "Find evidence" not in public and "What is a cache?" not in public
    assert "href=" not in public
    assert main([*prefix, "journey", "split", jid, "--title", "API investigation",
                 "--questions", questions[2].fingerprint]) == 0
    split = capsys.readouterr().out.split()[-1]
    assert main([*prefix, "journey", "group", "--into", jid,
                 "--questions", questions[2].fingerprint]) == 0
    capsys.readouterr()
    with Store(db) as store:
        assert len(store.get_journey(jid)["questions"]) == 3
        assert store.get_journey(split)["questions"] == []


def test_cli_errors_do_not_succeed_or_modify_data(tmp_path, capsys, questions):
    db = tmp_path / "a.db"
    prefix = ["--db", str(db)]
    with Store(db) as store:
        store.add_questions(questions)
    assert main([*prefix, "review", "missing", "--goal", "bad"]) == 2
    assert "unknown journey" in capsys.readouterr().err
    assert main([*prefix, "journey", "group", "--title", "Bad", "--questions", "missing"]) == 2
    assert "unknown question" in capsys.readouterr().err
    assert main([*prefix, "grass", "--format", "html", "--out", str(db)]) == 2
    assert "different from your database" in capsys.readouterr().err
    for argv in [
        ["grass", "--format", "html"],
        ["journey", "list", "--date", "not-a-date"],
        ["journey", "list", "--timezone", "not/a/timezone"],
        ["journey", "group", "--title", "hook"],  # 'hook' in another command must not hide errors
    ]:
        with pytest.raises(SystemExit) as error:
            main([*prefix, *argv])
        assert error.value.code == 2
        capsys.readouterr()
    with Store(db) as store:
        assert store.count_questions() == 3
        assert store.conn.execute("SELECT count(*) FROM journey_reflections").fetchone()[0] == 0


def test_cli_empty_journeys_and_private_export(tmp_path, capsys):
    prefix = ["--db", str(tmp_path / "empty.db")]
    assert main([*prefix, "journey", "list"]) == 0
    assert "No journeys" in capsys.readouterr().out
    output = tmp_path / "empty.html"
    assert main([*prefix, "grass", "--format", "html", "--out", str(output)]) == 0
    assert "No questions in this window" in output.read_text()


def test_private_export_failure_preserves_previous_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "private.html"
    output.write_text("Previous snapshot", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("output unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert main([
        "--db", str(tmp_path / "a.db"), "grass", "--format", "html", "--out", str(output)
    ]) == 2
    assert "output unavailable" in capsys.readouterr().err
    assert output.read_text() == "Previous snapshot"
    assert not list(tmp_path.glob(".asktrace-*"))

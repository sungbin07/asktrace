from pathlib import Path

from asktrace.sources import ChatGPTExportSource, ClaudeCodeSource, ClaudeExportSource, JsonlSource
from asktrace.sources.base import looks_like_question

FIX = Path(__file__).parent / "fixtures"


def test_claude_code_filters_tool_results_commands_and_meta():
    qs = list(ClaudeCodeSource(FIX / "claude_code_session.jsonl").questions())
    texts = [q.text for q in qs]
    assert texts == [
        "prompt caching이 뭐야? 어떤 경우에 쓰는 거야",
        "prompt caching 적용했는데 cache_read_input_tokens가 0이야 왜 안돼?",
        "sqlite WAL 모드랑 journal 모드 차이가 뭐야",
    ]
    assert all(q.source == "claude-code" and q.session_id == "s1" for q in qs)
    assert qs[0].asked_at.isoformat() == "2026-09-01T09:00:00+00:00"
    assert len({q.fingerprint for q in qs}) == 3


def test_claude_export_reads_text_and_content_blocks():
    qs = list(ClaudeExportSource(FIX / "claude_export.json").questions())
    assert [q.text for q in qs] == [
        "git rebase와 merge 차이가 뭐야?",
        "interactive rebase로 커밋 합치는 방법 알려줘",
    ]


def test_chatgpt_export_orders_by_time_and_skips_non_user():
    qs = list(ChatGPTExportSource(FIX / "chatgpt_export.json").questions())
    assert [q.text for q in qs] == [
        "docker compose에서 볼륨 마운트 어떻게 해?",
        "컨테이너가 바로 죽는데 왜 그래? exit code 137",
    ]
    assert qs[0].asked_at < qs[1].asked_at


def test_jsonl_source(tmp_path):
    f = tmp_path / "q.jsonl"
    f.write_text(
        '{"text":"what is a bloom filter","asked_at":"2026-01-01T00:00:00Z"}\n'
        '{"text":"hi","asked_at":"2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    qs = list(JsonlSource(f).questions())
    assert len(qs) == 1 and qs[0].text == "what is a bloom filter"


def test_looks_like_question():
    assert looks_like_question("why does this fail?")
    assert not looks_like_question("/clear")
    assert not looks_like_question("ok")
    assert not looks_like_question("https://example.com/x")
    assert not looks_like_question("<system-reminder>x</system-reminder>")

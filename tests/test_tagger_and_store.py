from pathlib import Path

from asktrace.sources import ClaudeCodeSource
from asktrace.store import Store
from asktrace.tagger import HeuristicTagger, get_tagger

FIX = Path(__file__).parent / "fixtures"


def test_heuristic_depth_and_domain():
    t = HeuristicTagger()
    tags = t.tag(
        [
            ("a", "prompt caching이 뭐야?"),
            ("b", "docker compose에서 볼륨 마운트 어떻게 해?"),
            ("c", "git rebase와 merge 차이가 뭐야?"),
            ("d", "cache_read_input_tokens가 0이야 왜 안돼? error"),
            ("e", "멀티 테넌트 설계에서 trade-off 뭐가 있어"),
        ]
    )
    assert [x.depth for x in tags] == [1, 2, 3, 4, 5]
    assert tags[0].domain == "ml"
    assert tags[1].domain == "devops"
    assert tags[2].domain == "git"
    assert "prompt" in tags[0].concepts
    assert all(x.tagger == "heuristic" for x in tags)


def test_store_roundtrip_is_idempotent(tmp_path):
    with Store(tmp_path / "t.db") as store:
        qs = list(ClaudeCodeSource(FIX / "claude_code_session.jsonl").questions())
        assert store.add_questions(qs) == 3
        assert store.add_questions(qs) == 0
        rows = store.untagged()
        assert len(rows) == 3
        store.add_tags(HeuristicTagger().tag([(r["id"], r["text"]) for r in rows]))
        assert store.count_tagged() == 3
        assert store.untagged() == []
        tagged = store.tagged_questions()
        assert tagged[0]["asked_at"].year == 2026
        assert isinstance(tagged[0]["concepts"], list)


def test_heuristic_strips_korean_particles():
    (tag,) = HeuristicTagger().tag([("a", "설계 관점에서 모드랑 스키마의 차이")])
    assert "관점" in tag.concepts and "모드" in tag.concepts and "스키마" in tag.concepts
    assert not any(c.endswith("에서") or c.endswith("랑") for c in tag.concepts)


def test_get_tagger_unknown():
    try:
        get_tagger("nope")
    except ValueError as e:
        assert "unknown tagger" in str(e)
    else:
        raise AssertionError("expected ValueError")

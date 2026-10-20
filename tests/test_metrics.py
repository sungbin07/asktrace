from datetime import datetime, timezone

from asktrace import metrics, report


def _row(day: int, concepts: list[str], depth: int, domain: str = "ml"):
    return {
        "asked_at": datetime(2026, 9, day, tzinfo=timezone.utc),
        "concepts": concepts,
        "depth": depth,
        "domain": domain,
        "text": "",
    }


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def test_empty():
    s = metrics.compute([], now=NOW)
    assert s.questions == 0 and s.accumulated_knowledge == 0.0
    assert "No tagged questions" in report.to_markdown(s)


def test_deepening_and_shallow_repeats():
    rows = [
        _row(1, ["prompt caching"], 1),
        _row(2, ["prompt caching"], 4),  # went deeper
        _row(3, ["git rebase"], 2, "git"),
        _row(4, ["git rebase"], 2, "git"),  # shallow re-ask
        _row(5, ["git rebase"], 1, "git"),  # shallow re-ask
        _row(8, ["sqlite wal"], 3, "data"),
    ]
    s = metrics.compute(rows, now=NOW)
    assert s.questions == 6 and s.concepts == 3
    assert s.domains == {"ml": 1, "git": 1, "data": 1}
    by = {c.concept: c for c in s.top_concepts}
    assert by["prompt caching"].max_depth == 4 and by["prompt caching"].depth_trend == 3
    assert by["git rebase"].re_asks_at_or_below_depth == 2
    assert s.re_ask_rate == round(2 / 6, 3)
    assert s.deepening_rate == 0.5  # 2 revisited, 1 deepened
    assert [c.concept for c in s.shallow_repeats] == ["git rebase"]
    assert by["prompt caching"].mastery > by["git rebase"].mastery
    assert len(s.weeks) == 2
    assert s.weeks[0].new_concepts == 2 and s.weeks[1].new_concepts == 1
    md = report.to_markdown(s)
    assert "Revisited at the same or lower question depth" in md and "prompt caching" in md
    assert "not demonstrated understanding" in md
    assert "Exploration score (heuristic)" in md
    assert '"accumulated_knowledge"' in report.to_json(s)


def test_same_day_repeats_are_not_forgetting():
    rows = [_row(3, ["plan review"], 2), _row(3, ["plan review"], 2), _row(3, ["plan review"], 1)]
    s = metrics.compute(rows, now=NOW)
    (c,) = s.top_concepts
    assert c.asks == 3 and c.re_asks_at_or_below_depth == 0
    assert s.re_ask_rate == 0.0 and s.shallow_repeats == []


def test_single_deep_question_is_not_full_mastery():
    assert metrics.mastery_score(5, 1, 0) == 0.5
    assert metrics.mastery_score(1, 1, 0) == 0.1
    # Returning on more days earns more, approaching but never reaching 1.0.
    scores = [metrics.mastery_score(5, d, 0) for d in (1, 2, 3, 5)]
    assert scores == sorted(scores) and scores[1] == 0.75 and scores[2] == 0.875
    assert scores[-1] < 1.0
    # Sustained shallower engagement can match a one-off deep question.
    assert metrics.mastery_score(3, 3, 0) > metrics.mastery_score(5, 1, 0)


def test_mastery_decays_but_keeps_floor():
    fresh = metrics.mastery_score(5, 3, 0)
    old = metrics.mastery_score(5, 3, 365)
    assert old < fresh
    assert old >= round(0.3 * fresh, 4) - 1e-4

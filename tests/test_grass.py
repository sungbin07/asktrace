import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from asktrace import grass, metrics
from asktrace.cli import main
from asktrace.metrics import DayLearning
from asktrace.store import Store

FIX = Path(__file__).parent / "fixtures"


def _row(day: int, concepts: list[str], depth: int, hour: int = 12, text: str = ""):
    return {
        "asked_at": datetime(2026, 9, day, hour, tzinfo=timezone.utc),
        "concepts": concepts,
        "depth": depth,
        "domain": "ml",
        "text": text,
    }


ROWS = [
    _row(1, ["prompt caching", "git rebase"], 1),  # 2 new
    _row(1, ["prompt caching"], 1, hour=13),  # same depth: nothing
    _row(1, ["prompt caching"], 1, hour=14),  # split question: nothing
    _row(2, ["prompt caching"], 3),  # deeper: 1
    _row(2, ["prompt caching"], 2, hour=13),  # shallower: nothing
    _row(3, ["prompt caching"], 3),  # same as max: nothing
    _row(3, ["sqlite wal"], 4),  # new: 1
    _row(5, ["prompt caching"], 5),  # deeper: 1
]


def test_learning_events_per_day():
    assert metrics.learning_events_by_day(ROWS) == {
        date(2026, 9, 1): DayLearning(new_concepts=2, deeper=0),
        date(2026, 9, 2): DayLearning(new_concepts=0, deeper=1),
        date(2026, 9, 3): DayLearning(new_concepts=1, deeper=0),
        date(2026, 9, 5): DayLearning(new_concepts=0, deeper=1),
    }


def test_splitting_a_question_adds_nothing():
    one = [_row(1, ["docker volume"], 2)]
    five = [_row(1, ["docker volume"], 2, hour=h) for h in range(8, 13)]
    assert metrics.learning_events_by_day(one) == metrics.learning_events_by_day(five)


def test_each_concept_is_worth_at_most_five_events():
    rows = [_row(d, ["k8s"], d) for d in range(1, 6)]  # depth 1..5 on days 1..5
    rows += [_row(d, ["k8s"], 5) for d in range(6, 30)]
    days = metrics.learning_events_by_day(rows)
    assert sum(v.events for v in days.values()) == 5


def test_days_follow_the_given_time_zone():
    late = [_row(1, ["wal"], 1, hour=23)]
    kst = timezone(timedelta(hours=9))
    assert list(metrics.learning_events_by_day(late)) == [date(2026, 9, 1)]
    assert list(metrics.learning_events_by_day(late, tz=kst)) == [date(2026, 9, 2)]


def test_levels_use_fixed_thresholds():
    assert [grass.level(n) for n in (0, 1, 4, 5, 9, 10, 19, 20, 500)] == [
        0,
        1,
        1,
        2,
        2,
        3,
        3,
        4,
        4,
    ]


def test_window_starts_on_a_sunday():
    start, end = grass.window(date(2026, 9, 30), 52)  # a Wednesday
    assert start.weekday() == 6 and end == date(2026, 9, 30)
    assert (end - start).days == 51 * 7 + 3


def test_svg_is_deterministic_and_matches_per_day_values():
    shuffled = ROWS[:]
    random.Random(7).shuffle(shuffled)
    end = date(2026, 9, 30)
    svg = grass.render(metrics.learning_events_by_day(ROWS), end=end, weeks=8)
    assert svg == grass.render(metrics.learning_events_by_day(shuffled), end=end, weeks=8)
    assert svg.startswith("<svg ") and svg.endswith("</svg>\n")

    cells = dict(re.findall(r'data-date="([\d-]+)" data-level="(\d)"', svg))
    start, _ = grass.window(end, 8)
    assert len(cells) == (end - start).days + 1  # no future days in the last column
    assert cells["2026-09-01"] == "1" and cells["2026-09-04"] == "0"
    assert "2026-09-01: 2 learning events (2 new, 0 deeper)" in svg
    summary = (
        "5 learning events (3 new concepts, 2 deeper asks) on 4 days, 2026-08-09 to 2026-09-30"
    )
    assert f"<title>asktrace knowledge grass: {summary}</title>" in svg
    assert f'aria-label="asktrace knowledge grass: {summary}"' in svg


def test_svg_shades_by_level():
    end = date(2026, 9, 30)
    days = {end - timedelta(days=i): DayLearning(n, 0) for i, n in enumerate((1, 5, 10, 20))}
    svg = grass.render(days, end=end, weeks=4, theme="dark")
    cells = dict(re.findall(r'data-date="([\d-]+)" data-level="(\d)"', svg))
    assert [cells[(end - timedelta(days=i)).isoformat()] for i in range(4)] == ["1", "2", "3", "4"]
    assert grass.THEMES["dark"]["bg"] in svg and grass.THEMES["light"]["bg"] not in svg
    with pytest.raises(ValueError):
        grass.render(days, end=end, theme="neon")


def test_empty_history_renders():
    svg = grass.render({}, end=date(2026, 9, 30))
    assert "0 learning events in the last 52 weeks" in svg
    assert 'data-level="0"' in svg and 'data-level="1"' not in svg


def test_svg_has_no_concepts_or_question_text():
    secret = [
        _row(
            1, ["project-zeta billing", "acme internal api"], 2, text="why does zeta-billing 500?"
        ),
        _row(2, ["project-zeta billing"], 4, text="acme internal api times out on retry"),
    ]
    secret[0]["domain"] = "acme-corp"
    svg = grass.render(metrics.learning_events_by_day(secret), end=date(2026, 9, 30))
    for s in ("zeta", "acme", "billing", "internal", "retry", "times out"):
        assert s not in svg.lower()


def test_cli_grass_writes_svg_without_concepts(tmp_path, capsys):
    db, out = tmp_path / "a.db", tmp_path / "grass.svg"
    assert (
        main(["--db", str(db), "ingest", "claude-code", str(FIX / "claude_code_session.jsonl")])
        == 0
    )
    assert main(["--db", str(db), "grass", "--out", str(out)]) == 0
    assert "3 untagged questions are not drawn" in capsys.readouterr().err
    assert main(["--db", str(db), "tag"]) == 0
    capsys.readouterr()
    assert main(["--db", str(db), "grass", "--out", str(out), "--weeks", "8"]) == 0
    assert capsys.readouterr().out.startswith(f"wrote {out}: ")
    svg = out.read_text(encoding="utf-8")
    with Store(db) as store:
        rows = store.tagged_questions()
    for r in rows:
        assert r["text"] not in svg
        for c in r["concepts"]:
            assert c not in svg
    assert main(["--db", str(db), "grass", "--theme", "dark"]) == 0
    assert capsys.readouterr().out.startswith("<svg ")

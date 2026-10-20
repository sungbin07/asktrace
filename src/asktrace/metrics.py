"""Knowledge-accumulation metrics computed from tagged questions.

Everything here is a transparent heuristic. The point is to make trends visible, not to
claim a ground-truth measure of what someone knows. See docs/DESIGN.md for the reasoning.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class ConceptStats:
    concept: str
    domain: str
    first_asked: datetime
    last_asked: datetime
    asks: int
    max_depth: int
    depth_trend: int  # last depth minus first depth
    re_asks_at_or_below_depth: int  # later questions that did not go deeper: forgetting signal
    distinct_days: int
    mastery: float  # 0..1 heuristic, see `mastery_score`


@dataclass
class WeekStats:
    week_start: str  # ISO date (Monday)
    questions: int
    new_concepts: int
    revisited_concepts: int
    mean_depth: float


@dataclass
class Summary:
    questions: int
    concepts: int
    domains: dict[str, int]
    accumulated_knowledge: float  # sum of concept mastery
    acquisition_rate_per_week: float  # new concepts per active week
    re_ask_rate: float  # share of asks that revisit a concept without going deeper
    deepening_rate: float  # share of revisited concepts whose depth went up
    mean_depth: float
    period: tuple[str, str]
    weeks: list[WeekStats] = field(default_factory=list)
    top_concepts: list[ConceptStats] = field(default_factory=list)
    shallow_repeats: list[ConceptStats] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for cs in d["top_concepts"] + d["shallow_repeats"]:
            cs["first_asked"] = cs["first_asked"].isoformat()
            cs["last_asked"] = cs["last_asked"].isoformat()
        return d


def mastery_score(max_depth: int, distinct_days: int, days_since_last: float) -> float:
    """Depth you reached, spread over time, decayed by how long ago you last touched it.

    - depth: 1 -> 0.2 ... 5 -> 1.0
    - spacing: revisiting on 3+ distinct days saturates at +50%
    - decay: half-life 90 days, floor at 0.3 so old knowledge is discounted, not erased
    """
    depth_part = max_depth / 5.0
    spacing_part = 1.0 + 0.5 * min(distinct_days - 1, 2) / 2
    decay = 0.3 + 0.7 * math.pow(0.5, days_since_last / 90.0)
    return round(min(1.0, depth_part * spacing_part) * decay, 4)


def _week_start(dt: datetime) -> str:
    monday = (dt - timedelta(days=dt.weekday())).date()
    return monday.isoformat()


def compute(rows: list[dict], now: datetime | None = None, top_n: int = 20) -> Summary:
    """`rows` come from Store.tagged_questions(): dicts with asked_at, concepts, domain, depth."""
    now = now or datetime.now(timezone.utc)
    if not rows:
        return Summary(0, 0, {}, 0.0, 0.0, 0.0, 0.0, 0.0, ("", ""))

    per_concept: dict[str, list[dict]] = defaultdict(list)
    domains: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        for c in r["concepts"]:
            per_concept[c].append(r)
            domains[r["domain"]].add(c)

    concept_stats: list[ConceptStats] = []
    total_asks = 0
    shallow_repeats_total = 0
    revisited = deepened = 0
    for concept, asks in per_concept.items():
        asks.sort(key=lambda r: r["asked_at"])
        depths = [a["depth"] for a in asks]
        running_max = depths[0]
        shallow = 0
        for d in depths[1:]:
            if d <= running_max:
                shallow += 1
            running_max = max(running_max, d)
        days = {a["asked_at"].date() for a in asks}
        domain = max({a["domain"] for a in asks}, key=lambda d: sum(a["domain"] == d for a in asks))
        last = asks[-1]["asked_at"]
        cs = ConceptStats(
            concept=concept,
            domain=domain,
            first_asked=asks[0]["asked_at"],
            last_asked=last,
            asks=len(asks),
            max_depth=max(depths),
            depth_trend=depths[-1] - depths[0],
            re_asks_at_or_below_depth=shallow,
            distinct_days=len(days),
            mastery=mastery_score(max(depths), len(days), (now - last).total_seconds() / 86400),
        )
        concept_stats.append(cs)
        total_asks += len(asks)
        shallow_repeats_total += shallow
        if len(asks) > 1:
            revisited += 1
            if max(depths) > depths[0]:
                deepened += 1

    # Weekly timeline
    first_seen: dict[str, str] = {}
    weekly: dict[str, dict] = defaultdict(
        lambda: {"q": 0, "new": set(), "revisit": set(), "depth": []}
    )
    for r in rows:  # rows are oldest-first
        wk = _week_start(r["asked_at"])
        weekly[wk]["q"] += 1
        weekly[wk]["depth"].append(r["depth"])
        for c in r["concepts"]:
            if c not in first_seen:
                first_seen[c] = wk
                weekly[wk]["new"].add(c)
            elif first_seen[c] != wk:
                weekly[wk]["revisit"].add(c)
    weeks = [
        WeekStats(
            week_start=wk,
            questions=v["q"],
            new_concepts=len(v["new"]),
            revisited_concepts=len(v["revisit"]),
            mean_depth=round(sum(v["depth"]) / len(v["depth"]), 2),
        )
        for wk, v in sorted(weekly.items())
    ]

    concept_stats.sort(key=lambda c: (-c.mastery, -c.asks, c.concept))
    shallow = sorted(
        (c for c in concept_stats if c.re_asks_at_or_below_depth >= 2),
        key=lambda c: (-c.re_asks_at_or_below_depth, c.concept),
    )
    return Summary(
        questions=len(rows),
        concepts=len(per_concept),
        domains={d: len(cs) for d, cs in sorted(domains.items(), key=lambda kv: -len(kv[1]))},
        accumulated_knowledge=round(sum(c.mastery for c in concept_stats), 2),
        acquisition_rate_per_week=round(len(per_concept) / max(1, len(weeks)), 2),
        re_ask_rate=round(shallow_repeats_total / max(1, total_asks), 3),
        deepening_rate=round(deepened / revisited, 3) if revisited else 0.0,
        mean_depth=round(sum(r["depth"] for r in rows) / len(rows), 2),
        period=(rows[0]["asked_at"].date().isoformat(), rows[-1]["asked_at"].date().isoformat()),
        weeks=weeks,
        top_concepts=concept_stats[:top_n],
        shallow_repeats=shallow[:top_n],
    )

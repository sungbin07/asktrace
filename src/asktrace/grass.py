"""Knowledge grass: a GitHub-contribution-style SVG of learning events per day.

A day's shade comes from `metrics.learning_events_by_day`, not from how many questions were
asked. The SVG holds only dates and counts: no concept names, domains or question text, so
it is safe to commit to a public profile README. Output depends only on the inputs (no
generation timestamp), so re-running on unchanged data gives an identical file.
"""

from __future__ import annotations

from datetime import date, timedelta

from .metrics import DayLearning

# Minimum learning events for levels 1-4 (0 events is level 0). Fixed rather than relative
# to your busiest day, so a level means the same thing every month. With a model tagger a
# question usually carries two or three concepts.
LEVEL_THRESHOLDS = (1, 5, 10, 20)

THEMES: dict[str, dict] = {
    "light": {
        "bg": "#ffffff",
        "text": "#1f2328",
        "muted": "#59636e",
        "levels": ("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"),
    },
    "dark": {
        "bg": "#0d1117",
        "text": "#f0f6fc",
        "muted": "#9198a1",
        "levels": ("#151b23", "#033a16", "#196c2e", "#2ea043", "#56d364"),
    },
}

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}  # rows are Sunday (0) to Saturday (6)

CELL, GAP = 10, 3
PITCH = CELL + GAP
PAD, LABEL_W = 10, 28
GRID_X, GRID_Y = PAD + LABEL_W, 52
MIN_WIDTH = 480
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def level(events: int) -> int:
    return sum(events >= t for t in LEVEL_THRESHOLDS)


def window(end: date, weeks: int) -> tuple[date, date]:
    """First Sunday and last day of a `weeks`-column grid whose last column holds `end`."""
    if weeks < 1:
        raise ValueError("weeks must be at least 1")
    this_sunday = end - timedelta(days=(end.weekday() + 1) % 7)
    return this_sunday - timedelta(weeks=weeks - 1), end


def totals(days: dict[date, DayLearning], start: date, end: date) -> dict[str, int]:
    inside = [v for d, v in days.items() if start <= d <= end]
    return {
        "events": sum(v.events for v in inside),
        "new_concepts": sum(v.new_concepts for v in inside),
        "deeper": sum(v.deeper for v in inside),
        "active_days": sum(v.events > 0 for v in inside),
    }


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _month_labels(start: date, weeks: int) -> list[tuple[int, str]]:
    labels = []
    for col in range(weeks):
        top = start + timedelta(weeks=col)
        if col == 0 or top.month != (top - timedelta(weeks=1)).month:
            labels.append((col, MONTHS[top.month - 1]))
    if len(labels) > 1 and labels[1][0] - labels[0][0] < 3:
        labels.pop(0)  # a sliver of the first month would overlap the next label
    return labels


def render(days: dict[date, DayLearning], end: date, weeks: int = 52, theme: str = "light") -> str:
    if theme not in THEMES:
        raise ValueError(f"unknown theme: {theme!r} (expected one of {sorted(THEMES)})")
    t = THEMES[theme]
    start, end = window(end, weeks)
    tot = totals(days, start, end)
    width = max(GRID_X + weeks * PITCH - GAP + PAD, MIN_WIDTH)
    grid_bottom = GRID_Y + 7 * PITCH - GAP
    height = grid_bottom + 30
    events = _plural(tot["events"], "learning event")
    summary = (
        f"{events} ({_plural(tot['new_concepts'], 'new concept')},"
        f" {_plural(tot['deeper'], 'deeper ask')}) on {_plural(tot['active_days'], 'day')},"
        f" {start.isoformat()} to {end.isoformat()}"
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
        f' viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="asktrace knowledge grass: {summary}">',
        f"<title>asktrace knowledge grass: {summary}</title>",
        "<desc>One square per day. The shade counts learning events: concepts asked about"
        " for the first time plus questions that went deeper than before on a concept."
        " Repeats at the same depth add nothing.</desc>",
        f'<rect width="{width}" height="{height}" rx="6" fill="{t["bg"]}"/>',
        f'<g font-family="{FONT}" font-size="10" fill="{t["muted"]}">',
        f'<text x="{PAD}" y="24" font-size="14" fill="{t["text"]}">'
        f"{events} in the last {_plural(weeks, 'week')}</text>",
    ]
    for col, name in _month_labels(start, weeks):
        out.append(f'<text x="{GRID_X + col * PITCH}" y="{GRID_Y - 6}">{name}</text>')
    for row, name in WEEKDAY_LABELS.items():
        out.append(f'<text x="{PAD}" y="{GRID_Y + row * PITCH + 9}">{name}</text>')

    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        v = days.get(d, DayLearning())
        lv = level(v.events)
        tip = (
            f"{_plural(v.events, 'learning event')} ({v.new_concepts} new, {v.deeper} deeper)"
            if v.events
            else "no learning events"
        )
        out.append(
            f'<rect x="{GRID_X + (i // 7) * PITCH}" y="{GRID_Y + (i % 7) * PITCH}"'
            f' width="{CELL}" height="{CELL}" rx="2" fill="{t["levels"][lv]}"'
            f' data-date="{d.isoformat()}" data-level="{lv}"><title>{d.isoformat()}: {tip}</title>'
            "</rect>"
        )

    foot = grid_bottom + 20
    out.append(
        f'<text x="{PAD}" y="{foot}">Counts new concepts and deeper asks, not questions.</text>'
    )
    sq_x = width - PAD - 28 - (5 * PITCH - GAP)
    out.append(f'<text x="{sq_x - 4}" y="{foot}" text-anchor="end">Less</text>')
    for lv, color in enumerate(t["levels"]):
        out.append(
            f'<rect x="{sq_x + lv * PITCH}" y="{foot - 9}" width="{CELL}" height="{CELL}"'
            f' rx="2" fill="{color}"/>'
        )
    out.append(f'<text x="{sq_x + 5 * PITCH + 1}" y="{foot}">More</text>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"

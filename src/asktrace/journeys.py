"""Question journeys and user reflections, without inferring learning or causality."""

from __future__ import annotations

import hashlib
import json
from datetime import date, tzinfo
from html import escape

from . import grass
from .metrics import DayLearning

REFLECTION_FIELDS = (
    ("goal", "What I wanted to understand"),
    ("learned", "What I checked or understood"),
    ("open_question", "What I still want to ask"),
)


def session_journey_id(source: str, session_id: str, question_id: str) -> str:
    # Without a session ID, unrelated generic JSONL rows must not be lumped together.
    key = [source, "session", session_id] if session_id else [source, "question", question_id]
    digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
    return f"s_{digest}"


def select(
    groups: list[dict], *, day: date | None = None, tz: tzinfo | None = None
) -> list[dict]:
    """Create display records in the same calendar used by the grass."""
    selected = []
    for group in groups:
        questions = []
        for question in group["questions"]:
            local = question["asked_at"].astimezone(tz)
            if day is None or local.date() == day:
                questions.append({**question, "asked_at": local.isoformat()})
        if day is not None and not questions:
            continue
        if not questions and not group["reflection"]:
            continue
        title = group["title"]
        if not title:
            title = (
                " ".join(questions[0]["text"].split())[:100]
                if questions
                else "Journey with no remaining questions"
            )
        selected.append(
            {
                **group,
                "title": title,
                "total_questions": len(group["questions"]),
                "questions": questions,
            }
        )
    return sorted(
        selected,
        key=lambda g: (
            g["questions"][0]["asked_at"] if g["questions"] else g["created_at"],
            g["id"],
        ),
    )


def to_markdown(groups: list[dict], *, summary: bool = False) -> str:
    if not groups:
        return "No journeys in this selection. Ingest questions first or choose another date.\n"
    lines = [
        "# asktrace question journeys",
        "",
        "Session groups are a starting point; split or group questions to match your inquiry.",
        "Reflections are your own account, not an automatic assessment of understanding.",
        "",
    ]
    for group in groups:
        lines += [
            f"## {group['title']}",
            "",
            f"Journey: `{group['id']}` · "
            f"{'session grouping' if group['kind'] == 'session' else 'your grouping'} · "
            f"{len(group['questions'])} of {group['total_questions']} questions",
            "",
        ]
        if summary:
            if group["questions"]:
                lines += [
                    f"From {group['questions'][0]['asked_at']}"
                    f" to {group['questions'][-1]['asked_at']}.",
                    "",
                ]
            lines += [f"Read with `asktrace journey show {group['id']}`.", ""]
            continue
        for question in group["questions"]:
            lines += [
                f"- `{question['id']}` · {question['asked_at']} · {question['source']}",
                "",
                *[f"  > {line}" for line in question["text"].splitlines()],
                "",
            ]
        reflection = group["reflection"] or {}
        lines.append("### Your reflection")
        for key, label in REFLECTION_FIELDS:
            lines += ["", f"**{label}**", "", reflection.get(key) or "_Not recorded._"]
        lines += ["", f"Edit with `asktrace review {group['id']} --goal ...`.", ""]
    return "\n".join(lines)


def to_json(groups: list[dict]) -> str:
    return json.dumps(groups, ensure_ascii=False, indent=2)


def to_html(
    groups: list[dict],
    days: dict[date, DayLearning],
    *,
    end: date,
    weeks: int = 52,
    theme: str = "light",
    tz: tzinfo | None = None,
    timezone_label: str = "system local time",
) -> str:
    """Self-contained, private, read-only snapshot. All transcript text is escaped."""
    start, _ = grass.window(end, weeks)
    # Only retain days in the exported window. Every question is visible even if
    # untagged, repeated, or too shallow to change the grass's activity score.
    dates = {
        question["asked_at"].astimezone(tz).date()
        for group in groups
        for question in group["questions"]
        if start <= question["asked_at"].astimezone(tz).date() <= end
    }
    svg = grass.render(days, end=end, weeks=weeks, theme=theme, linked_days=dates)
    palette = grass.THEMES[theme]
    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\';'
        " style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>asktrace · My question journeys</title>",
        "<style>",
        f":root {{ color-scheme: {theme}; --bg: {palette['bg']};"
        f" --fg: {palette['text']}; --muted: {palette['muted']}; }}",
        "body { background:var(--bg); color:var(--fg); font:16px/1.65 system-ui,sans-serif;"
        " margin:0 auto; max-width:1000px; padding:24px; }",
        "a { color:var(--fg); text-underline-offset:3px; }",
        "a:focus-visible { outline:3px solid #0969da; outline-offset:3px; }",
        ".calendar { overflow-x:auto; padding:8px 0; }",
        ".calendar a:hover rect, .calendar a:focus rect { stroke:#0969da; stroke-width:2; }",
        "section { margin-top:36px; scroll-margin-top:16px; }",
        "article { border:1px solid var(--muted); border-radius:8px; padding:20px;"
        " margin:20px 0; overflow-wrap:anywhere; }",
        ".meta { color:var(--muted); font-size:14px; }",
        ".text { white-space:pre-wrap; overflow-wrap:anywhere; }",
        "li { margin-bottom:20px; } code { overflow-wrap:anywhere; }",
        "h1,h2,h3 { line-height:1.3; } dt { font-weight:600; margin-top:16px; }",
        "dd { margin:4px 0 0; }",
        "</style></head><body>",
        '<header id="top"><h1>My question journeys</h1>',
        "<p>Revisit what you asked, what you checked, and what remains open.</p>",
        "<p><strong>Private snapshot:</strong> this file includes your questions and reflections."
        " Use the default SVG for sharing dates and counts only.</p>",
        f'<p class="meta">{start.isoformat()} – {end.isoformat()} · '
        f"{escape(timezone_label)}. Regenerate after adding questions or editing reflections.</p>",
        "</header><main>",
        '<nav aria-label="Choose a day"><h2>Explore a day</h2>',
        "<p>Choose an outlined or coloured linked square. All recorded questions are included,"
        " even when they add no colour. Colours reflect first-seen concepts and deeper asks;"
        " they do not confirm learning.</p>",
        f'<div class="calendar">{svg}</div></nav>',
    ]
    if not dates:
        out.append(
            "<p>No questions in this window. Ingest a transcript or choose another end date.</p>"
        )
    for day in sorted(dates, reverse=True):
        out += [
            f'<section id="day-{day.isoformat()}" aria-labelledby="heading-{day.isoformat()}">',
            f'<h2 id="heading-{day.isoformat()}">{day.isoformat()}</h2>',
        ]
        for group in select(groups, day=day, tz=tz):
            out += [
                f"<article><h3>{escape(group['title'])}</h3>",
                f'<p class="meta"><code>{escape(group["id"])}</code> · '
                f'{"Session grouping" if group["kind"] == "session" else "Your grouping"} · '
                f'{len(group["questions"])} of {group["total_questions"]} questions</p>',
                "<ol>",
            ]
            for question in group["questions"]:
                out += [
                    f'<li id="question-{escape(question["id"], quote=True)}">',
                    f'<div class="meta">{escape(question["asked_at"])} · '
                    f'{escape(question["source"])} · <code>{escape(question["id"])}</code></div>',
                    f'<div class="text">{escape(question["text"])}</div></li>',
                ]
            out += ["</ol><h4>Your reflection</h4>", "<p class=\"meta\">Self-reported.</p><dl>"]
            reflection = group["reflection"] or {}
            for key, label in REFLECTION_FIELDS:
                out += [
                    f"<dt>{label}</dt>",
                    f'<dd class="text">{escape(reflection.get(key) or "Not recorded.")}</dd>',
                ]
            out += [
                "</dl><p>Save a reflection in your terminal:</p>",
                f"<code>asktrace review {escape(group['id'])}"
                ' --goal "..." --learned "..." --open-question "..."</code>',
                "</article>",
            ]
        out += ['<p><a href="#top">Back to calendar</a></p></section>']
    out += ["</main></body></html>"]
    return "\n".join(out) + "\n"

"""Render a metrics Summary as Markdown or JSON."""

from __future__ import annotations

import json

from .metrics import Summary
from .models import DEPTH_LABELS


def to_json(summary: Summary) -> str:
    return json.dumps(summary.to_dict(), ensure_ascii=False, indent=2)


def to_markdown(summary: Summary) -> str:
    if summary.questions == 0:
        return "No tagged questions yet. Run `asktrace ingest ...` then `asktrace tag`.\n"
    s = summary
    lines = [
        "# asktrace report",
        "",
        f"Period: {s.period[0]} → {s.period[1]}",
        "",
        "These scores describe question patterns, not demonstrated understanding.",
        "Re-asking can reflect practice, delegation, or a different context.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Questions | {s.questions} |",
        f"| Distinct concepts | {s.concepts} |",
        f"| Exploration score (heuristic) | {s.accumulated_knowledge} |",
        f"| New concepts / active week | {s.acquisition_rate_per_week} |",
        f"| Re-ask rate (no deeper) | {s.re_ask_rate:.1%} |",
        f"| Deepening rate (revisits that went deeper) | {s.deepening_rate:.1%} |",
        f"| Mean depth (1 define … 5 design) | {s.mean_depth} |",
        "",
        "## Domains (distinct concepts)",
        "",
    ]
    lines += [f"- **{d}**: {n}" for d, n in s.domains.items()]
    lines += [
        "",
        "## Weekly",
        "",
        "| Week | Questions | New concepts | Revisited | Mean depth |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {w.week_start} | {w.questions} | {w.new_concepts} |"
        f" {w.revisited_concepts} | {w.mean_depth} |"
        for w in s.weeks
    ]
    lines += [
        "",
        "## Most explored concepts",
        "",
        "| Concept | Domain | Asks | Max depth | Days | Exploration score |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {c.concept} | {c.domain} | {c.asks} | {c.max_depth} ({DEPTH_LABELS[c.max_depth]}) |"
        f" {c.distinct_days} | {c.mastery:.2f} |"
        for c in s.top_concepts
    ]
    if s.shallow_repeats:
        lines += [
            "",
            "## Revisited at the same or lower question depth",
            "",
            "| Concept | Asks | Shallow re-asks | Max depth |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {c.concept} | {c.asks} | {c.re_asks_at_or_below_depth} | {c.max_depth} |"
            for c in s.shallow_repeats
        ]
    lines.append("")
    return "\n".join(lines)

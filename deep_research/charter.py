# ABOUTME: The research charter: a written brief parsed from and rendered to Markdown.
# ABOUTME: Pure logic, so a run is reproducible from a file rather than from memory.
from __future__ import annotations

import re
from dataclasses import dataclass


class CharterError(ValueError):
    """Raised when a charter is missing a field a detached run cannot proceed without."""


@dataclass(frozen=True)
class Charter:
    question: str
    decision: str
    must_answer: tuple[str, ...]
    source_tier: str
    recency: str
    deliverable: str
    out_of_scope: tuple[str, ...]


def _section(text: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _bullets(block: str) -> tuple[str, ...]:
    return tuple(
        line.lstrip("-").strip()
        for line in block.splitlines()
        if line.lstrip().startswith("-") and line.lstrip("- ").strip()
    )


def _labelled(block: str, label: str) -> str:
    for line in block.splitlines():
        if line.strip().lower().startswith(f"{label}:"):
            return line.split(":", 1)[1].strip()
    return ""


def parse_charter(text: str) -> Charter:
    title = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if not title:
        raise CharterError("charter is missing its question: no level-1 heading found")

    must_answer = _bullets(_section(text, "Must answer"))
    if not must_answer:
        raise CharterError("charter lists nothing under must answer")

    bar = _section(text, "Source bar")
    return Charter(
        question=title.group(1).strip(),
        decision=_section(text, "Decision this feeds"),
        must_answer=must_answer,
        source_tier=_labelled(bar, "tier"),
        recency=_labelled(bar, "recency"),
        deliverable=_section(text, "Deliverable"),
        out_of_scope=_bullets(_section(text, "Out of scope")),
    )


def render_charter(c: Charter) -> str:
    must = "\n".join(f"- {q}" for q in c.must_answer)
    scope = "\n".join(f"- {s}" for s in c.out_of_scope)
    return (
        f"# {c.question}\n\n"
        f"## Decision this feeds\n{c.decision}\n\n"
        f"## Must answer\n{must}\n\n"
        f"## Source bar\ntier: {c.source_tier}\nrecency: {c.recency}\n\n"
        f"## Deliverable\n{c.deliverable}\n\n"
        f"## Out of scope\n{scope}\n"
    )

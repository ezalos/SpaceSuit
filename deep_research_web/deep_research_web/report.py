# ABOUTME: Turns a finished Research artifact into report.md, sources.md and run-result.json.
# ABOUTME: Markers come from md_citations character offsets; numbering is by first appearance of a URL.
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPORT_NAME = "report.md"
SOURCES_NAME = "sources.md"
RESULT_NAME = "run-result.json"
CONVERSATION_NAME = "conversation.json"
DONE_SENTINEL = "DONE"

# A double-quoted span of 12+ characters, straight or curly. Twelve is SpaceSuit's
# MIN_QUOTE_CHARS: anything shorter matches every page and proves nothing.
_QUOTED_RE = re.compile(f'"([^"\n]{{12,}})"|“([^”\n]{{12,}})”')
_WORD_RE = re.compile(r"[a-z0-9]{4,}")
_NOT_VERIFIED = {"dead", "unverifiable", "misquoted"}


@dataclass(frozen=True)
class Citation:
    n: int
    url: str
    title: str
    origin: str
    start: int
    end: int


def citations_from_artifact(artifact: dict) -> list[Citation]:
    raw = sorted(
        artifact.get("md_citations") or [],
        key=lambda c: (int(c.get("start_index") or 0), int(c.get("end_index") or 0)),
    )
    numbers: dict[str, int] = {}
    out: list[Citation] = []
    for c in raw:
        url = str(c.get("url") or "").strip()
        if not url:
            continue
        n = numbers.setdefault(url, len(numbers) + 1)
        out.append(Citation(
            n, url, str(c.get("title") or ""), str(c.get("origin_tool_name") or ""),
            int(c.get("start_index") or 0), int(c.get("end_index") or 0),
        ))
    return out


def cited_span(content: str, c: Citation) -> str:
    # Offsets are taken as code points. If a report with astral characters (emoji) ever
    # shows drift, the web app counts UTF-16 units and this is where to convert.
    return content[max(c.start, 0):max(c.end, 0)]


def quoted_string(span: str) -> str | None:
    m = _QUOTED_RE.search(span)
    if not m:
        return None
    return (m.group(1) or m.group(2)).strip()


def insert_markers(content: str, citations: list[Citation]) -> str:
    # From the end so earlier offsets stay valid. Same offset: the lower n ends up first.
    out = content
    for end, n in sorted({(c.end, c.n) for c in citations}, reverse=True):
        end = min(max(end, 0), len(out))
        out = f"{out[:end]} [{n}]{out[end:]}"
    return out


def render_sources_md(citations: list[Citation], accessed: str) -> str:
    lines = ["# Sources", ""]
    seen: set[int] = set()
    for c in sorted(citations, key=lambda c: c.n):
        if c.n in seen:
            continue
        seen.add(c.n)
        lines.append(f"{c.n}. [{c.title or c.url}]({c.url}) via {c.origin or 'unknown'}, accessed {accessed}")
    return "\n".join(lines) + "\n"


def unanswered_hint(must_answer, content: str) -> list[str]:
    """Must-answer bullets whose significant words mostly do not appear in the report.

    A hint for the human, not a verdict: it catches a question the report never touched,
    not one it answered badly.
    """
    body = set(_WORD_RE.findall(content.lower()))
    out = []
    for q in must_answer:
        words = set(_WORD_RE.findall(q.lower()))
        if words and len(words & body) < max(1, (len(words) + 1) // 2):
            out.append(q)
    return out


def build_result(status: str, citations: list[Citation], graded, unanswered: list[str]) -> dict:
    return {
        "status": status,
        "sources_total": len({c.n for c in citations}),
        "sources_verified": sum(1 for g in graded if g.grade.value == "quoted"),
        "unanswered": list(unanswered),
        "unverified": [{"url": g.url, "reason": g.detail} for g in graded if g.grade.value in _NOT_VERIFIED],
        "sources": [
            {"n": g.n, "url": g.url, "quote": g.quote or "", "grade": g.grade.value, "detail": g.detail}
            for g in graded
        ],
    }


def write_report_files(
    out_dir: Path, content: str, citations: list[Citation], graded, unanswered: list[str],
    status: str = "complete", accessed: str | None = None,
) -> None:
    accessed = accessed or date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / REPORT_NAME).write_text(insert_markers(content, citations), encoding="utf-8")
    (out_dir / SOURCES_NAME).write_text(render_sources_md(citations, accessed), encoding="utf-8")
    result = build_result(status, citations, graded, unanswered)
    (out_dir / RESULT_NAME).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

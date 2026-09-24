# ABOUTME: Grades each Research citation: QUOTED, LIVE, MISQUOTED, DEAD, UNVERIFIABLE or UNCHECKED.
# ABOUTME: Reuses SpaceSuit verification; the only fetching in the package that leaves claude.ai.
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from deep_research.verify import MIN_QUOTE_CHARS, Fetched, VerdictKind, fetch, normalize, verify_source

from .report import Citation, cited_span, quoted_string

_READABLE = ("text/html", "application/xhtml", "text/plain")

# What a report adds around a quote rather than copying from the page: markdown inline-code
# backticks, and a sentence's own punctuation pulled inside the closing quote mark. The page
# is compared verbatim, so these graded a correctly-quoted report MISQUOTED on a one-character
# difference - six of eight citations on the 2026-09-17 Astra report, every one of them real.
# Only backticks are stripped, never * or _: those sit OUTSIDE the quote marks in this
# report format, and stripping _ would mangle an identifier like md_citations into a miss.
_QUOTE_EDGE = " \t\n.,;:!?\u2026"


def matchable(quote: str) -> str:
    """The part of a quote a page can be expected to carry verbatim.

    Only ever shorter than what it is given, so a real difference in the words still fails.
    The evidence floor in verify_source then applies to THIS, not to the padded original: a
    quote that is only long enough before trimming is not evidence after it.
    """
    return quote.replace("`", "").strip(_QUOTE_EDGE)


# "A ... B" is the ordinary way to quote two passages from one page, so requiring the joined
# string verbatim reported real citations as misquotes (two on the 2026-09-17 LIBERO run).
_ELLIPSIS = re.compile(r"\s*(?:\u2026|\.\.\.)\s*")


def elided_fragments(quote: str, page: str) -> str | None:
    """A detail line when every fragment of an elided quote is on the page, else None.

    The evidence floor applies to the LONGEST fragment, not to their total: three short
    fragments can add up past the floor while proving nothing, so at least one substantial
    passage has to be verbatim and every remaining fragment has to be there too.
    """
    parts = [normalize(matchable(p)) for p in _ELLIPSIS.split(quote)]
    parts = [p for p in parts if p]
    if len(parts) < 2 or not all(p in page for p in parts):
        return None
    if max(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return None
    return f"elided quote; every fragment found on the page ({len(parts)} fragments)"


class Grade(str, Enum):
    QUOTED = "quoted"
    LIVE = "live"
    MISQUOTED = "misquoted"
    DEAD = "dead"
    UNVERIFIABLE = "unverifiable"
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class Graded:
    n: int
    url: str
    grade: Grade
    quote: str | None
    detail: str


def _bare_domain(url: str) -> bool:
    without_scheme = re.sub(r"^[a-z]+://", "", url.strip(), flags=re.IGNORECASE)
    path = without_scheme.partition("/")[2].split("?")[0].split("#")[0]
    return path.strip("/") == ""


def grade_citation(c: Citation, span: str, fetcher: Callable = fetch, timeout: int | None = None) -> Graded:
    quote = quoted_string(span)
    if _bare_domain(c.url):
        return Graded(c.n, c.url, Grade.DEAD, quote, "bare domain, not the exact page carrying the claim")
    try:
        fetched = Fetched(*fetcher(c.url, timeout=timeout))
    except Exception as exc:  # the network stack raises a wide family; none should crash us
        return Graded(c.n, c.url, Grade.UNVERIFIABLE, quote, f"fetch failed: {exc}")
    if fetched.status != 200:
        return Graded(c.n, c.url, Grade.DEAD, quote, f"HTTP {fetched.status}")
    ctype = fetched.content_type
    if ctype and not any(ctype.startswith(t) for t in _READABLE):
        return Graded(c.n, c.url, Grade.UNVERIFIABLE, quote, f"cannot read content type {ctype}; check this one by hand")
    if not normalize(fetched.body):
        return Graded(c.n, c.url, Grade.UNVERIFIABLE, quote, "page had no readable text")
    if not quote:
        return Graded(c.n, c.url, Grade.LIVE, None, "page resolves; nothing verbatim to match")
    # The page is already in hand: verify_source gets it back through a closure, so the
    # quote check never fetches twice and never re-derives status on its own.
    v = verify_source({"n": c.n, "url": c.url, "quote": matchable(quote)},
                      fetcher=lambda url, timeout=None: fetched, timeout=timeout)
    if v.kind is VerdictKind.VERIFIED:
        return Graded(c.n, c.url, Grade.QUOTED, quote, v.detail)
    if v.kind is VerdictKind.CONTRADICTED:
        elided = elided_fragments(quote, normalize(fetched.body))
        if elided:
            return Graded(c.n, c.url, Grade.QUOTED, quote, elided)
        return Graded(c.n, c.url, Grade.MISQUOTED, quote, v.detail)
    return Graded(c.n, c.url, Grade.UNVERIFIABLE, quote, v.detail)


def grade_citations(
    citations: list[Citation], content: str, fetcher: Callable = fetch,
    verify: bool = True, timeout: int | None = None,
) -> list[Graded]:
    by_n: dict[int, list[Citation]] = {}
    for c in citations:
        by_n.setdefault(c.n, []).append(c)
    out: list[Graded] = []
    for n, group in sorted(by_n.items()):
        if not verify:
            out.append(Graded(n, group[0].url, Grade.UNCHECKED, None, "not verified (--no-verify)"))
            continue
        spans = [cited_span(content, c) for c in group]
        with_quote = [s for s in spans if quoted_string(s)]
        out.append(grade_citation(group[0], with_quote[0] if with_quote else spans[0], fetcher=fetcher, timeout=timeout))
    return out


def count_grades(graded: list[Graded]) -> dict[str, int]:
    counts = {g.value: 0 for g in Grade}
    for item in graded:
        counts[item.grade.value] += 1
    return counts

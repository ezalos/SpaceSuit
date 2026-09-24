# ABOUTME: Tests for turning a Research artifact into report.md, sources.md and run-result.json.
# ABOUTME: Marker offsets come from md_citations; numbering is by first appearance of a URL.
import json
from dataclasses import dataclass
from enum import Enum

from conftest import artifact_block, citation

from deep_research_web.report import (
    Citation, build_result, citations_from_artifact, cited_span, insert_markers, quoted_string,
    render_sources_md, unanswered_hint, write_report_files,
)

CONTENT = "Alpha costs $5 per month. Beta is faster. Alpha again."
#          0123456789012345678901234567890123456789012345678901234
ART = artifact_block(CONTENT, [
    citation("https://x.test/beta", 26, 41),        # "Beta is faster." cited second in text
    citation("https://x.test/alpha", 0, 25),        # "Alpha costs $5 per month." first
    citation("https://x.test/alpha", 42, 54),       # same url again
])


class G(str, Enum):
    QUOTED = "quoted"; LIVE = "live"; DEAD = "dead"


@dataclass
class Graded:
    n: int; url: str; grade: G; quote: str | None; detail: str


def test_numbering_by_first_appearance_of_url_in_text_order():
    cits = citations_from_artifact(ART["input"])
    assert [(c.n, c.url, c.start, c.end) for c in cits] == [
        (1, "https://x.test/alpha", 0, 25), (2, "https://x.test/beta", 26, 41), (1, "https://x.test/alpha", 42, 54),
    ]
    assert cited_span(CONTENT, cits[1]) == "Beta is faster."


def test_markers_are_inserted_at_end_offsets_without_shifting_each_other():
    cits = citations_from_artifact(ART["input"])
    assert insert_markers(CONTENT, cits) == "Alpha costs $5 per month. [1] Beta is faster. [2] Alpha again. [1]"


def test_two_citations_at_the_same_offset_keep_ascending_order():
    cits = [Citation(2, "u2", "", "", 0, 5), Citation(1, "u1", "", "", 0, 5)]
    assert insert_markers("Hello world", cits) == "Hello [1] [2] world"


def test_quoted_string_needs_twelve_characters_and_accepts_curly_quotes():
    assert quoted_string('it says "the price is five dollars" here') == "the price is five dollars"
    assert quoted_string('it says "short" here') is None
    assert quoted_string("it says “curly quoted sentence here” ok") == "curly quoted sentence here"
    assert quoted_string("no quotes at all in this span") is None


def test_sources_md_lists_each_number_once():
    cits = citations_from_artifact(ART["input"])
    md = render_sources_md(cits, accessed="2026-09-16")
    assert md.count("\n1. ") == 1 and md.count("\n2. ") == 1
    assert "[https://x.test/alpha](https://x.test/alpha) via web_fetch, accessed 2026-09-16" in md


def test_unanswered_hint_flags_bullets_whose_words_are_absent():
    content = "# Cost at ten million vectors\nAlpha costs five dollars monthly at that scale."
    must = ("Cost at 10M vectors monthly", "p99 latency under sustained load")
    assert unanswered_hint(must, content) == ["p99 latency under sustained load"]


def test_build_result_matches_the_v1_shape(tmp_path):
    cits = citations_from_artifact(ART["input"])
    graded = [Graded(1, "https://x.test/alpha", G.QUOTED, "Alpha costs $5 per month", "ok"),
              Graded(2, "https://x.test/beta", G.DEAD, None, "HTTP 404")]
    result = build_result("complete", cits, graded, ["p99"])
    assert result["sources_total"] == 2 and result["sources_verified"] == 1
    assert result["unanswered"] == ["p99"]
    assert result["unverified"] == [{"url": "https://x.test/beta", "reason": "HTTP 404"}]
    assert result["sources"][0] == {"n": 1, "url": "https://x.test/alpha", "quote": "Alpha costs $5 per month", "grade": "quoted", "detail": "ok"}
    write_report_files(tmp_path, CONTENT, cits, graded, ["p99"], accessed="2026-09-16")
    assert (tmp_path / "report.md").read_text().startswith("Alpha costs $5 per month. [1]")
    assert json.loads((tmp_path / "run-result.json").read_text())["status"] == "complete"
    assert (tmp_path / "sources.md").exists()

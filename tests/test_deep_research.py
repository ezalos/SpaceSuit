# ABOUTME: Tests the deep_research package pure logic: charter, manifest, status, runner prompt.
# ABOUTME: No subprocesses and no network; the real claude --bg launch is covered separately.

import pytest

from deep_research.charter import Charter, CharterError, parse_charter, render_charter

CHARTER_TEXT = """# Which vector database fits a 10M chunk corpus

## Decision this feeds
Picking the store before the ingestion pipeline is written.

## Must answer
- What are the p95 query latencies at 10M vectors
- What does each cost per month at that scale

## Source bar
tier: vendor benchmarks only when reproduced by a third party
recency: 2024-01-01 onwards

## Deliverable
A comparison table plus a one paragraph recommendation.

## Out of scope
- Self hosted Kubernetes operational burden
"""


def test_parse_charter_extracts_every_field():
    c = parse_charter(CHARTER_TEXT)
    assert c.question == "Which vector database fits a 10M chunk corpus"
    assert c.decision == "Picking the store before the ingestion pipeline is written."
    assert c.must_answer == (
        "What are the p95 query latencies at 10M vectors",
        "What does each cost per month at that scale",
    )
    assert c.source_tier == "vendor benchmarks only when reproduced by a third party"
    assert c.recency == "2024-01-01 onwards"
    assert c.deliverable == "A comparison table plus a one paragraph recommendation."
    assert c.out_of_scope == ("Self hosted Kubernetes operational burden",)


def test_charter_round_trips():
    c = parse_charter(CHARTER_TEXT)
    assert parse_charter(render_charter(c)) == c


def test_parse_charter_rejects_missing_question():
    with pytest.raises(CharterError, match="question"):
        parse_charter("## Decision this feeds\nnothing\n")


def test_parse_charter_rejects_empty_must_answer():
    text = CHARTER_TEXT.replace(
        "- What are the p95 query latencies at 10M vectors\n"
        "- What does each cost per month at that scale\n",
        "",
    )
    with pytest.raises(CharterError, match="must answer"):
        parse_charter(text)


def test_parse_charter_strips_indented_bullet_dashes():
    text = CHARTER_TEXT.replace(
        "- What are the p95 query latencies at 10M vectors\n"
        "- What does each cost per month at that scale\n",
        "  - What are the p95 query latencies at 10M vectors\n"
        "  - What does each cost per month at that scale\n",
    )
    c = parse_charter(text)
    assert c.must_answer == (
        "What are the p95 query latencies at 10M vectors",
        "What does each cost per month at that scale",
    )
    for item in c.must_answer:
        assert not item.startswith("-")
        assert item == item.strip()


FENCED_CHARTER_TEXT = """# Which vector database fits a 10M chunk corpus

## Decision this feeds
Picking the store before the ingestion pipeline is written.

## Must answer
- What are the p95 query latencies at 10M vectors
- What does each cost per month at that scale

## Source bar
tier: vendor benchmarks only when reproduced by a third party
recency: 2024-01-01 onwards

## Deliverable
A comparison table plus a one paragraph recommendation.

```markdown
## This is sample text inside a fence, not a real heading
- not a real bullet either
```

## Out of scope
- Self hosted Kubernetes operational burden
"""


def test_parse_charter_ignores_section_headings_inside_fenced_code():
    c = parse_charter(FENCED_CHARTER_TEXT)
    assert c.out_of_scope == ("Self hosted Kubernetes operational burden",)
    assert "## This is sample text inside a fence, not a real heading" in c.deliverable
    assert "- not a real bullet either" in c.deliverable


def test_fenced_charter_round_trips():
    c = parse_charter(FENCED_CHARTER_TEXT)
    assert parse_charter(render_charter(c)) == c

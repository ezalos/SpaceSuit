# ABOUTME: Tests for the claude.ai Research prompt rendered from a charter.
# ABOUTME: Checks every charter field lands and the verbatim-quote instruction is present.
import dataclasses

from deep_research.charter import Charter

from deep_research_web.prompt import CITATION_RULE, START_RULE, build_web_prompt

CHARTER = Charter(
    question="Which vector database for 10M embeddings?",
    decision="Whether to migrate off pgvector this quarter",
    must_answer=("Cost at 10M vectors", "p99 latency under load"),
    source_tier="vendor docs and independent benchmarks",
    recency="2025-01-01 onwards",
    deliverable="A comparison table and a recommendation",
    out_of_scope=("Graph databases",),
)


def test_every_field_is_rendered():
    text = build_web_prompt(CHARTER)
    for needle in (
        CHARTER.question, CHARTER.decision, "- Cost at 10M vectors", "- p99 latency under load",
        "tier: vendor docs and independent benchmarks", "recency: 2025-01-01 onwards",
        CHARTER.deliverable, "- Graph databases", CITATION_RULE, START_RULE,
    ):
        assert needle in text


def test_empty_out_of_scope_says_so():
    text = build_web_prompt(dataclasses.replace(CHARTER, out_of_scope=()))
    assert "- nothing excluded" in text


def test_prompt_never_mentions_files():
    # The web run writes no files; a leftover v1 instruction would confuse the agent.
    text = build_web_prompt(CHARTER)
    assert "report.md" not in text and "sentinel" not in text.lower()

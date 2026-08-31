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


from datetime import datetime
from pathlib import Path

from deep_research.manifest import (
    MANIFEST_NAME,
    Manifest,
    active_runs,
    find_runs,
    make_run_id,
    read_manifest,
    slugify,
    write_manifest,
)


def _manifest(**over) -> Manifest:
    base = dict(
        run_id="2026-08-31-143022-vector-db",
        bg_session_id="8c969912",
        engine="local",
        model="fable",
        effort="max",
        charter="/runs/x/charter.md",
        out_dir="/runs/x",
        started_at="2026-08-31T14:30:22+02:00",
        status="running",
    )
    base.update(over)
    return Manifest(**base)


def test_slugify_is_filesystem_safe_and_bounded():
    assert slugify("Which vector DB fits 10M chunks?!") == "which-vector-db-fits-10m-chunks"
    assert len(slugify("word " * 60)) <= 40
    assert not slugify("word " * 60).endswith("-")


def test_make_run_id_is_sortable_and_carries_the_slug():
    rid = make_run_id("Which vector DB", datetime(2026, 8, 31, 14, 30, 22))
    assert rid == "2026-08-31-143022-which-vector-db"


def test_manifest_round_trips_through_disk(tmp_path):
    m = _manifest()
    written = write_manifest(tmp_path, m)
    assert written.name == MANIFEST_NAME
    assert read_manifest(tmp_path) == m


def test_find_runs_discovers_nested_manifests_and_skips_junk(tmp_path):
    a = tmp_path / "run-a"
    b = tmp_path / "run-b"
    a.mkdir()
    b.mkdir()
    write_manifest(a, _manifest(run_id="a", out_dir=str(a)))
    write_manifest(b, _manifest(run_id="b", out_dir=str(b), status="done"))
    (tmp_path / "not-a-run").mkdir()
    (tmp_path / "not-a-run" / "run.json").write_text("{ broken", encoding="utf-8")

    found = {m.run_id for m in find_runs(tmp_path)}
    assert found == {"a", "b"}


def test_active_runs_counts_only_running():
    runs = [_manifest(run_id="a"), _manifest(run_id="b", status="done")]
    assert [m.run_id for m in active_runs(runs)] == ["a"]


def test_slugify_punctuation_only_falls_back_to_untitled():
    assert slugify("?!...") == "untitled"
    rid = make_run_id("?!...", datetime(2026, 8, 31, 14, 30, 22))
    assert not rid.endswith("-")


def test_find_runs_skips_undecodable_manifest(tmp_path):
    good = tmp_path / "run-good"
    good.mkdir()
    write_manifest(good, _manifest(run_id="good", out_dir=str(good)))

    bad = tmp_path / "run-bad"
    bad.mkdir()
    (bad / MANIFEST_NAME).write_bytes(b"\xff\xfe\x00binary")

    found = {m.run_id for m in find_runs(tmp_path)}
    assert found == {"good"}


def test_find_runs_skips_manifest_with_mismatched_keys(tmp_path):
    good = tmp_path / "run-good"
    good.mkdir()
    write_manifest(good, _manifest(run_id="good", out_dir=str(good)))

    bad = tmp_path / "run-bad"
    bad.mkdir()
    (bad / MANIFEST_NAME).write_text('{"foo": "bar"}', encoding="utf-8")

    found = {m.run_id for m in find_runs(tmp_path)}
    assert found == {"good"}

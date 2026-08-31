# ABOUTME: Tests the deep_research package pure logic: charter, manifest, status, runner prompt.
# ABOUTME: No subprocesses and no network; the real claude --bg launch is covered separately.

import re

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


from deep_research.status import (
    DONE_SENTINEL,
    REPORT_NAME,
    RunState,
    resolve_state,
)


def test_done_sentinel_wins_even_if_the_session_is_gone(tmp_path):
    (tmp_path / DONE_SENTINEL).write_text("", encoding="utf-8")
    (tmp_path / REPORT_NAME).write_text("# report", encoding="utf-8")
    assert resolve_state(tmp_path, session_alive=False) is RunState.DONE


def test_no_sentinel_but_session_alive_is_running(tmp_path):
    assert resolve_state(tmp_path, session_alive=True) is RunState.RUNNING


def test_dead_session_with_a_partial_report_is_incomplete(tmp_path):
    (tmp_path / REPORT_NAME).write_text("# half a report", encoding="utf-8")
    assert resolve_state(tmp_path, session_alive=False) is RunState.INCOMPLETE


def test_dead_session_with_nothing_written_is_lost(tmp_path):
    assert resolve_state(tmp_path, session_alive=False) is RunState.LOST


from deep_research.runner import build_runner_prompt


def test_runner_prompt_carries_the_question_and_every_sub_question():
    c = parse_charter(CHARTER_TEXT)
    prompt = build_runner_prompt(c, Path("/runs/x"))
    assert c.question in prompt
    for q in c.must_answer:
        assert q in prompt


def test_runner_prompt_states_the_output_contract_with_absolute_paths():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    assert "/runs/x/report.md" in prompt
    assert "/runs/x/sources.md" in prompt
    assert "/runs/x/run-result.json" in prompt
    assert "/runs/x/DONE" in prompt


def test_runner_prompt_demands_fetched_and_quoted_citations():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x")).lower()
    assert "verbatim" in prompt
    assert "bare domain" in prompt
    assert "webfetch" in prompt


def test_runner_prompt_orders_the_sentinel_last():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    assert prompt.index("report.md") < prompt.index("/runs/x/DONE")
    assert "last" in prompt.lower()


def test_runner_prompt_includes_notify_only_when_a_script_is_given():
    c = parse_charter(CHARTER_TEXT)
    assert "notify.sh" not in build_runner_prompt(c, Path("/runs/x"))
    with_notify = build_runner_prompt(c, Path("/runs/x"), notify_script="/n/notify.sh")
    assert "/n/notify.sh" in with_notify


def test_runner_prompt_makes_a_relative_out_dir_absolute():
    # The prompt promises absolute paths, and the detached agent's cwd need not match
    # the caller's, so a relative path would strand the files where nobody polls.
    # Pull the path the prompt actually emits and assert it is absolute; a plain
    # substring check cannot work, since the resolved path still ENDS with the
    # relative one.
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("relative/out"))
    emitted = re.search(r"^1\. (\S*report\.md)\s*$", prompt, re.MULTILINE)
    assert emitted, f"no report.md line in prompt:\n{prompt}"
    assert Path(emitted.group(1)).is_absolute()
    assert emitted.group(1) == str((Path.cwd() / "relative" / "out" / "report.md"))


def test_runner_prompt_forbids_citing_an_unverified_source():
    # Listing a source as unverified must not be a licence to cite it anyway: without
    # this rule an agent can satisfy every other line and still ship an uncited claim.
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    assert "unverified source may NEVER back an [n] marker" in prompt
    assert "unanswered" in prompt


import json as _json_mod
from types import SimpleNamespace

from deep_research.launcher import (
    CONCURRENCY_CAP,
    LaunchError,
    launch,
    parse_session_id,
    session_alive,
)

BG_STDOUT = (
    "Starting background service\n"
    "backgrounded · 8c969912\n"
    "  claude agents             list sessions\n"
    "  claude attach 8c969912    open in this terminal\n"
)


def test_parse_session_id_reads_the_backgrounded_line():
    assert parse_session_id(BG_STDOUT) == "8c969912"


def test_parse_session_id_rejects_output_without_an_id():
    with pytest.raises(LaunchError, match="session id"):
        parse_session_id("error: could not start background service\n")


def test_launch_writes_a_manifest_and_returns_it(tmp_path):
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    out = tmp_path / "run"
    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=out,
        runs_root=tmp_path,
        now=datetime(2026, 8, 31, 14, 30, 22),
        runner=fake_runner,
    )

    assert m.bg_session_id == "8c969912"
    assert m.model == "fable" and m.effort == "max"
    assert m.status == "running"
    assert read_manifest(out) == m
    assert (out / "charter.md").exists()

    cmd = calls[0]
    assert cmd[:2] == ["claude", "--bg"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "fable"
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "max"


def test_launch_refuses_past_the_concurrency_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    def fake_runner(cmd, **kwargs):
        raise AssertionError("must not spawn past the cap")

    with pytest.raises(LaunchError, match="already running"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=tmp_path / "new",
            runs_root=tmp_path,
            runner=fake_runner,
        )


def test_force_overrides_the_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=tmp_path / "new",
        runs_root=tmp_path,
        force=True,
        runner=fake_runner,
    )
    assert m.status == "running"


def test_launch_raises_when_claude_exits_nonzero(tmp_path):
    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(LaunchError, match="boom"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=tmp_path / "run",
            runs_root=tmp_path,
            runner=fake_runner,
        )


def test_launch_refuses_to_overwrite_an_existing_run(tmp_path):
    out = tmp_path / "run"
    write_manifest(out, _manifest(out_dir=str(out)))

    def fake_runner(cmd, **kwargs):
        raise AssertionError("must not spawn over an existing run")

    with pytest.raises(LaunchError, match="already holds"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=out,
            runs_root=tmp_path / "elsewhere",
            runner=fake_runner,
        )


AGENTS_JSON = _json_mod.dumps(
    [
        {
            "pid": 1,
            "id": "4c00ef07",
            "kind": "background",
            "sessionId": "4c00ef07-0c5c-4c7c-bad5-478c1fb7f234",
            "name": "a finished run",
            "status": "idle",
            "state": "done",
        },
        {
            "pid": 2,
            "id": "8c969912",
            "kind": "background",
            "sessionId": "8c969912-1111-2222-3333-444444444444",
            "name": "a live run",
            "status": "busy",
            "state": "running",
        },
    ]
)


def _agents_runner(payload: str, returncode: int = 0):
    def run(cmd, **kwargs):
        assert cmd == ["claude", "agents", "--json"]
        return SimpleNamespace(returncode=returncode, stdout=payload, stderr="")

    return run


def test_session_alive_is_true_for_a_running_background_session():
    assert session_alive("8c969912", runner=_agents_runner(AGENTS_JSON)) is True


def test_session_alive_is_false_once_the_state_is_done():
    assert session_alive("4c00ef07", runner=_agents_runner(AGENTS_JSON)) is False


def test_session_alive_is_false_when_the_id_is_absent():
    assert session_alive("deadbeef", runner=_agents_runner(AGENTS_JSON)) is False


def test_session_alive_matches_on_the_session_id_prefix():
    payload = _json_mod.dumps(
        [{"sessionId": "abc12345-0000-0000-0000-000000000000", "state": "running"}]
    )
    assert session_alive("abc12345", runner=_agents_runner(payload)) is True


def test_session_alive_is_false_rather_than_raising_when_claude_is_missing():
    def missing(cmd, **kwargs):
        raise FileNotFoundError("claude")

    assert session_alive("8c969912", runner=missing) is False


def test_session_alive_is_false_on_unparseable_output():
    assert session_alive("8c969912", runner=_agents_runner("not json")) is False

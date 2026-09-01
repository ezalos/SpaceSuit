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
    assert (out / "charter.rendered.md").exists()
    assert m.charter == str(out / "charter.rendered.md")

    cmd = calls[0]
    assert cmd[:2] == ["claude", "--bg"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "fable"
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "max"


def test_launch_refuses_past_the_concurrency_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    agents_payload = _json_mod.dumps(
        [{"id": "8c969912", "sessionId": "8c969912-0000", "state": "running"}]
    )

    def fake_runner(cmd, **kwargs):
        if cmd == ["claude", "agents", "--json"]:
            return SimpleNamespace(returncode=0, stdout=agents_payload, stderr="")
        if cmd[:2] == ["claude", "--bg"]:
            raise AssertionError("must not spawn past the cap")
        raise AssertionError(f"unexpected command: {cmd}")

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

    agents_payload = _json_mod.dumps(
        [{"id": "8c969912", "sessionId": "8c969912-0000", "state": "running"}]
    )

    def fake_runner(cmd, **kwargs):
        if cmd == ["claude", "agents", "--json"]:
            return SimpleNamespace(returncode=0, stdout=agents_payload, stderr="")
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=tmp_path / "new",
        runs_root=tmp_path,
        force=True,
        runner=fake_runner,
    )
    assert m.status == "running"


def test_finished_run_does_not_count_against_the_cap(tmp_path):
    # A manifest's status is written once, at launch, and nothing rewrites it: two
    # finished runs must not wedge the cap just because their run.json still says
    # "running". A DONE sentinel on disk is enough to prove a run is over without
    # even asking the agents list, so the fake runner here answers only claude --bg.
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))
        (d / DONE_SENTINEL).write_text("", encoding="utf-8")

    def fake_runner(cmd, **kwargs):
        assert cmd[:2] == ["claude", "--bg"], f"unexpected command: {cmd}"
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=tmp_path / "new",
        runs_root=tmp_path,
        runner=fake_runner,
    )
    assert m.status == "running"


def test_dead_session_does_not_count_against_the_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    agents_payload = _json_mod.dumps(
        [{"id": "8c969912", "sessionId": "8c969912-0000", "state": "done"}]
    )

    def fake_runner(cmd, **kwargs):
        if cmd == ["claude", "agents", "--json"]:
            return SimpleNamespace(returncode=0, stdout=agents_payload, stderr="")
        assert cmd[:2] == ["claude", "--bg"], f"unexpected command: {cmd}"
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=tmp_path / "new",
        runs_root=tmp_path,
        runner=fake_runner,
    )
    assert m.status == "running"


def test_two_genuinely_live_runs_still_block_the_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    agents_payload = _json_mod.dumps(
        [{"id": "8c969912", "sessionId": "8c969912-0000", "state": "running"}]
    )

    def fake_runner(cmd, **kwargs):
        if cmd == ["claude", "agents", "--json"]:
            return SimpleNamespace(returncode=0, stdout=agents_payload, stderr="")
        raise AssertionError(f"must not spawn past the cap: {cmd}")

    with pytest.raises(LaunchError, match="already running"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=tmp_path / "new",
            runs_root=tmp_path,
            runner=fake_runner,
        )


def test_launch_stores_an_absolute_out_dir_when_handed_a_relative_path(tmp_path, monkeypatch):
    # The CLI passes Path(args.out) straight through, so a relative --out is the normal
    # case. The manifest's out_dir is read back later by status/collect, potentially
    # from a different working directory, so it must be resolved before it is recorded.
    monkeypatch.chdir(tmp_path)

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=Path("new"),
        runs_root=Path("."),
        now=datetime(2026, 8, 31, 14, 30, 22),
        runner=fake_runner,
    )

    assert Path(m.out_dir).is_absolute()
    assert Path(m.out_dir) == (tmp_path / "new").resolve()
    assert Path(m.charter).is_absolute()


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


def test_launch_does_not_overwrite_an_existing_charter_file(tmp_path):
    # SKILL.md tells the operator to write their charter to <out>/charter.md and pass
    # that same path as --charter. If launch() ever renders back onto that path, it
    # silently destroys anything outside the seven known fields (e.g. operator notes).
    out = tmp_path / "run"
    out.mkdir(parents=True)
    charter_path = out / "charter.md"
    original_text = CHARTER_TEXT + "\n## Notes for me\ndistinctive-marker-line\n"
    charter_path.write_text(original_text, encoding="utf-8")

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    launch(
        parse_charter(CHARTER_TEXT),
        out_dir=out,
        runs_root=tmp_path,
        now=datetime(2026, 8, 31, 14, 30, 22),
        runner=fake_runner,
    )

    assert "distinctive-marker-line" in charter_path.read_text(encoding="utf-8")


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


from deep_research.__main__ import main


def test_status_reports_done_for_a_finished_run(tmp_path, capsys):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")

    rc = main(["status", "--runs-root", str(tmp_path)])
    assert rc == 0
    assert "done" in capsys.readouterr().out


def test_collect_shouts_about_unverified_sources(tmp_path, capsys):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")
    (out / "report.md").write_text("# findings", encoding="utf-8")
    (out / "run-result.json").write_text(
        _json_mod.dumps(
            {
                "status": "partial",
                "sources_total": 3,
                "sources_verified": 2,
                "unanswered": ["what does it cost"],
                "unverified": [{"url": "https://x.test/a", "reason": "404"}],
            }
        ),
        encoding="utf-8",
    )

    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "https://x.test/a" in printed
    assert "UNVERIFIED" in printed.upper()
    assert "what does it cost" in printed


def test_collect_is_clean_for_a_fully_verified_run(tmp_path, capsys):
    # Pinned to --no-verify: this test has always covered the self-report path, and the
    # fetching path added later has its own tests. Without the flag it would now fail
    # correctly, because this fixture claims 3 sources but lists none to check.
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")
    (out / "report.md").write_text("# findings", encoding="utf-8")
    (out / "run-result.json").write_text(
        _json_mod.dumps(
            {
                "status": "complete",
                "sources_total": 3,
                "sources_verified": 3,
                "unanswered": [],
                "unverified": [],
            }
        ),
        encoding="utf-8",
    )

    rc = main(["collect", "r1", "--runs-root", str(tmp_path), "--no-verify"])
    assert rc == 0
    assert "report.md" in capsys.readouterr().out


def test_collect_errors_on_an_unknown_run(tmp_path, capsys):
    rc = main(["collect", "nope", "--runs-root", str(tmp_path)])
    assert rc == 2
    assert "nope" in capsys.readouterr().err


def test_list_prints_every_run(tmp_path, capsys):
    for rid in ("r1", "r2"):
        d = tmp_path / rid
        write_manifest(d, _manifest(run_id=rid, out_dir=str(d)))
    assert main(["list", "--runs-root", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "r1" in printed and "r2" in printed


def test_list_shows_done_for_a_run_with_a_done_sentinel(tmp_path, capsys):
    # m.status is written once at launch and never rewritten, so it always reads
    # "running". list must report the real, on-disk state, not the stale field.
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")

    rc = main(["list", "--runs-root", str(tmp_path)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "done" in printed
    assert "running" not in printed


def test_cmd_launch_derives_runs_root_from_out_so_the_cap_counts_existing_runs(
    tmp_path, monkeypatch, capsys
):
    # SKILL.md documents `deep-research launch --charter <out>/charter.md --out <out>`
    # with no --runs-root, which defaults to ~/research-runs. The manifest itself always
    # lands under --out regardless of runs_root, so discoverability alone does not
    # exercise the bug; the observable break is the concurrency cap, which counts runs
    # under runs_root. Two sibling runs already live under <out>'s parent: pre-fix the
    # cap looks under ~/research-runs (empty here), sees zero, and never fires;
    # post-fix it looks under the derived root, sees both, and refuses.
    from deep_research import __main__ as dr_main

    parent = tmp_path / "runs"
    parent.mkdir()
    for i in range(CONCURRENCY_CAP):
        d = parent / f"busy{i}"
        write_manifest(
            d, _manifest(run_id=f"busy{i}", out_dir=str(d), bg_session_id="8c969912")
        )

    agents_payload = _json_mod.dumps(
        [{"id": "8c969912", "sessionId": "8c969912-0000", "state": "running"}]
    )

    def fake_runner(cmd, **kw):
        if cmd == ["claude", "agents", "--json"]:
            return SimpleNamespace(returncode=0, stdout=agents_payload, stderr="")
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    charter_path = tmp_path / "charter.md"
    charter_path.write_text(CHARTER_TEXT, encoding="utf-8")

    real_launch = dr_main.launch

    def fake_launch(charter, out_dir, runs_root, **kwargs):
        return real_launch(
            charter, out_dir=out_dir, runs_root=runs_root, runner=fake_runner, **kwargs
        )

    monkeypatch.setattr(dr_main, "launch", fake_launch)

    rc = dr_main.main(
        ["launch", "--charter", str(charter_path), "--out", str(parent / "new")]
    )
    assert rc == 1
    assert "already running" in capsys.readouterr().err


def test_collect_on_a_done_run_missing_run_result_is_never_clean(tmp_path, capsys):
    # The runner prompt writes run-result.json BEFORE the DONE sentinel, so a DONE run
    # missing it broke its own completion contract. We know nothing about whether any
    # source was verified, which must never read as a clean exit.
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")
    (out / "report.md").write_text("# findings", encoding="utf-8")

    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "run-result.json" in printed


def test_collect_on_a_done_run_missing_everything_is_never_clean(tmp_path, capsys):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")

    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    assert rc == 1


import subprocess


def test_stop_reports_a_message_instead_of_a_traceback_when_claude_is_missing(
    tmp_path, capsys, monkeypatch
):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))

    def missing(cmd, **kwargs):
        raise FileNotFoundError("claude")

    # Patching subprocess.run itself, not the unit under test: cmd_stop has no
    # injected runner (unlike launcher.session_alive), so this is the same posture
    # as test_session_alive_is_false_rather_than_raising_when_claude_is_missing.
    monkeypatch.setattr(subprocess, "run", missing)

    rc = main(["stop", "r1", "--runs-root", str(tmp_path)])
    assert rc == 1
    assert "cannot run claude" in capsys.readouterr().err


# --------------------------------------------------------------------------
# source verification (deep_research.verify)
# --------------------------------------------------------------------------

from deep_research.verify import (
    Verdict,
    VerdictKind,
    normalize,
    verify_sources,
)

PAGE = """<html><head><style>.x{color:red}</style><script>var a="Capital: Nowhere";</script></head>
<body><h1>Portugal</h1><p>The  capital
is <b>Lisbon</b>&nbsp;&mdash; the largest city.</p>
<p>It&rsquo;s on the Atlantic.</p></body></html>"""


def _fetcher(pages):
    """Fetcher stub over a {url: (status, body)} map; a missing url is a network error."""

    def fetch(url, timeout=None):
        if url not in pages:
            raise OSError(f"unreachable: {url}")
        status, body = pages[url]
        return status, body

    return fetch


def test_normalize_strips_tags_scripts_entities_and_collapses_whitespace():
    text = normalize(PAGE)
    assert "The capital is Lisbon" in text
    assert "<b>" not in text and "color:red" not in text
    # script content must not become quotable page text
    assert "Capital: Nowhere" not in text
    # &nbsp; and &mdash; decoded, runs of whitespace collapsed to one space
    assert "Lisbon - the largest city" in text


def test_normalize_folds_curly_punctuation_so_a_straight_quote_still_matches():
    assert "It's on the Atlantic" in normalize(PAGE)


def test_verify_sources_confirms_a_quote_present_on_the_page():
    sources = [{"n": 1, "url": "https://x.test/a", "quote": "The capital is Lisbon"}]
    verdicts = verify_sources(sources, fetcher=_fetcher({"https://x.test/a": (200, PAGE)}))
    assert [v.kind for v in verdicts] == [VerdictKind.VERIFIED]
    assert verdicts[0].url == "https://x.test/a"


def test_verify_sources_flags_a_quote_absent_from_the_page_as_contradicted():
    sources = [{"n": 1, "url": "https://x.test/a", "quote": "The capital is Madrid"}]
    verdicts = verify_sources(sources, fetcher=_fetcher({"https://x.test/a": (200, PAGE)}))
    assert verdicts[0].kind is VerdictKind.CONTRADICTED


def test_verify_sources_marks_an_http_error_unverifiable_not_contradicted():
    sources = [{"n": 1, "url": "https://x.test/a", "quote": "a quote long enough to check"}]
    verdicts = verify_sources(sources, fetcher=_fetcher({"https://x.test/a": (403, "")}))
    assert verdicts[0].kind is VerdictKind.UNVERIFIABLE
    assert "403" in verdicts[0].detail


def test_verify_sources_marks_a_network_failure_unverifiable():
    sources = [{"n": 1, "url": "https://gone.test/a", "quote": "a quote long enough to check"}]
    verdicts = verify_sources(sources, fetcher=_fetcher({}))
    assert verdicts[0].kind is VerdictKind.UNVERIFIABLE


def test_verify_sources_marks_a_bare_domain_unverifiable_without_fetching():
    # A bare domain is not an exact source; the citation contract forbids it, and
    # fetching it would often succeed and wrongly look like evidence.
    def exploding_fetcher(url, timeout=None):
        raise AssertionError("must not fetch a bare domain")

    sources = [{"n": 1, "url": "https://x.test", "quote": "a quote long enough to check"}]
    verdicts = verify_sources(sources, fetcher=exploding_fetcher)
    assert verdicts[0].kind is VerdictKind.UNVERIFIABLE
    assert "bare domain" in verdicts[0].detail.lower()


def test_verify_sources_marks_a_missing_quote_unverifiable():
    sources = [{"n": 1, "url": "https://x.test/a", "quote": ""}]
    verdicts = verify_sources(sources, fetcher=_fetcher({"https://x.test/a": (200, PAGE)}))
    assert verdicts[0].kind is VerdictKind.UNVERIFIABLE


def test_verdict_is_hashable_and_carries_its_index():
    v = Verdict(n=3, url="u", quote="q", kind=VerdictKind.VERIFIED, detail="")
    assert v.n == 3


# --------------------------------------------------------------------------
# collect: independent verification wiring
# --------------------------------------------------------------------------

import deep_research.__main__ as dr_main

CLEAN_RESULT = {
    "status": "complete",
    "sources_total": 1,
    "sources_verified": 1,
    "unanswered": [],
    "unverified": [],
    "sources": [{"n": 1, "url": "https://x.test/a", "quote": "The capital is Lisbon"}],
}


def _finished_run(tmp_path, result):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")
    (out / "report.md").write_text("# findings [1]", encoding="utf-8")
    (out / "run-result.json").write_text(_json_mod.dumps(result), encoding="utf-8")
    return out


def _stub_verdicts(monkeypatch, verdicts):
    monkeypatch.setattr(dr_main, "verify_sources", lambda sources, **kw: verdicts)


def test_collect_verifies_sources_by_default_and_stays_clean_when_they_check_out(
    tmp_path, capsys, monkeypatch
):
    _finished_run(tmp_path, CLEAN_RESULT)
    _stub_verdicts(
        monkeypatch,
        [Verdict(1, "https://x.test/a", "q", VerdictKind.VERIFIED, "quote found")],
    )
    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "1/1 confirmed" in printed


def test_collect_fails_when_a_quote_is_not_on_the_page(tmp_path, capsys, monkeypatch):
    # The agent self-reported a clean run; independent checking must override it.
    _finished_run(tmp_path, CLEAN_RESULT)
    _stub_verdicts(
        monkeypatch,
        [Verdict(1, "https://x.test/a", "q", VerdictKind.CONTRADICTED, "not on page")],
    )
    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "CONTRADICTED" in printed.upper()
    assert "https://x.test/a" in printed


def test_collect_fails_when_a_source_cannot_be_fetched(tmp_path, capsys, monkeypatch):
    _finished_run(tmp_path, CLEAN_RESULT)
    _stub_verdicts(
        monkeypatch,
        [Verdict(1, "https://x.test/a", "q", VerdictKind.UNVERIFIABLE, "HTTP 403")],
    )
    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    assert rc == 1
    assert "403" in capsys.readouterr().out


def test_no_verify_skips_the_network_entirely(tmp_path, capsys, monkeypatch):
    _finished_run(tmp_path, CLEAN_RESULT)

    def exploding(sources, **kw):
        raise AssertionError("--no-verify must not fetch anything")

    monkeypatch.setattr(dr_main, "verify_sources", exploding)
    rc = main(["collect", "r1", "--runs-root", str(tmp_path), "--no-verify"])
    assert rc == 0
    assert "not verified" in capsys.readouterr().out.lower()


def test_collect_fails_when_the_run_claims_sources_but_lists_none_to_check(
    tmp_path, capsys, monkeypatch
):
    # sources_total > 0 with no structured `sources` array means the agent broke the
    # output contract, so its "verified" count cannot be checked and is worthless.
    result = dict(CLEAN_RESULT)
    result.pop("sources")
    _finished_run(tmp_path, result)
    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    assert rc == 1
    assert "cannot be verified" in capsys.readouterr().out.lower()


def test_runner_prompt_demands_a_structured_sources_array_and_warns_it_is_checked():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    # The verifier reads this array; without it collect cannot check anything.
    assert '"n": 1' in prompt and '"url"' in prompt and '"quote"' in prompt
    # Telling the agent the quotes are refetched is itself a quality lever.
    assert "REFETCHED AND CHECKED AGAINST THE LIVE PAGE" in prompt
    assert "CONTRADICTED" in prompt


from deep_research.verify import longest_common_span


def test_longest_common_span_finds_the_matching_tail_after_an_early_divergence():
    # The real failure mode observed: an agent inserted a colon the page does not have,
    # so the quote diverges at character 6 but the remaining ~100 match exactly.
    page = normalize('<p>Symbol"W": from Wolfram, originally from Middle High German</p>')
    quote = normalize('Symbol: "W": from Wolfram, originally from Middle High German')
    span = longest_common_span(quote, page)
    assert span.strip().startswith('"W": from Wolfram')
    assert len(span) > len(quote) * 0.8


def test_longest_common_span_is_tiny_for_an_invented_quote():
    page = normalize("<p>The capital is Lisbon and the weather is mild.</p>")
    quote = normalize("Quarterly revenue grew forty three percent year over year")
    assert len(longest_common_span(quote, page)) < 12


def test_contradicted_verdict_reports_how_much_actually_matched():
    page = '<p>Symbol"W": from Wolfram, originally from Middle High German</p>'
    sources = [{"n": 1, "url": "https://x.test/a", "quote": 'Symbol: "W": from Wolfram, originally from Middle High German'}]
    v = verify_sources(sources, fetcher=_fetcher({"https://x.test/a": (200, page)}))[0]
    assert v.kind is VerdictKind.CONTRADICTED
    assert "matched" in v.detail
    assert "longest span actually on the page" in v.detail


def test_normalize_drops_soft_hyphens_and_zero_width_characters():
    # Wikipedia really ships "pro\xadduct"; leaving it in fails a legitimate quote.
    assert "product catalogue" in normalize("<p>pro\xadduct cata​logue</p>")


def test_normalize_separates_adjacent_block_elements():
    # Without a block boundary space this reads as "foobar" and a quote spanning two
    # paragraphs would falsely come back CONTRADICTED.
    assert "foo bar" in normalize("<p>foo</p><p>bar</p>")


def test_normalize_does_not_double_unescape_entities():
    # convert_charrefs already decoded once; decoding again would turn the page's
    # literal "&lt;" text into markup the reader never saw.
    assert normalize("<p>a &amp;lt; b</p>") == "a &lt; b"


def test_a_quote_too_short_to_be_evidence_is_unverifiable_not_verified():
    page = "<html><body><p>The capital is Lisbon.</p></body></html>"
    v = verify_sources(
        [{"n": 1, "url": "https://x.test/a", "quote": "the"}],
        fetcher=_fetcher({"https://x.test/a": (200, page)}),
    )[0]
    assert v.kind is VerdictKind.UNVERIFIABLE
    assert "too short" in v.detail


def test_a_short_but_real_fact_still_verifies():
    # The guard must not reject legitimately terse citations like an infobox field.
    page = "<html><body><p>Capital: Lisbon</p></body></html>"
    v = verify_sources(
        [{"n": 1, "url": "https://x.test/a", "quote": "Capital: Lisbon"}],
        fetcher=_fetcher({"https://x.test/a": (200, page)}),
    )[0]
    assert v.kind is VerdictKind.VERIFIED


def test_footnote_markers_do_not_break_an_otherwise_verbatim_quote():
    # Wikipedia's shape: "Lisbon<sup>[1]</sup> is the capital". The marker is not prose.
    page = "<html><body><p>Lisbon<sup>[1]</sup> is the capital of Portugal.</p></body></html>"
    v = verify_sources(
        [{"n": 1, "url": "https://x.test/a", "quote": "Lisbon is the capital of Portugal."}],
        fetcher=_fetcher({"https://x.test/a": (200, page)}),
    )[0]
    assert v.kind is VerdictKind.VERIFIED


def test_a_pdf_is_unverifiable_not_contradicted():
    # Accusing a PDF citation of being wrong because we cannot parse it would be a lie.
    def pdf_fetcher(url, timeout=None):
        return (200, "%PDF-1.7 binary garbage", "application/pdf", False)

    v = verify_sources(
        [{"n": 1, "url": "https://x.test/paper.pdf", "quote": "a quote long enough to check"}],
        fetcher=pdf_fetcher,
    )[0]
    assert v.kind is VerdictKind.UNVERIFIABLE
    assert "application/pdf" in v.detail


def test_a_truncated_page_is_unverifiable_not_contradicted():
    def truncating(url, timeout=None):
        return (200, "<p>only the first chunk</p>", "text/html", True)

    v = verify_sources(
        [{"n": 1, "url": "https://x.test/big", "quote": "a quote from deep in the page"}],
        fetcher=truncating,
    )[0]
    assert v.kind is VerdictKind.UNVERIFIABLE
    assert "read limit" in v.detail


def test_collect_fails_when_the_run_lists_fewer_sources_than_it_claims(
    tmp_path, capsys, monkeypatch
):
    # Under-listing is the obvious evasion: cite twenty, list one, collect clean.
    result = dict(CLEAN_RESULT)
    result["sources_total"] = 20
    _finished_run(tmp_path, result)
    _stub_verdicts(
        monkeypatch,
        [Verdict(1, "https://x.test/a", "q", VerdictKind.VERIFIED, "quote found")],
    )
    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "listed only 1" in printed


def test_fetch_really_talks_to_an_http_server(tmp_path):
    # The only test that exercises the real fetch path: charset, content type and
    # status all come from an actual server rather than a stub.
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/missing":
                self.send_error(404)
                return
            body = "<html><body><p>Café résumé on the page.</p></body></html>"
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        from deep_research.verify import fetch as real_fetch

        got = real_fetch(f"{base}/page")
        assert got.status == 200
        assert got.content_type == "text/html"
        assert not got.truncated
        assert "Café résumé" in got.body

        # A real end-to-end verdict over real HTTP, no stub anywhere.
        v = verify_sources(
            [{"n": 1, "url": f"{base}/page", "quote": "Café résumé on the page."}]
        )[0]
        assert v.kind is VerdictKind.VERIFIED

        assert real_fetch(f"{base}/missing").status == 404
        missing = verify_sources(
            [{"n": 1, "url": f"{base}/missing", "quote": "a quote long enough to check"}]
        )[0]
        assert missing.kind is VerdictKind.UNVERIFIABLE
        assert "404" in missing.detail
    finally:
        server.shutdown()


def test_sup_drops_footnote_markers_but_keeps_exponents_and_ordinals():
    # Dropping every <sup> would turn "10^6 square metres" into "10 square metres" and
    # VERIFY a quote wrong by a factor of a million.
    assert normalize("<p>Lisbon<sup>[1]</sup> is the capital.</p>") == "Lisbon is the capital."
    # Multiple markers and lettered notes are the same thing, not prose.
    assert normalize("<p>Lisbon<sup>[1][2]</sup> is the capital.</p>") == "Lisbon is the capital."
    assert normalize("<p>Lisbon<sup>[a]</sup> is the capital.</p>") == "Lisbon is the capital."
    assert "10" + "6" in normalize("<p>The area is 10<sup>6</sup> square metres.</p>")
    assert "4th quarter" in normalize("<p>The 4<sup>th</sup> quarter.</p>")


def test_an_exponent_quote_is_not_falsely_verified_against_the_stripped_number():
    page = "<html><body><p>The area is 10<sup>6</sup> square metres exactly.</p></body></html>"
    v = verify_sources(
        [{"n": 1, "url": "https://x.test/a", "quote": "The area is 10 square metres exactly."}],
        fetcher=_fetcher({"https://x.test/a": (200, page)}),
    )[0]
    assert v.kind is VerdictKind.CONTRADICTED

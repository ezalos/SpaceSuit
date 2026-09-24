# ABOUTME: Tests for run records: round trip, discovery that ignores v1 manifests, updates.
# ABOUTME: Everything under tmp_path; no real runs root is ever read.
import json
from datetime import datetime

from deep_research_web.runs import (
    RunRecord, RunStatus, chat_url, find_run, find_runs, list_all, make_run_id, read_run,
    running_runs, slugify, update_run, write_run,
)


def _rec(out_dir, **over):
    base = dict(
        run_id="2026-09-16-120000-vector-db", question="Which vector db?",
        status=RunStatus.RUNNING.value, org_uuid="org-1", conversation_uuid="conv-1",
        chat_url=chat_url("conv-1"), model="claude-fable-5-1",
        charter=str(out_dir / "charter.md"), out_dir=str(out_dir),
        started_at="2026-09-16T12:00:00+00:00",
    )
    base.update(over)
    return RunRecord(**base)


def test_round_trip_and_defaults(tmp_path):
    rec = _rec(tmp_path)
    write_run(tmp_path, rec)
    back = read_run(tmp_path)
    assert back == rec
    assert back.engine == "claude-web"
    assert back.poll_failures == 0 and back.task_id is None and back.reason is None
    assert json.loads((tmp_path / "run.json").read_text())["chat_url"] == "https://claude.ai/chat/conv-1"


def test_find_runs_skips_v1_manifests_and_corrupt_files(tmp_path):
    good = tmp_path / "a"; good.mkdir(); write_run(good, _rec(good))
    v1 = tmp_path / "b"; v1.mkdir()
    (v1 / "run.json").write_text(json.dumps({"run_id": "x", "bg_session_id": "s", "engine": "local"}))
    bad = tmp_path / "c"; bad.mkdir(); (bad / "run.json").write_text("{not json")
    assert [r.run_id for r in find_runs(tmp_path)] == ["2026-09-16-120000-vector-db"]
    assert find_run(tmp_path, "2026-09-16-120000-vector-db").out_dir == str(good)
    assert find_run(tmp_path, "nope") is None


def test_list_all_covers_both_engines(tmp_path):
    web = tmp_path / "a"; web.mkdir(); write_run(web, _rec(web, run_id="a"))
    v1 = tmp_path / "b"; v1.mkdir()
    (v1 / "run.json").write_text(json.dumps({
        "run_id": "b", "bg_session_id": "sess-1", "engine": "claude-code-bg",
        "model": "claude-fable-5-1", "effort": "high", "charter": str(v1 / "charter.md"),
        "out_dir": str(v1), "started_at": "2026-09-16T12:00:00+00:00", "status": "running",
    }))
    rows = {r["run_id"]: r for r in list_all(tmp_path)}
    assert rows["a"] == {"run_id": "a", "engine": "claude-web", "status": "running", "link": chat_url("conv-1")}
    assert rows["b"] == {"run_id": "b", "engine": "claude-code-bg", "status": "running", "link": "sess-1"}


def test_running_runs_filters_on_status(tmp_path):
    a = tmp_path / "a"; a.mkdir(); write_run(a, _rec(a, run_id="a"))
    b = tmp_path / "b"; b.mkdir(); write_run(b, _rec(b, run_id="b", status="done"))
    assert [r.run_id for r in running_runs(tmp_path)] == ["a"]


def test_update_run_replaces_fields_atomically(tmp_path):
    write_run(tmp_path, _rec(tmp_path))
    new = update_run(tmp_path, status=RunStatus.DONE.value, collected_at="t", poll_failures=3)
    assert (new.status, new.collected_at, new.poll_failures) == ("done", "t", 3)
    assert read_run(tmp_path) == new
    assert not (tmp_path / "run.json.tmp").exists()


def test_run_id_and_slug():
    assert slugify("Which Vector DB?! ") == "which-vector-db"
    assert slugify("???") == "untitled"
    assert make_run_id("Which vector DB?", datetime(2026, 9, 16, 14, 15, 0)) == "2026-09-16-141500-which-vector-db"


def test_find_run_accepts_a_unique_prefix(tmp_path):
    # Run ids are ~58 characters; requiring the whole one is not a usable interface.
    d = tmp_path / "a"; d.mkdir()
    write_run(d, _rec(d, run_id="2026-09-17-153236-how-is-openai-s-gpt-6-astra"))
    assert find_run(tmp_path, "2026-09-17").run_id == "2026-09-17-153236-how-is-openai-s-gpt-6-astra"
    assert find_run(tmp_path, "astra") is None  # a prefix, not a substring search


def test_find_run_prefers_an_exact_match_over_a_longer_prefix_match(tmp_path):
    short = tmp_path / "a"; short.mkdir(); write_run(short, _rec(short, run_id="2026-09-17-run"))
    long = tmp_path / "b"; long.mkdir(); write_run(long, _rec(long, run_id="2026-09-17-run-two"))
    assert find_run(tmp_path, "2026-09-17-run").out_dir == str(short)


def test_a_legacy_record_without_account_still_parses(tmp_path):
    d = tmp_path / "old"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({
        "run_id": "old", "question": "q", "status": "done", "org_uuid": "o", "conversation_uuid": "c",
        "chat_url": "u", "model": "m", "charter": "c.md", "out_dir": str(d), "started_at": "t",
    }))
    assert read_run(d).account is None


def test_find_run_refuses_an_ambiguous_prefix(tmp_path):
    import pytest

    from deep_research_web import runs as runs_mod

    a = tmp_path / "a"; a.mkdir(); write_run(a, _rec(a, run_id="2026-09-17-alpha"))
    b = tmp_path / "b"; b.mkdir(); write_run(b, _rec(b, run_id="2026-09-17-beta"))
    with pytest.raises(runs_mod.AmbiguousRunId) as caught:
        find_run(tmp_path, "2026-09-17")
    assert "2026-09-17-alpha" in str(caught.value) and "2026-09-17-beta" in str(caught.value)

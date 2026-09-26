# ABOUTME: Tests for the CLI commands against the fake API and a temporary runs root.
# ABOUTME: Covers every exit code; the browser is never opened here.
import json
import os
import time
from pathlib import Path

import pytest
from conftest import FakeApi, research_done, research_started, citation, conversation, human, assistant, text_block, fetcher_stub

from deep_research_web import __main__ as cli
from deep_research_web.__main__ import (
    EXIT_LOGGED_OUT, EXIT_NEEDS_REPLY, EXIT_OK, EXIT_PREFLIGHT, EXIT_PROBLEM, EXIT_RUNNING,
    EXIT_USAGE, collect_record, launch_run, main,
)
from deep_research_web.accounts import UsageUnavailable
from deep_research_web.client import ApiError, ApiResponse, Client
from deep_research_web.config import Config
from deep_research_web.profiles import live_name
from deep_research_web.runs import RunStatus, find_runs, read_run, update_run, write_run, RunRecord
from deep_research_web.session import BrowserError, LoggedOut

CHARTER = """# Which vector database for 10M embeddings?

## Decision this feeds
Whether to migrate off pgvector this quarter.

## Must answer
- Cost at 10M vectors
- p99 latency under load

## Source bar
tier: vendor docs and independent benchmarks
recency: 2025-01-01 onwards

## Deliverable
A comparison table and a recommendation.

## Out of scope
- Graph databases
"""

ORGS = [{"uuid": "o", "name": "max", "capabilities": ["chat", "claude_max"], "active_flags": []}]


def _cfg(runs_root):
    return Config("claude-fable-5-1", "Deep research", runs_root, runs_root / "profile", runs_root / "profiles")


def _routes(conversation_json, orgs=ORGS, completion=ApiResponse(200, "event: message_stop\r\n\r\n"), projects=None):
    if projects is None:
        projects = [{"uuid": "p1", "name": "Deep research"}]
    return {
        ("GET", "/api/organizations"): ApiResponse(200, json.dumps(orgs)),
        ("GET", "/api/organizations/o/model_configs/claude-fable-5-1"): ApiResponse(200, "{}"),
        ("GET", "/api/organizations/o/projects"): ApiResponse(200, json.dumps(projects)),
        ("PUT", None): None,
        ("POST", None): None,
        "completion": completion,
        "conversation": conversation_json,
    }


class RoutingApi(FakeApi):
    """FakeApi whose conversation-scoped paths do not depend on the client-generated uuid."""

    def __init__(self, routes):
        super().__init__({k: v for k, v in routes.items() if isinstance(k, tuple) and k[1]})
        self.completion = routes["completion"]
        self.conversation_json = routes["conversation"]

    def __call__(self, method, path, body=None, stream=False, timeout_ms=None):
        base = path.split("?")[0]
        if base.endswith("/completion"):
            self.calls.append((method, path, body, stream))
            return self.completion
        if base.endswith("/stop_response"):
            self.calls.append((method, path, body, stream))
            return ApiResponse(200, "")
        if method == "PUT" and "/chat_conversations/" in base:
            self.calls.append((method, path, body, stream))
            return ApiResponse(202, "")
        if method == "GET" and "/chat_conversations/" in base:
            self.calls.append((method, path, body, stream))
            conv = self.conversation_json() if callable(self.conversation_json) else self.conversation_json
            return ApiResponse(200, json.dumps(conv))
        return super().__call__(method, path, body, stream, timeout_ms)


def _charter(tmp_path):
    p = tmp_path / "charter.md"
    p.write_text(CHARTER)
    return p


def test_launch_happy_path_writes_the_record_and_renames(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", "Deep research", False, sleep=lambda s: None)
    assert code == EXIT_OK
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.RUNNING.value and rec.task_id == "wf-123" and rec.project_uuid == "p1"
    assert rec.chat_url == f"https://claude.ai/chat/{rec.conversation_uuid}"
    assert (Path(rec.out_dir) / "charter.md").read_text() == CHARTER
    posted = next(c for c in api.calls if c[0] == "POST")
    assert posted[2]["create_conversation_params"]["compass_mode"] == "advanced"
    assert posted[2]["create_conversation_params"]["project_uuid"] == "p1"
    renamed = next(c for c in api.calls if c[0] == "PUT")
    assert renamed[2] == {"name": "Which vector database for 10M embeddings?"}
    out = capsys.readouterr().out
    assert rec.chat_url in out and f"collect {rec.run_id}" in out


def test_launch_refuses_on_account_flags(runs_root, tmp_path, capsys):
    orgs = [{"uuid": "o", "capabilities": ["chat"], "active_flags": [{"type": "consumer_first_warning", "expires_at": "2026-09-20"}]}]
    api = RoutingApi(_routes(research_started(), orgs=orgs))
    assert launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None) == EXIT_PREFLIGHT
    assert not any(c[0] == "POST" for c in api.calls)
    assert "consumer_first_warning" in capsys.readouterr().out


def _running(runs_root, run_id, account=None):
    d = runs_root / run_id; d.mkdir()
    write_run(d, RunRecord(run_id, "q", "running", "o", "c", "u", "m", "c.md", str(d), "2026-09-16T00:00:00+00:00",
                           account=account))


def test_the_cap_is_per_account_and_counts_max_per_account_runs(runs_root, tmp_path, capsys):
    # Was one run on the whole seat. Measured 2026-09-25: one account runs two Research tasks at once.
    api = RoutingApi(_routes(research_started()))
    cfg = _cfg(runs_root)
    launch = lambda force=False, account="a": launch_run(Client(api), cfg, _charter(tmp_path), "claude-fable-5-1",
                                                         None, force, sleep=lambda s: None, account=account)
    _running(runs_root, "one-on-a", "a")
    _running(runs_root, "one-on-b", "b")
    _running(runs_root, "two-on-b", "b")
    assert launch() == EXIT_OK, "a holds one of its two slots: a second run starts"
    assert launch() == EXIT_PREFLIGHT, "a now holds two"
    assert "a run is already in flight: account a holds 2 of its 2" in capsys.readouterr().out
    assert launch(force=True) == EXIT_OK, "--force still passes"
    assert launch(account="c") == EXIT_OK, "b's runs never fill c"


def test_a_run_recorded_without_an_account_counts_against_the_live_one(runs_root, tmp_path):
    _running(runs_root, "legacy-1")
    _running(runs_root, "legacy-2")
    api = RoutingApi(_routes(research_started()))
    assert launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False,
                      sleep=lambda s: None, account=None) == EXIT_PREFLIGHT


_REFUSED_EXHAUSTED = ('{"type":"error","error":{"type":"exceeded_limit",'
                      '"message":"{\\"type\\":\\"exceeded_limit\\",\\"resetsAt\\":1790010000}"}}')


def test_launch_reports_an_exhausted_usage_window(runs_root, tmp_path, capsys):
    # The real refusal nests its type and reset inside a JSON string in error.message.
    api = RoutingApi(_routes(research_started(), completion=ApiResponse(429, _REFUSED_EXHAUSTED)))
    assert launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None) == EXIT_USAGE
    out = capsys.readouterr().out
    assert "usage window" in out
    assert "exceeded_limit" in out


def test_launch_does_not_call_a_rate_limited_429_an_exhausted_window(runs_root, tmp_path, capsys):
    # A short-term rate limit clears in minutes, so it is not a usage window and must not be
    # reported as one: that sent a launch extra usage already covered into a three-day wait,
    # with the body that said "rate_limit" thrown away unread.
    api = RoutingApi(_routes(research_started(), completion=ApiResponse(429, '{"error":{"type":"rate_limit_error"}}')))
    assert launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None) == EXIT_PROBLEM
    out = capsys.readouterr().out
    assert "rate_limit_error" in out
    assert "usage window" not in out


_EXCEEDED = ('event: message_limit\r\ndata: {"type":"message_limit","message_limit":'
            '{"type":"exceeded_limit","resetsAt":1790010000}}\r\n\r\nevent: message_stop\r\n\r\n')


def test_launch_keeps_a_run_that_started_despite_an_exhausted_window(runs_root, tmp_path, capsys):
    # A 200 carrying a message_limit "exceeded" while research still engaged: extra usage
    # covers it, so the run is real and must NOT be discarded. This is the orphan bug.
    api = RoutingApi(_routes(research_started(), completion=ApiResponse(200, _EXCEEDED)))
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    assert code == EXIT_OK
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.RUNNING.value and rec.task_id == "wf-123"
    out = capsys.readouterr().out
    assert "extra usage is covering" in out and rec.chat_url in out


def test_launch_reports_usage_block_when_nothing_started(runs_root, tmp_path, capsys):
    # A 200 that exceeded the window AND started no research task: the cap really blocked it.
    # A run marked failed is never polled again, so the response is stopped here: a late
    # engagement would otherwise research unseen and bill extra usage.
    plain = conversation([human("q", "h1")], leaf="h1")
    api = RoutingApi(_routes(plain, completion=ApiResponse(200, _EXCEEDED)))
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    assert code == EXIT_USAGE
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.FAILED.value and "usage window exhausted" in (rec.reason or "")
    assert any(c[1].endswith("/stop_response") for c in api.calls)


def test_launch_stop_after_usage_block_is_best_effort(runs_root, tmp_path, capsys):
    # A dead stop_response call must not turn the usage block into an unhandled crash.
    plain = conversation([human("q", "h1")], leaf="h1")

    class DeadStopApi(RoutingApi):
        def __call__(self, method, path, body=None, stream=False, timeout_ms=None):
            if path.split("?")[0].endswith("/stop_response"):
                self.calls.append((method, path, body, stream))
                return ApiResponse(500, "boom")
            return super().__call__(method, path, body, stream, timeout_ms)

    api = DeadStopApi(_routes(plain, completion=ApiResponse(200, _EXCEEDED)))
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    assert code == EXIT_USAGE
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.FAILED.value
    assert "could not stop" in capsys.readouterr().out


def test_launch_reports_a_turn_that_never_started_research_as_needs_reply(runs_root, tmp_path, capsys):
    plain = conversation([human("q", "h1"), assistant([text_block("quick answer")], "a1", "h1", stop_reason="end_turn")], leaf="a1")
    api = RoutingApi(_routes(plain))
    slept = []
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=slept.append)
    assert code == EXIT_NEEDS_REPLY
    assert len(slept) == 3  # four reads, three waits between them
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.NEEDS_REPLY.value
    assert "waiting on you" in capsys.readouterr().out


class FlakyConversationApi(RoutingApi):
    """A conversation GET that always fails, as if the network dropped mid-poll."""

    def __call__(self, method, path, body=None, stream=False, timeout_ms=None):
        base = path.split("?")[0]
        if method == "GET" and "/chat_conversations/" in base:
            self.calls.append((method, path, body, stream))
            return ApiResponse(500, "boom")
        return super().__call__(method, path, body, stream, timeout_ms)


def test_launch_survives_an_engagement_check_error(runs_root, tmp_path, capsys):
    api = FlakyConversationApi(_routes(research_started()))
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    assert code == EXIT_OK
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.RUNNING.value and rec.task_id is None
    out = capsys.readouterr().out
    assert rec.chat_url in out
    assert "engagement check could not read the conversation" in out


def test_launch_run_does_not_collide_when_relaunched_within_the_same_second(runs_root, tmp_path):
    # The switch-and-retry path launches the identical charter twice in quick succession,
    # exactly this shape (a failed run, then a normal one): make_run_id's second-level
    # resolution must not let the second overwrite the first on disk.
    cfg = _cfg(runs_root)
    charter = _charter(tmp_path)
    plain = conversation([human("q", "h1")], leaf="h1")
    api_a = RoutingApi(_routes(plain, completion=ApiResponse(200, _EXCEEDED)))
    api_b = RoutingApi(_routes(research_started()))
    assert launch_run(Client(api_a), cfg, charter, "claude-fable-5-1", None, False, sleep=lambda s: None) == EXIT_USAGE
    assert launch_run(Client(api_b), cfg, charter, "claude-fable-5-1", None, False, sleep=lambda s: None) == EXIT_OK
    recs = find_runs(runs_root)
    assert len({r.run_id for r in recs}) == 2
    assert {r.status for r in recs} == {RunStatus.FAILED.value, RunStatus.RUNNING.value}


class DeadApi(FlakyConversationApi):
    """Both the conversation GET and the rename PUT are dead, as if the API went away mid-launch."""

    def __call__(self, method, path, body=None, stream=False, timeout_ms=None):
        base = path.split("?")[0]
        if method == "PUT" and "/chat_conversations/" in base:
            self.calls.append((method, path, body, stream))
            return ApiResponse(500, "boom")
        return super().__call__(method, path, body, stream, timeout_ms)


def test_launch_prints_the_url_before_renaming(runs_root, tmp_path, capsys):
    # The research is running on claude.ai the moment the POST returns; a rename that
    # blows up must not cost Louis the only link to it.
    api = DeadApi(_routes(research_started()))
    with pytest.raises(ApiError):
        launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    assert rec.status == RunStatus.RUNNING.value
    assert rec.chat_url in capsys.readouterr().out


def test_launch_records_the_live_account(runs_root, tmp_path):
    _saved(runs_root, "work5")
    cfg = _cfg(runs_root)
    cli.cmd_switch(_SwitchArgs("work5"), cfg)
    api = RoutingApi(_routes(research_started()))
    assert launch_run(Client(api), cfg, _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None) == EXIT_OK
    [rec] = find_runs(runs_root)
    assert rec.account == "work5"


def test_launch_run_records_the_passed_account_over_the_live_link(runs_root, tmp_path):
    # cmd_launch resolves the account once and passes it in; launch_run must trust that
    # value rather than re-reading the live link, which may have moved on since.
    _saved(runs_root, "work5")
    cfg = _cfg(runs_root)
    cli.cmd_switch(_SwitchArgs("work5"), cfg)
    api = RoutingApi(_routes(research_started()))
    code = launch_run(Client(api), cfg, _charter(tmp_path), "claude-fable-5-1", None, False,
                       sleep=lambda s: None, account="other")
    assert code == EXIT_OK
    [rec] = find_runs(runs_root)
    assert rec.account == "other"


def test_launch_run_records_none_when_the_caller_passes_none(runs_root, tmp_path):
    _saved(runs_root, "work5")
    cfg = _cfg(runs_root)
    cli.cmd_switch(_SwitchArgs("work5"), cfg)
    api = RoutingApi(_routes(research_started()))
    code = launch_run(Client(api), cfg, _charter(tmp_path), "claude-fable-5-1", None, False,
                       sleep=lambda s: None, account=None)
    assert code == EXIT_OK
    [rec] = find_runs(runs_root)
    assert rec.account is None


class _LaunchArgs:
    def __init__(self, charter):
        self.charter, self.model, self.project, self.no_project, self.force = str(charter), None, None, False, False
        self.name = None


def _two_accounts(runs_root, monkeypatch, first_completion, second_completion, orgs=ORGS,
                  projects=None, conversation_a=None):
    """Profiles a and b saved, a live; a's API answers first_completion, b's second_completion."""
    _saved(runs_root, "a", "b")
    cfg = _cfg(runs_root)
    cli.cmd_switch(_SwitchArgs("a"), cfg)
    conv_a = research_started() if conversation_a is None else conversation_a
    apis = {"a": RoutingApi(_routes(conv_a, orgs=orgs, completion=first_completion, projects=projects)),
            "b": RoutingApi(_routes(research_started(), completion=second_completion))}
    opened = []

    def factory(profile, headless=False, create=False):
        name = live_name(cfg.profile, cfg.profiles) if profile == cfg.profile else profile.name
        opened.append(name)
        return _SessionWithApi(apis[name])

    monkeypatch.setattr(cli, "_open_session", factory)
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=100, b=10))
    # research_started() engages on the first read, so launch_run's engagement loop never sleeps.
    return cfg, opened, apis


def test_launch_switches_once_on_a_usage_refusal(runs_root, tmp_path, monkeypatch, capsys):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, ApiResponse(429, _REFUSED_EXHAUSTED),
                                       ApiResponse(200, "event: message_stop\r\n\r\n"))
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_OK
    assert opened == ["a", "b"]
    assert live_name(cfg.profile, cfg.profiles) == "b"
    [rec] = find_runs(runs_root)
    assert rec.account == "b"
    assert "retrying the launch on b" in capsys.readouterr().out


def test_launch_does_not_switch_twice(runs_root, tmp_path, monkeypatch):
    refused = ApiResponse(429, _REFUSED_EXHAUSTED)
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, refused, refused)
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_USAGE
    assert opened == ["a", "b"]


def test_launch_never_switches_on_a_rate_limit(runs_root, tmp_path, monkeypatch):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, ApiResponse(429, '{"error":{"type":"rate_limit_error"}}'),
                                       ApiResponse(200, "event: message_stop\r\n\r\n"))
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_PROBLEM
    assert opened == ["a"] and live_name(cfg.profile, cfg.profiles) == "a"


def test_launch_stays_put_when_no_account_has_room(runs_root, tmp_path, monkeypatch, capsys):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, ApiResponse(429, _REFUSED_EXHAUSTED),
                                       ApiResponse(200, "event: message_stop\r\n\r\n"))
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=100, b=99))
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_USAGE
    assert opened == ["a"] and "no saved profile has room" in capsys.readouterr().out


_FLAGGED_ORGS = [{"uuid": "o", "capabilities": ["chat"],
                  "active_flags": [{"type": "consumer_first_warning", "expires_at": "2026-09-20"}]}]


def test_launch_stays_on_a_flagged_account_without_switching(runs_root, tmp_path, monkeypatch, capsys):
    # A preflight refusal (account flag) is never a usage refusal: the switch must not fire.
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, ApiResponse(200, "event: message_stop\r\n\r\n"),
                                       ApiResponse(200, "event: message_stop\r\n\r\n"), orgs=_FLAGGED_ORGS)
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_PREFLIGHT
    assert opened == ["a"]
    assert live_name(cfg.profile, cfg.profiles) == "a"


def test_launch_stays_on_an_account_missing_the_project_without_switching(runs_root, tmp_path, monkeypatch, capsys):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, ApiResponse(200, "event: message_stop\r\n\r\n"),
                                       ApiResponse(200, "event: message_stop\r\n\r\n"), projects=[])
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_PREFLIGHT
    assert opened == ["a"]
    assert live_name(cfg.profile, cfg.profiles) == "a"


def test_launch_never_switches_when_the_pick_cannot_run(runs_root, tmp_path, monkeypatch, capsys):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, ApiResponse(429, _REFUSED_EXHAUSTED),
                                       ApiResponse(200, "event: message_stop\r\n\r\n"))

    def boom():
        raise UsageUnavailable("claude-usage did not run")

    monkeypatch.setattr(cli, "read_usage", boom)
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_USAGE
    assert opened == ["a"]
    assert live_name(cfg.profile, cfg.profiles) == "a"


def test_launch_switches_and_stops_the_response_on_the_200_stream_path(runs_root, tmp_path, monkeypatch, capsys):
    # account a: a 200 stream flags "exceeded" but the conversation never starts research.
    monkeypatch.setattr(cli, "ENGAGEMENT_WAIT_S", 0)
    plain = conversation([human("q", "h1")], leaf="h1")
    cfg, opened, apis = _two_accounts(runs_root, monkeypatch, ApiResponse(200, _EXCEEDED),
                                      ApiResponse(200, "event: message_stop\r\n\r\n"), conversation_a=plain)
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_OK
    assert opened == ["a", "b"]
    assert live_name(cfg.profile, cfg.profiles) == "b"
    recs = {r.account: r for r in find_runs(runs_root)}
    assert len(recs) == 2
    assert recs["a"].status == RunStatus.FAILED.value
    assert recs["b"].status == RunStatus.RUNNING.value
    assert any(c[1].endswith("/stop_response") for c in apis["a"].calls)


def _running_run(runs_root, run_id="x"):
    d = runs_root / run_id
    d.mkdir()
    write_run(d, RunRecord(run_id, "q?", RunStatus.RUNNING.value, "o", "c",
                           "https://claude.ai/chat/c", "m", str(d / "charter.md"), str(d),
                           "2026-09-16T12:00:00+00:00"))
    return d


def _env(monkeypatch, runs_root, tmp_path):
    monkeypatch.setenv("DEEP_RESEARCH_WEB_RUNS_ROOT", str(runs_root))
    monkeypatch.setenv("DEEP_RESEARCH_WEB_PROFILE", str(tmp_path / "p"))
    monkeypatch.setenv("DEEP_RESEARCH_WEB_PROFILES", str(tmp_path / "ps"))


def test_main_maps_browser_error_to_exit_one(runs_root, tmp_path, monkeypatch, capsys):
    _env(monkeypatch, runs_root, tmp_path)
    _running_run(runs_root)

    def boom(cfg, headless=True, create=False):
        raise BrowserError("browser: boom")

    monkeypatch.setattr(cli, "_open_session", boom)
    assert main(["list"]) == EXIT_OK  # list opens no browser
    assert main(["collect", "x"]) == EXIT_PROBLEM
    assert "browser: boom" in capsys.readouterr().out


def test_main_maps_logged_out_to_exit_two(runs_root, tmp_path, monkeypatch, capsys):
    _env(monkeypatch, runs_root, tmp_path)
    _running_run(runs_root)

    def boom(cfg, headless=True, create=False):
        raise LoggedOut("profile is not logged in to claude.ai; run: deep-research-web login")

    monkeypatch.setattr(cli, "_open_session", boom)
    assert main(["collect", "x"]) == EXIT_LOGGED_OUT
    assert "deep-research-web login" in capsys.readouterr().out


def test_stop_leaves_a_settled_run_alone(runs_root, tmp_path, monkeypatch, capsys):
    # A done run has nothing in flight: stopping it would only rewrite the record as failed.
    _env(monkeypatch, runs_root, tmp_path)
    d = _running_run(runs_root, "done-run")
    update_run(d, status=RunStatus.DONE.value)

    def boom(cfg, headless=True, create=False):
        raise AssertionError("stop must not open a browser for a settled run")

    monkeypatch.setattr(cli, "_open_session", boom)
    assert main(["stop", "done-run"]) == EXIT_OK
    assert "done-run is done; nothing to stop" in capsys.readouterr().out
    assert read_run(d).status == RunStatus.DONE.value


def test_collect_still_running(runs_root, tmp_path):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    code, counts = collect_record(Client(api), rec, fetcher=fetcher_stub({}))
    assert code == EXIT_RUNNING and counts == {}


def test_collect_done_writes_files_and_grades(runs_root, tmp_path, capsys):
    content = 'Alpha says "price is five dollars a month" today. Beta is fast.'
    cits = [citation("https://x.test/a", 0, 49), citation("https://x.test/b", 50, 63)]
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    api.conversation_json = research_done(content, cits)
    pages = {"https://x.test/a": (200, "<p>The price is five dollars a month, billed yearly.</p>"), "https://x.test/b": (200, "<p>Beta text</p>")}
    code, counts = collect_record(Client(api), rec, fetcher=fetcher_stub(pages))
    assert code == EXIT_OK
    assert counts["quoted"] == 1 and counts["live"] == 1
    out = Path(rec.out_dir)
    assert (out / "DONE").exists() and (out / "conversation.json").exists()
    assert (out / "report.md").read_text() == 'Alpha says "price is five dollars a month" today. [1] Beta is fast. [2]'
    assert read_run(out).status == RunStatus.DONE.value and read_run(out).collected_at
    result = json.loads((out / "run-result.json").read_text())
    assert result["sources_verified"] == 1
    assert "[2] https://x.test/b  live" in capsys.readouterr().out


def test_collect_dead_source_exits_one(runs_root, tmp_path):
    content = "Alpha is cheap."
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    api.conversation_json = research_done(content, [citation("https://x.test/a", 0, 15)])
    code, counts = collect_record(Client(api), rec, fetcher=fetcher_stub({"https://x.test/a": (404, "")}))
    assert code == EXIT_PROBLEM and counts["dead"] == 1


def test_collect_keeps_a_refusal_text_and_exits_one(runs_root, tmp_path):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    api.conversation_json = conversation([human("q", "h1"), assistant([text_block("I cannot help with that.")], "a1", "h1", stop_reason="end_turn")], leaf="a1")
    code, _ = collect_record(Client(api), rec, fetcher=fetcher_stub({}))
    assert code == EXIT_PROBLEM
    report = (Path(rec.out_dir) / "report.md").read_text()
    assert report.startswith("# NOT A RESEARCH REPORT") and "I cannot help with that." in report
    # A refusal is one of the three shapes that are indistinguishable in this JSON (question,
    # refusal, plain answer); all three leave Louis something to read and answer.
    assert read_run(Path(rec.out_dir)).status == RunStatus.NEEDS_REPLY.value


class _SessionOnAccount:
    """A logged-in profile that claude.ai says belongs to `account`, whatever the config names."""

    def __init__(self, account, logged_in=True):
        self._account = account
        self._logged_in = logged_in
        self.logins = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def logged_in(self, challenge_timeout_s=0):
        return self._logged_in

    def login(self, email, code_reader):
        self.logins.append(email)

    def api(self, method, path, **kw):
        if path == "/api/account":
            return ApiResponse(200, json.dumps({"email_address": self._account}))
        if path == "/api/organizations":
            return ApiResponse(200, json.dumps(ORGS))
        return ApiResponse(404, "{}")


class _LoginArgs:
    name = "acct"
    email = "me@example.test"
    headless = True


def test_login_reports_the_account_claude_ai_names_not_the_expected_one(runs_root, monkeypatch, capsys):
    # The profile holds another account's live session, so login short-circuits without
    # switching it. Printing the expected email here reported a switch that never happened.
    session = _SessionOnAccount("other@example.test")
    opened = []
    monkeypatch.setattr(cli, "_open_session", lambda p, headless=True, create=False: opened.append(p) or session)
    assert cli.cmd_login(_LoginArgs(), _cfg(runs_root)) == EXIT_PROBLEM
    out = capsys.readouterr().out
    assert "logged in as other@example.test" in out
    assert "me@example.test" in out
    assert "move the profile directory aside" in out
    assert opened == [runs_root / "profiles" / "acct"]
    assert session.logins == []


def test_login_confirms_a_matching_account(runs_root, monkeypatch, capsys):
    session = _SessionOnAccount("me@example.test")
    monkeypatch.setattr(cli, "_open_session", lambda p, headless=True, create=False: session)
    assert cli.cmd_login(_LoginArgs(), _cfg(runs_root)) == EXIT_OK
    out = capsys.readouterr().out
    assert "logged in as me@example.test" in out
    assert "NOT the expected account" not in out


def test_login_takes_the_email_from_claude_usage(runs_root, monkeypatch, capsys):
    session = _SessionOnAccount("acct@example.test", logged_in=False)
    monkeypatch.setattr(cli, "_open_session", lambda p, headless=True, create=False: session)
    monkeypatch.setattr(cli, "read_usage", lambda: [{"name": "acct", "email": "acct@example.test", "usage": {}}])
    args = _LoginArgs()
    args.email = None
    assert cli.cmd_login(args, _cfg(runs_root)) == EXIT_OK
    assert session.logins == ["acct@example.test"]


def test_login_without_an_email_anywhere_exits_one(runs_root, monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_usage", lambda: [])
    args = _LoginArgs()
    args.email = None
    assert cli.cmd_login(args, _cfg(runs_root)) == EXIT_PROBLEM
    assert "--email" in capsys.readouterr().out


def test_login_refuses_a_name_with_a_path_separator(runs_root, monkeypatch, capsys):
    def boom(*a, **kw):
        raise AssertionError("an invalid name must be refused before any session opens")

    monkeypatch.setattr(cli, "_open_session", boom)
    args = _LoginArgs()
    args.name = "../x"
    assert cli.cmd_login(args, _cfg(runs_root)) == EXIT_PROBLEM
    assert "invalid account name" in capsys.readouterr().out


def test_login_creates_the_profiles_directory_with_strict_permissions(runs_root, monkeypatch):
    import stat

    session = _SessionOnAccount("me@example.test")
    monkeypatch.setattr(cli, "_open_session", lambda p, headless=True, create=False: session)
    cfg = _cfg(runs_root)
    assert not cfg.profiles.exists()
    assert cli.cmd_login(_LoginArgs(), cfg) == EXIT_OK
    assert stat.S_IMODE(cfg.profiles.stat().st_mode) == 0o700


def _usage(**weekly):
    return [{"name": n, "email": f"{n}@example.test", "provider": "anthropic",
             "usage": {"at": int(time.time() * 1000), "session": {"percent": 1},
                      "weekly": {"percent": w}, "scoped": []}}
            for n, w in weekly.items()]


def _saved(runs_root, *names):
    for n in names:
        (runs_root / "profiles" / n).mkdir(parents=True)
        (runs_root / "profiles" / n).chmod(0o700)


class _SwitchArgs:
    def __init__(self, name=None):
        self.name = name


def test_switch_by_name_moves_the_live_link(runs_root, capsys):
    _saved(runs_root, "a", "b")
    cfg = _cfg(runs_root)
    assert cli.cmd_switch(_SwitchArgs("a"), cfg) == EXIT_OK
    assert cli.cmd_switch(_SwitchArgs("b"), cfg) == EXIT_OK
    assert os.readlink(cfg.profile) == "profiles/b"
    assert "a -> b" in capsys.readouterr().out


def test_switch_without_a_name_takes_the_pick(runs_root, monkeypatch, capsys):
    _saved(runs_root, "a", "b")
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=90, b=20))
    assert cli.cmd_switch(_SwitchArgs(), _cfg(runs_root)) == EXIT_OK
    out = capsys.readouterr().out
    assert "-> b" in out and "dropped: a window is at or above 85%" in out


def test_switch_without_room_anywhere_changes_nothing(runs_root, monkeypatch, capsys):
    _saved(runs_root, "a")
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=99))
    assert cli.cmd_switch(_SwitchArgs(), _cfg(runs_root)) == EXIT_PROBLEM
    assert not _cfg(runs_root).profile.is_symlink()
    assert "no saved profile has room" in capsys.readouterr().out


def test_switch_to_a_missing_profile_names_login(runs_root, capsys):
    assert cli.cmd_switch(_SwitchArgs("ghost"), _cfg(runs_root)) == EXIT_PROBLEM
    assert "deep-research-web login ghost" in capsys.readouterr().out


def test_switch_refuses_a_name_with_a_path_separator(runs_root, capsys):
    assert cli.cmd_switch(_SwitchArgs("../x"), _cfg(runs_root)) == EXIT_PROBLEM
    assert "invalid account name" in capsys.readouterr().out


def test_pick_reports_malformed_json_instead_of_crashing(runs_root, monkeypatch, capsys):
    # A row that is not a dict (claude-usage printed valid JSON in an unexpected shape)
    # must be reported, not let an AttributeError escape out of the CLI.
    monkeypatch.setattr(cli, "read_usage", lambda: ["not-a-dict"])
    assert cli._pick_next(_cfg(runs_root), None) is None
    assert "the pick could not run" in capsys.readouterr().out


def test_profiles_lists_every_saved_profile_with_its_meters(runs_root, monkeypatch, capsys):
    _saved(runs_root, "a", "b")
    cli.cmd_switch(_SwitchArgs("a"), _cfg(runs_root))
    capsys.readouterr()
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=10, b=20))
    assert cli.cmd_profiles(None, _cfg(runs_root)) == EXIT_OK
    out = capsys.readouterr().out
    # The table replaced the per-account prose lines this test first asserted (2026-09-21).
    assert "live: a" in out and "20%" in out and "live now" in out
    assert "switch would pick: b" in out


def test_profiles_lists_saved_profiles_even_when_claude_usage_fails(runs_root, monkeypatch, capsys):
    _saved(runs_root, "a", "b")
    cli.cmd_switch(_SwitchArgs("a"), _cfg(runs_root))
    capsys.readouterr()

    def boom():
        raise UsageUnavailable("claude-usage did not run")

    monkeypatch.setattr(cli, "read_usage", boom)
    assert cli.cmd_profiles(None, _cfg(runs_root)) == EXIT_OK
    out = capsys.readouterr().out
    assert "live: a" in out and "a" in out and "b" in out
    assert "the pick could not run" in out


class _SessionWithApi:
    """A logged-in session whose api is a routed fake: no browser, no network."""

    def __init__(self, api):
        self.api = api

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def require_login(self):
        pass


def test_collect_opens_the_profile_of_the_run_account(runs_root, tmp_path, monkeypatch):
    d = _running_run(runs_root)  # existing helper: run id "x", status running
    update_run(d, account="perso")
    opened = []
    api = RoutingApi(_routes(research_started()))
    monkeypatch.setattr(cli, "_open_session", lambda p, headless=False, create=False: opened.append(p) or _SessionWithApi(api))
    cli.cmd_collect(type("A", (), {"run_id": "x", "no_verify": True})(), _cfg(runs_root))
    assert opened == [runs_root / "profiles" / "perso"]


def _needs_reply_conversation():
    return conversation([
        human("q", "h1"),
        assistant([text_block("Which Astra do you mean?")], "a1", "h1", stop_reason="end_turn"),
    ], leaf="a1")


class _StatusArgs:
    run_id = None


def test_status_persists_a_needs_reply_run_so_it_stops_aging_into_stale(runs_root, monkeypatch, capsys):
    d = _running_run(runs_root)
    api = RoutingApi(_routes(_needs_reply_conversation()))
    monkeypatch.setattr(cli, "_open_session", lambda cfg, headless=True, create=False: _SessionWithApi(api))
    assert cli.cmd_status(_StatusArgs(), _cfg(runs_root)) == EXIT_OK
    assert "needs-reply" in capsys.readouterr().out
    assert read_run(d).status == RunStatus.NEEDS_REPLY.value


def test_status_leaves_a_finished_run_for_the_watcher_to_collect(runs_root, monkeypatch, capsys):
    # The watcher only looks at runs marked running, and it owns DONE: it collects the report
    # and sends the Telegram. Persisting done here would silently steal both.
    d = _running_run(runs_root)
    api = RoutingApi(_routes(research_done("# R", [citation("https://x.test/a", 0, 3)])))
    monkeypatch.setattr(cli, "_open_session", lambda cfg, headless=True, create=False: _SessionWithApi(api))
    assert cli.cmd_status(_StatusArgs(), _cfg(runs_root)) == EXIT_OK
    assert read_run(d).status == RunStatus.RUNNING.value


class _ReportArgs:
    def __init__(self, run_id="x", out=None):
        self.run_id = run_id
        self.out = out


def test_report_prints_the_collected_report_to_stdout(runs_root, capsys):
    # Exact equality: the content must be the only thing on stdout, or `report x > f.md` lies.
    d = _running_run(runs_root)
    (d / "report.md").write_text("# Astra\n\nbody\n", encoding="utf-8")
    assert cli.cmd_report(_ReportArgs(), _cfg(runs_root)) == EXIT_OK
    assert capsys.readouterr().out == "# Astra\n\nbody\n"


def test_report_writes_the_report_to_an_out_path(runs_root, tmp_path, capsys):
    d = _running_run(runs_root)
    (d / "report.md").write_text("# Astra\n", encoding="utf-8")
    dest = tmp_path / "exported.md"
    assert cli.cmd_report(_ReportArgs(out=str(dest)), _cfg(runs_root)) == EXIT_OK
    assert dest.read_text(encoding="utf-8") == "# Astra\n"
    assert str(dest) in capsys.readouterr().out


def test_report_on_a_running_run_says_it_is_not_ready(runs_root, capsys):
    _running_run(runs_root)
    assert cli.cmd_report(_ReportArgs(), _cfg(runs_root)) == EXIT_RUNNING
    assert "still running" in capsys.readouterr().out


def test_report_on_an_uncollected_settled_run_names_the_collect_command(runs_root, capsys):
    d = _running_run(runs_root)
    update_run(d, status=RunStatus.STALE.value)
    assert cli.cmd_report(_ReportArgs(), _cfg(runs_root)) == EXIT_PROBLEM
    assert "collect x" in capsys.readouterr().out


def test_report_resolves_a_run_id_prefix(runs_root, capsys):
    d = _running_run(runs_root, run_id="2026-09-17-153236-how-is-openai-s-gpt-6-astra")
    (d / "report.md").write_text("# Astra\n", encoding="utf-8")
    assert cli.cmd_report(_ReportArgs(run_id="2026-09-17-15"), _cfg(runs_root)) == EXIT_OK
    assert capsys.readouterr().out == "# Astra\n"


# --- archive on collect, launch --name, the archive subcommand ---

from argparse import Namespace
from dataclasses import replace as _replace


def _archiving_cfg(runs_root, lib):
    return _replace(_cfg(runs_root), archive_root=lib)


def test_collect_archives_the_run_when_an_archive_root_is_configured(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None, name="vector-db-choice")
    [rec] = find_runs(runs_root)
    api.conversation_json = research_done("Alpha is cheap.", [])
    lib = tmp_path / "lib"
    code, _ = collect_record(Client(api), rec, fetcher=fetcher_stub({}), archive_root=lib)
    assert code == EXIT_OK
    dest = lib / f"{rec.run_id[:10]}-vector-db-choice"
    assert (dest / "report.md").read_text() == "Alpha is cheap."
    assert (dest / "archive.json").exists() and (lib / "README.md").exists()
    out = capsys.readouterr().out
    assert f"archived: {dest}" in out and f"check-claims {rec.run_id}" in out


def test_collect_survives_an_archive_failure(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    api.conversation_json = research_done("Alpha is cheap.", [])
    not_a_dir = tmp_path / "lib"
    not_a_dir.write_text("in the way")
    code, _ = collect_record(Client(api), rec, fetcher=fetcher_stub({}), archive_root=not_a_dir)
    assert code == EXIT_OK and read_run(Path(rec.out_dir)).status == RunStatus.DONE.value
    assert "archive failed" in capsys.readouterr().out


def test_collect_archives_nothing_without_an_archive_root(runs_root, tmp_path):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    api.conversation_json = research_done("Alpha is cheap.", [])
    collect_record(Client(api), rec, fetcher=fetcher_stub({}))
    assert not (tmp_path / "lib").exists() and sorted(p.name for p in runs_root.iterdir()) == [rec.run_id]


def test_the_watcher_collect_uses_the_configured_archive_root(runs_root, tmp_path):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None, name="watched")
    [rec] = find_runs(runs_root)
    lib = tmp_path / "lib"
    code, _ = cli.watcher_collect(_archiving_cfg(runs_root, lib))(rec, research_done("Alpha is cheap.", []))
    assert code == EXIT_OK and (lib / f"{rec.run_id[:10]}-watched" / "report.md").exists()


def test_launch_records_the_explicit_name(runs_root, tmp_path):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None, name="vector-db-choice")
    [rec] = find_runs(runs_root)
    assert rec.name == "vector-db-choice"


def test_launch_refuses_a_bad_name_before_touching_the_api(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    code = launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None, name="Vector DB")
    assert code == EXIT_PROBLEM and find_runs(runs_root) == [] and api.calls == []
    assert "kebab" in capsys.readouterr().out


def test_launch_parser_accepts_name():
    args = cli.build_parser().parse_args(["launch", "--charter", "c.md", "--name", "vector-db-choice"])
    assert args.name == "vector-db-choice"
    assert cli.build_parser().parse_args(["launch", "--charter", "c.md"]).name is None


def test_archive_command_archives_and_renames_a_collected_run(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    api.conversation_json = research_done("Alpha is cheap.", [])
    collect_record(Client(api), rec, fetcher=fetcher_stub({}))
    lib = tmp_path / "lib"
    code = cli.cmd_archive(Namespace(run_id=rec.run_id[:17], name="vector-db-choice"), _archiving_cfg(runs_root, lib))
    assert code == EXIT_OK
    dest = lib / f"{rec.run_id[:10]}-vector-db-choice"
    assert (dest / "report.md").exists() and read_run(Path(rec.out_dir)).name == "vector-db-choice"
    assert str(dest) in capsys.readouterr().out


def test_archive_command_needs_an_archive_root(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    code = cli.cmd_archive(Namespace(run_id=rec.run_id, name=None), _cfg(runs_root))
    assert code == EXIT_PROBLEM and "DEEP_RESEARCH_WEB_ARCHIVE_ROOT" in capsys.readouterr().out


def test_archive_command_refuses_an_uncollected_run(runs_root, tmp_path, capsys):
    api = RoutingApi(_routes(research_started()))
    launch_run(Client(api), _cfg(runs_root), _charter(tmp_path), "claude-fable-5-1", None, False, sleep=lambda s: None)
    [rec] = find_runs(runs_root)
    code = cli.cmd_archive(Namespace(run_id=rec.run_id, name=None), _archiving_cfg(runs_root, tmp_path / "lib"))
    assert code == EXIT_PROBLEM and "not collected" in capsys.readouterr().out


def _many_accounts(runs_root, monkeypatch, completions, live):
    """One saved profile per name in `completions`, `live` made live, one fake API each."""
    _saved(runs_root, *completions)
    cfg = _cfg(runs_root)
    cli.cmd_switch(_SwitchArgs(live), cfg)
    apis = {n: RoutingApi(_routes(research_started(), completion=c)) for n, c in completions.items()}
    opened = []

    def factory(profile, headless=False, create=False):
        name = live_name(cfg.profile, cfg.profiles) if profile == cfg.profile else profile.name
        opened.append(name)
        return _SessionWithApi(apis[name])

    monkeypatch.setattr(cli, "_open_session", factory)
    return cfg, opened


def test_launch_walks_every_account_with_room_until_one_takes_it(runs_root, tmp_path, monkeypatch, capsys):
    refused, ok = ApiResponse(429, _REFUSED_EXHAUSTED), ApiResponse(200, "event: message_stop\r\n\r\n")
    cfg, opened = _many_accounts(runs_root, monkeypatch, {"a": refused, "b": refused, "c": ok}, live="a")
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=100, b=20, c=30))
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_OK
    assert opened == ["a", "b", "c"]
    assert live_name(cfg.profile, cfg.profiles) == "c"
    [rec] = find_runs(runs_root)
    assert rec.account == "c"
    out = capsys.readouterr().out
    assert "retrying the launch on b" in out and "retrying the launch on c" in out


def test_launch_never_asks_the_same_account_twice(runs_root, tmp_path, monkeypatch, capsys):
    refused = ApiResponse(429, _REFUSED_EXHAUSTED)
    cfg, opened = _many_accounts(runs_root, monkeypatch, {"a": refused, "b": refused}, live="a")
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=10, b=20))  # a still reads as room
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_USAGE
    assert opened == ["a", "b"]
    assert "already refused this launch" in capsys.readouterr().out


def test_launch_out_of_accounts_names_the_earliest_reset(runs_root, tmp_path, monkeypatch, capsys):
    cfg, opened = _many_accounts(runs_root, monkeypatch, {"a": ApiResponse(429, _REFUSED_EXHAUSTED)}, live="a")
    usage = _usage(a=100, b=100)
    usage[1]["usage"]["weekly"]["resetsAt"] = "2026-09-22T12:00:00+00:00"
    monkeypatch.setattr(cli, "read_usage", lambda: usage)
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_USAGE
    assert opened == ["a"]
    assert "earliest window reopens 2026-09-22 12:00Z" in capsys.readouterr().out


def test_profiles_prints_a_table_and_the_accounts_with_no_profile(runs_root, monkeypatch, capsys):
    _saved(runs_root, "a", "b")
    cli.cmd_switch(_SwitchArgs("a"), _cfg(runs_root))
    capsys.readouterr()
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=10, b=92, c=30))
    assert cli.cmd_profiles(None, _cfg(runs_root)) == EXIT_OK
    out = capsys.readouterr().out
    header = next(l for l in out.splitlines() if l.split()[:2] == ["name", "account"])
    live_row = next(l for l in out.splitlines() if l.startswith("-> a"))
    assert header.index("week") < header.index("model") and "live now" in live_row
    assert "week at 85%+" in out                      # b, dropped, in short form
    assert "no profile: c" in out and "login <name>" in out
    assert "no saved profile has room" in out         # a is live, b is over the bar


def test_login_reports_an_unusable_session_instead_of_a_raw_api_error(runs_root, monkeypatch, capsys):
    # A sign-in link that reaches the app but leaves claude.ai refusing the session used to
    # surface as "HTTP 403 account_session_invalid" from the orgs call, which reads as a
    # broken API rather than as "ask for a fresh link".
    class _Unusable(_SessionOnAccount):
        def api(self, method, path, **kw):
            if path == "/api/organizations":
                return ApiResponse(403, '{"error":{"type":"permission_error"},"error_code":"account_session_invalid"}')
            return super().api(method, path, **kw)

    session = _Unusable("acct@example.test")
    monkeypatch.setattr(cli, "_open_session", lambda p, headless=True, create=False: session)
    args = _LoginArgs()
    args.email = "acct@example.test"
    assert cli.cmd_login(args, _cfg(runs_root)) == EXIT_LOGGED_OUT
    out = capsys.readouterr().out
    assert "session is not usable" in out and "fresh sign-in link" in out
    assert "deep-research-web login acct" in out


# ------------------------------------------------ concurrent research (2026-09-25): per-account cap, --wait, spread
_OK = ApiResponse(200, "event: message_stop\r\n\r\n")


def _full(runs_root, account):
    _running(runs_root, f"{account}-slot-1", account)
    _running(runs_root, f"{account}-slot-2", account)


def _wait_args(tmp_path, minutes):
    args = _LaunchArgs(_charter(tmp_path))
    args.wait = minutes
    return args


def test_launch_wait_takes_the_slot_that_frees_up(runs_root, tmp_path, monkeypatch):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, _OK, _OK)
    _full(runs_root, "a")
    clock, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        clock[0] += s
        update_run(runs_root / "a-slot-1", status=RunStatus.DONE.value)   # one of a's runs finishes

    monkeypatch.setattr(cli, "_sleep", sleep)
    monkeypatch.setattr(cli, "_clock", lambda: clock[0])
    assert cli.cmd_launch(_wait_args(tmp_path, 5), cfg) == EXIT_OK
    assert slept == [cli.WAIT_POLL_S] and opened == ["a"]


def test_launch_wait_gives_up_after_its_minutes_and_never_opens_a_browser(runs_root, tmp_path, monkeypatch, capsys):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, _OK, _OK)
    _full(runs_root, "a")
    clock = [0.0]
    monkeypatch.setattr(cli, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(cli, "_clock", lambda: clock[0])
    assert cli.cmd_launch(_wait_args(tmp_path, 2), cfg) == EXIT_PREFLIGHT
    assert opened == [] and "holds 2 of its 2" in capsys.readouterr().out


def test_spread_is_off_by_default_so_a_full_account_refuses(runs_root, tmp_path, monkeypatch):
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, _OK, _OK)
    _full(runs_root, "a")
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=10, b=10))
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_PREFLIGHT
    assert opened == []


def test_spread_launches_on_another_account_and_never_moves_the_live_one(runs_root, tmp_path, monkeypatch, capsys):
    from dataclasses import replace
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, _OK, _OK)
    cfg = replace(cfg, spread=True)
    _full(runs_root, "a")
    monkeypatch.setattr(cli, "read_usage", lambda: _usage(a=10, b=20))
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_OK
    assert opened == ["b"] and live_name(cfg.profile, cfg.profiles) == "a"
    [rec] = [r for r in find_runs(runs_root) if not r.run_id.startswith("a-slot")]
    assert rec.account == "b" and "launching on b" in capsys.readouterr().out


def test_spread_never_takes_a_priority_account_or_a_full_one(runs_root, tmp_path, monkeypatch):
    from dataclasses import replace
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, _OK, _OK)
    cfg = replace(cfg, spread=True)
    _saved(runs_root, "c")
    _full(runs_root, "a")
    _full(runs_root, "c")
    usage = _usage(a=10, b=5, c=5)
    usage[1]["priority"] = 1                      # b: one of Louis's own accounts, never used automatically
    monkeypatch.setattr(cli, "read_usage", lambda: usage)
    assert cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg) == EXIT_PREFLIGHT
    assert opened == []



def test_a_launch_arriving_in_a_waiters_gap_queues_behind_it_and_cannot_take_the_slot(runs_root, tmp_path, monkeypatch):
    # main-dojo, 2026-09-25: main-xp's research-slot waited from 17:42 past 18:13 PDT. The slot flock itself serves
    # blocked waiters in arrival order (measured on this kernel, 10/10 threads and 10/10 processes); what starved it
    # is research-slot's own comment: "other sessions launch outside our lock, and a 5-min poll lost every gap to them".
    # Every launch enters the engine's launch queue, --wait or not, so a later launch can never take a freed slot
    # from a waiter ahead of it.
    import threading
    cfg, opened, _apis = _two_accounts(runs_root, monkeypatch, _OK, _OK)
    _full(runs_root, "a")
    results = {}
    clock = [0.0]

    def direct():
        results["direct"] = cli.cmd_launch(_LaunchArgs(_charter(tmp_path)), cfg)

    def sleep(s):
        clock[0] += s
        update_run(runs_root / "a-slot-1", status=RunStatus.DONE.value)   # a slot frees...
        late = threading.Thread(target=direct)                              # ...and a direct launch arrives in the gap
        late.start()
        late.join(0.5)                                                      # a launch outside the queue lands here
        results["late_blocked"] = late.is_alive()
        results["late"] = late

    monkeypatch.setattr(cli, "_sleep", sleep)
    monkeypatch.setattr(cli, "_clock", lambda: clock[0])
    results["waiter"] = cli.cmd_launch(_wait_args(tmp_path, 5), cfg)
    results["late"].join(5)
    assert results["late_blocked"], "the direct launch waited in the queue behind the waiter"
    assert results["waiter"] == EXIT_OK, "the waiter took the slot it waited for"
    assert results["direct"] == EXIT_PREFLIGHT, "the later launch found the account full again, and never jumped"

# ABOUTME: Tests for one watcher pass with a fake session, a recording sender and a stub collect.
# ABOUTME: Covers idle, halt on flags, done, poll failures, stale, and the browser-closed ordering.
import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeApi, research_done, research_started

from deep_research_web.client import ApiResponse
from deep_research_web.config import Config
from deep_research_web.notify import NotifyError
from deep_research_web.runs import RunRecord, read_run, write_run
from deep_research_web.watch import MAX_POLL_FAILURES, watch

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self, api):
        self.api = api
        self.opened = 0
        self.closed = False

    def __enter__(self):
        self.opened += 1
        return self

    def __exit__(self, *exc):
        self.closed = True

    def require_login(self):
        pass


def _cfg(runs_root):
    return Config("m", None, runs_root, runs_root / "profile", runs_root / "profiles")


def _run(runs_root, run_id="r1", started=T0, status="running", **over):
    d = runs_root / run_id
    d.mkdir()
    fields = dict(run_id=run_id, question="q?", status=status, org_uuid="o", conversation_uuid="c",
                  chat_url="https://claude.ai/chat/c", model="m", charter=str(d / "charter.md"),
                  out_dir=str(d), started_at=started.isoformat(timespec="seconds"))
    fields.update(over)
    write_run(d, RunRecord(**fields))
    return d


def _api(conversation, flags=()):
    orgs = [{"uuid": "o", "capabilities": ["chat"], "active_flags": list(flags)}]
    routes = {("GET", "/api/organizations"): ApiResponse(200, json.dumps(orgs))}
    if conversation is not None:
        routes[("GET", "/api/organizations/o/chat_conversations/c")] = ApiResponse(200, json.dumps(conversation))
    return FakeApi(routes)


def test_idle_pass_opens_no_browser(runs_root):
    _run(runs_root, status="done")
    sent, session = [], FakeSession(_api(None))
    assert watch(_cfg(runs_root), lambda p: session, sent.append, lambda r, c: (0, {}), lambda: T0) == 0
    assert session.opened == 0 and sent == []


def test_flags_halt_every_run_and_ping_once(runs_root):
    d = _run(runs_root)
    sent = []
    session = FakeSession(_api(research_started(), flags=[{"type": "consumer_first_warning", "expires_at": "x"}]))
    assert watch(_cfg(runs_root), lambda p: session, sent.append, lambda r, c: (0, {}), lambda: T0) == 1
    assert read_run(d).status == "halted" and "consumer_first_warning" in read_run(d).reason
    assert len(sent) == 1 and "HALTED" in sent[0] and "consumer_first_warning" in sent[0]


def test_done_collects_and_pings_with_counts(runs_root):
    d = _run(runs_root)
    sent, collected = [], []

    def collect(rec, conversation):
        collected.append(rec.run_id)
        return 0, {"quoted": 2, "live": 1, "misquoted": 0, "dead": 0, "unverifiable": 0, "unchecked": 0}

    session = FakeSession(_api(research_done()))
    assert watch(_cfg(runs_root), lambda p: session, sent.append, collect, lambda: T0 + timedelta(minutes=20)) == 0
    assert collected == ["r1"]
    assert sent == ['research done: "q?" https://claude.ai/chat/c report: ' + str(d / "report.md") + " (2 quoted, 1 live)"]
    assert read_run(d).notified_at


def test_poll_failures_count_up_and_fail_at_five(runs_root):
    d = _run(runs_root, poll_failures=MAX_POLL_FAILURES - 1)
    sent = []
    session = FakeSession(_api(None))  # conversation route missing -> 404 -> ApiError
    watch(_cfg(runs_root), lambda p: session, sent.append, lambda r, c: (0, {}), lambda: T0)
    rec = read_run(d)
    assert rec.status == "failed" and "consecutive poll failures" in rec.reason
    assert len(sent) == 1 and "failed" in sent[0]


def test_one_poll_failure_is_tolerated(runs_root):
    d = _run(runs_root)
    sent = []
    watch(_cfg(runs_root), lambda p: FakeSession(_api(None)), sent.append, lambda r, c: (0, {}), lambda: T0)
    assert read_run(d).status == "running" and read_run(d).poll_failures == 1 and sent == []


def test_running_resets_failures_and_goes_stale_after_ninety_minutes(runs_root):
    d = _run(runs_root, poll_failures=2)
    sent = []
    watch(_cfg(runs_root), lambda p: FakeSession(_api(research_started())), sent.append, lambda r, c: (0, {}), lambda: T0 + timedelta(minutes=30))
    assert read_run(d).status == "running" and read_run(d).poll_failures == 0 and sent == []
    watch(_cfg(runs_root), lambda p: FakeSession(_api(research_started())), sent.append, lambda r, c: (0, {}), lambda: T0 + timedelta(minutes=91))
    assert read_run(d).status == "stale" and len(sent) == 1 and "stale" in sent[0]


def test_a_notified_run_is_never_polled_or_pinged_again(runs_root):
    _run(runs_root, notified_at="2026-09-16T12:00:00+00:00")
    sent, api = [], _api(research_done())
    watch(_cfg(runs_root), lambda p: FakeSession(api), sent.append, lambda r, c: (0, {}), lambda: T0)
    assert sent == [] and not any("/chat_conversations/" in call[1] for call in api.calls)


def test_collect_disagreeing_keeps_the_run_running_without_a_ping(runs_root):
    d = _run(runs_root)
    sent = []
    watch(_cfg(runs_root), lambda p: FakeSession(_api(research_done())), sent.append, lambda r, c: (6, {}), lambda: T0)
    assert sent == [] and read_run(d).status == "running" and read_run(d).notified_at is None


def test_collect_crash_counts_as_a_poll_failure(runs_root):
    d = _run(runs_root, poll_failures=MAX_POLL_FAILURES - 1)
    sent = []

    def boom(rec, conversation):
        raise RuntimeError("charter.md missing")

    watch(_cfg(runs_root), lambda p: FakeSession(_api(research_done())), sent.append, boom, lambda: T0)
    rec = read_run(d)
    assert rec.status == "failed" and "charter.md missing" in rec.reason and len(sent) == 1


def test_grading_runs_after_the_session_closed(runs_root):
    # Grading fetches third-party pages and can take minutes; the browser must be gone by then.
    d = _run(runs_root)
    session = FakeSession(_api(research_done()))
    seen = []

    def collect(rec, conversation):
        assert session.closed is True
        seen.append(rec.run_id)
        return 0, {}

    assert watch(_cfg(runs_root), lambda p: session, lambda m: None, collect, lambda: T0) == 0
    assert seen == ["r1"] and read_run(d).notified_at


def test_a_failed_send_leaves_the_run_unnotified(runs_root):
    d = _run(runs_root)

    def sender(message):
        raise NotifyError("telegram API error")

    with pytest.raises(NotifyError):
        watch(_cfg(runs_root), lambda p: FakeSession(_api(research_done())), sender,
              lambda r, c: (0, {}), lambda: T0)
    assert read_run(d).notified_at is None


def _profiles(runs_root, *names):
    for n in names:
        (runs_root / "profiles" / n).mkdir(parents=True)


def test_one_session_per_account(runs_root):
    _profiles(runs_root, "perso", "work5")
    _run(runs_root, "r1", account="perso")
    _run(runs_root, "r2", account="work5")
    opened = []

    def factory(profile):
        opened.append(profile.name)
        return FakeSession(_api(research_started()))

    assert watch(_cfg(runs_root), factory, [].append, lambda r, c: (0, {}), lambda: T0) == 0
    assert opened == ["perso", "work5"]


def test_a_legacy_run_without_an_account_reads_through_the_live_link(runs_root):
    # A record written before this feature carries no account; the watcher must still
    # find it, through the live symlink rather than a per-account profile directory.
    _run(runs_root, "r1")  # no account= passed: RunRecord.account defaults to None
    opened = []

    def factory(profile):
        opened.append(profile)
        return FakeSession(_api(research_started()))

    cfg = _cfg(runs_root)
    assert watch(cfg, factory, [].append, lambda r, c: (0, {}), lambda: T0) == 0
    assert opened == [cfg.profile]


def test_a_flag_on_one_account_halts_every_run(runs_root):
    _profiles(runs_root, "perso", "work5")
    d1 = _run(runs_root, "r1", account="perso")
    d2 = _run(runs_root, "r2", account="work5")
    flagged = [{"type": "consumer_first_warning", "expires_at": "x"}]

    def factory(profile):
        return FakeSession(_api(research_started(), flags=flagged if profile.name == "work5" else ()))

    sent = []
    assert watch(_cfg(runs_root), factory, sent.append, lambda r, c: (0, {}), lambda: T0) == 1
    assert read_run(d1).status == read_run(d2).status == "halted"
    assert len(sent) == 1


def test_a_run_whose_account_profile_is_gone_counts_a_poll_failure(runs_root):
    d = _run(runs_root, "r1", account="ghost")

    def factory(profile):
        raise AssertionError("no browser for a profile that is gone")

    assert watch(_cfg(runs_root), factory, [].append, lambda r, c: (0, {}), lambda: T0) == 0
    assert read_run(d).poll_failures == 1

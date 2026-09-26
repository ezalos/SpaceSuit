# ABOUTME: The five-minute watcher pass: polls in-flight claude-web runs, collects finished ones, pings once per outcome.
# ABOUTME: Opens no browser when nothing is running; one session per account; halts everything on an account flag and never dismisses one.
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .client import Client, choose_org
from .config import Config
from .notify import done_line, outcome_line
from .profiles import group_by_profile
from .report import REPORT_NAME
from .runs import RunRecord, RunStatus, collect_lock, find_runs, running_runs, update_run
from .session import SessionError
from .thread import ThreadState, classify, failure_reason

STALE_AFTER = timedelta(minutes=90)
MAX_POLL_FAILURES = 5
# Runs graded at once. Grading fetches every cited page serially over plain HTTP; with several
# research runs in flight (2026-09-25) several can finish in one pass, and graded one after another
# they could outlast the unit's 25 min. Threads suffice: the work is I/O, and no browser is open.
COLLECT_WORKERS = 4
# A run that finished without its ping (the notifier failed) is pinged by a later pass, if it finished this recently.
# Older ones are history: 61 runs on the seat predate its notifier, and turning it on must not replay them.
RETRY_PING_WINDOW = timedelta(hours=6)


def _log(level: str, message: str) -> None:
    from .__main__ import log  # late import: __main__ imports this module

    log(level, message)


def _fetch_conversation(client: Client, rec: RunRecord) -> dict:
    from .__main__ import fetch_conversation  # late import: __main__ imports this module

    return fetch_conversation(client, rec)


def watch(
    cfg: Config,
    session_factory: Callable[[Path], object],
    sender: Callable[[str], None],
    collect: Callable[[RunRecord, dict], tuple[int, dict]],
    now: Callable[[], datetime],
) -> int:
    _retry_pings(cfg, sender, now)
    running = running_runs(cfg.runs_root)
    if not running:
        return 0
    fetched: list[tuple[RunRecord, dict | None, Exception | None]] = []
    for profile, group in group_by_profile(cfg.profile, cfg.profiles, running).items():
        if profile != cfg.profile and not profile.exists():
            # An account profile that was moved or deleted: its runs cannot be polled, which is
            # a poll failure (five in a row fail the run and ping), not a crash of the whole pass.
            gone = SessionError(f"profile {profile} is gone; its runs cannot be polled")
            fetched.extend((rec, None, gone) for rec in group if not rec.notified_at)
            continue
        with session_factory(profile) as s:
            s.require_login()
            client = Client(s.api)
            org = choose_org(client.orgs())
            flags = client.flags(org)
            if flags:
                # A flag on ANY account stops every run: never poll on, never rotate away from it.
                return _halt(running, flags, sender, now)
            for rec in group:
                if rec.notified_at:  # already reported once: never polled or pinged again
                    continue
                try:
                    fetched.append((rec, _fetch_conversation(client, rec), None))
                except Exception as exc:  # any fetch failure counts; five in a row fail the run
                    fetched.append((rec, None, exc))
    # The browser is closed from here on. Grading fetches third-party pages and Telegram
    # can hang; neither is a reason to hold a claude.ai session open, and a pass that
    # hangs past the unit's TimeoutStartSec now leaves no browser behind when it is killed.
    with ThreadPoolExecutor(max_workers=COLLECT_WORKERS) as pool:
        list(pool.map(lambda item: _poll_locked(*item, sender, collect, now), fetched))
    return 0


def _poll_locked(rec: RunRecord, conversation: dict | None, exc: Exception | None, sender, collect, now) -> None:
    """_poll under the run's collect lock: a manual `collect` holding it owns the run, and this pass
    leaves it alone rather than grade it twice (the next pass sees whatever that collect wrote)."""
    with collect_lock(Path(rec.out_dir)) as mine:
        if mine:
            _poll(rec, conversation, exc, sender, collect, now)


def _ping(sender, text: str) -> bool:
    """Send one ping; a notifier that fails is logged, never raised: the collector's work is already on disk, the
    run stays un-notified, and _retry_pings sends it on a later pass (the unit stays green)."""
    try:
        sender(text)
        return True
    except Exception as exc:  # noqa: BLE001 - any notifier failure; the pass must go on
        _log("WARNING", f"deep-research: ping not sent ({type(exc).__name__}: {str(exc)[:160]}); retried next pass")
        return False


def _retry_pings(cfg: Config, sender, now: Callable[[], datetime]) -> None:
    from time import time as _now

    for rec in find_runs(cfg.runs_root):
        if rec.status == RunStatus.RUNNING.value or rec.notified_at:
            continue
        out = Path(rec.out_dir)
        try:
            finished = max(f.stat().st_mtime for f in (out / "run.json", out / REPORT_NAME) if f.exists())
        except ValueError:
            continue
        if _now() - finished > RETRY_PING_WINDOW.total_seconds():
            continue
        if rec.status == RunStatus.DONE.value:
            text = f'research done: "{rec.question}" {rec.chat_url} report: {out / REPORT_NAME} (ping delayed)'
        else:
            text = outcome_line(rec, rec.status, f"{rec.reason or rec.status} (ping delayed)")
        if _ping(sender, text):
            update_run(out, notified_at=now().isoformat(timespec="seconds"))


def _halt(running: list[RunRecord], flags: list[dict], sender, now) -> int:
    names = ", ".join(f"{f.get('type')} (expires {f.get('expires_at')})" for f in flags)
    _log("CRITICAL", f"deep-research: halted on account flags: {names}")
    # Ping first: a run marked halted-and-notified that was never pinged is a silent stop.
    sent = _ping(sender, f"HALTED all research runs: account flags {names}. Nothing dismissed; look at what was asked, not at the polling.")
    stamp = now().isoformat(timespec="seconds") if sent else None
    for rec in running:   # halted whether or not the ping went out: the halt is the safety, the ping the courtesy
        update_run(Path(rec.out_dir), status=RunStatus.HALTED.value, reason=f"account flags: {names}", notified_at=stamp)
    return 1


def _count_failure(rec: RunRecord, out: Path, exc: Exception, sender, now) -> None:
    failures = rec.poll_failures + 1
    if failures >= MAX_POLL_FAILURES:
        reason = f"{failures} consecutive poll failures: {exc}"
        _log("WARNING", f"deep-research: run {rec.run_id} failed: {reason}")
        sent = _ping(sender, outcome_line(rec, "failed", reason))
        update_run(out, status=RunStatus.FAILED.value, reason=reason, poll_failures=failures,
                   notified_at=now().isoformat(timespec="seconds") if sent else None)
    else:
        update_run(out, poll_failures=failures)


def _poll(rec: RunRecord, conversation: dict | None, exc: Exception | None, sender, collect, now) -> None:
    """Everything after the browser closed: classify, grade, ping, record. No client here.

    Every branch that pings sends first and stamps notified_at after, so a failed send
    leaves the run un-notified and the next pass retries instead of going silent.
    """
    out = Path(rec.out_dir)
    if exc is not None:
        _count_failure(rec, out, exc, sender, now)
        return

    state = classify(conversation).state
    stamp = now().isoformat(timespec="seconds")
    if state is ThreadState.DONE:
        try:
            code, counts = collect(rec, conversation)
        except Exception as crash:  # a collect crash is a poll failure, not a watcher crash
            _count_failure(rec, out, crash, sender, now)
            return
        if code == 6:  # collect saw the run still running: transient disagreement, try again next pass
            update_run(out, poll_failures=0)
            return
        # collect already wrote status: done, so a retried send cannot re-collect.
        if _ping(sender, done_line(rec, counts, out / REPORT_NAME)):
            update_run(out, notified_at=stamp)
        return
    if state in (ThreadState.NEEDS_REPLY, ThreadState.EMPTY):
        reason = failure_reason(state)
        # A run waiting on Louis is not a failure and must not be reported as one: it settles
        # here so it stops aging toward stale, and the ping tells him there is something to answer.
        settled = (RunStatus.NEEDS_REPLY if state is ThreadState.NEEDS_REPLY else RunStatus.FAILED).value
        _log("WARNING", f"deep-research: run {rec.run_id} {settled}: {reason}")
        sent = _ping(sender, outcome_line(rec, settled, reason))
        update_run(out, status=settled, reason=reason, notified_at=stamp if sent else None)
        return
    started = datetime.fromisoformat(rec.started_at)
    if now() - started > STALE_AFTER:
        reason = f"no report after {int(STALE_AFTER.total_seconds() // 60)} min"
        _log("WARNING", f"deep-research: run {rec.run_id} stale")
        sent = _ping(sender, outcome_line(rec, "stale", reason))
        update_run(out, status=RunStatus.STALE.value, reason=reason, notified_at=stamp if sent else None)
        return
    update_run(out, poll_failures=0)

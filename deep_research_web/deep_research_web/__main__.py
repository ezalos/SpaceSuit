# ABOUTME: The deep-research-web CLI: login, switch, profiles, launch, status, collect, archive, check-claims, stop, report, list, watch.
# ABOUTME: Exit codes: 0 ok, 1 problem, 2 logged out, busy or profile unusable, 3 preflight, 4 usage exhausted, 5 needs reply, 6 still running.
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from deep_research.charter import parse_charter
from deep_research.verify import cited_markers, fetch

from .accounts import (
    UsageUnavailable, earliest_reset, email_for, pick_all, profiles_table, read_usage, verdict_line,
)
from .archive import NAME_MAX as ARCHIVE_NAME_MAX, ArchiveError, archive_run, valid_name
from .claims import ClaimsError, check_claims, verdict_counts
from .client import USAGE_REFUSALS, ApiError, Client, choose_org, exhausted, refusal
from .config import Config, load_config
from .grade import Grade, count_grades, grade_citations
from .notify import NotifyError
from .payload import completion_payload, local_timezone, new_uuid
from .profiles import ProfileError, group_by_profile, live_name, run_profile, saved_profiles, switch_to
from .prompt import build_web_prompt
from .report import (
    CONVERSATION_NAME, DONE_SENTINEL, REPORT_NAME, citations_from_artifact, unanswered_hint,
    write_report_files,
)
from .runs import (
    AmbiguousRunId, RunRecord, RunStatus, chat_url, collect_lock, find_run, find_runs, list_all, make_run_id,
    running_on, running_runs, update_run, write_run,
)
from .session import LOGIN_CHALLENGE_S, BrowserError, Session, SessionError
from .thread import (
    ThreadState, classify, failure_reason, has_research_task, live_thread, research_task_id,
)

EXIT_OK, EXIT_PROBLEM, EXIT_LOGGED_OUT, EXIT_PREFLIGHT = 0, 1, 2, 3
EXIT_USAGE, EXIT_NEEDS_REPLY, EXIT_RUNNING = 4, 5, 6
ENGAGEMENT_TRIES = 4
ENGAGEMENT_WAIT_S = 30
NAME_MAX = 120
SKILL = "deep-research-claude-web"
PROBLEM_GRADES = {Grade.DEAD, Grade.UNVERIFIABLE, Grade.MISQUOTED}
ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _valid_name(name: str) -> bool:
    """claude-usage account names, and nothing that could escape cfg.profiles as a path."""
    return bool(ACCOUNT_NAME_RE.match(name))


class PreflightError(RuntimeError):
    pass


class _Unset:
    """Tells 'launch_run was called without account=' apart from 'account=None was passed'."""


_UNSET = _Unset()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_reset(reset: str) -> str:
    """A unix-seconds reset stamp as a readable local time, or the raw value if it is not one."""
    try:
        return datetime.fromtimestamp(int(reset)).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return str(reset)


def log(level: str, message: str) -> None:
    """Observability line through claude-log when it is installed; silent otherwise."""
    if shutil.which("claude-log"):
        subprocess.run(["claude-log", SKILL, level, message], check=False)


def preflight(client: Client, model: str, project: str | None) -> tuple[dict, str | None]:
    org = choose_org(client.orgs())
    flags = client.flags(org)
    if flags:
        names = ", ".join(f"{f.get('type')} (expires {f.get('expires_at')})" for f in flags)
        raise PreflightError(f"account flags present, refusing to launch: {names}")
    if not client.model_available(org["uuid"], model):
        raise PreflightError(f"model {model} is not available to this account")
    project_uuid = None
    if project:
        project_uuid = client.find_project(org["uuid"], project)
        if project_uuid is None:
            seen = ", ".join(p.get("name", "") for p in client.projects(org["uuid"])) or "none"
            raise PreflightError(f"project {project!r} not found; projects seen: {seen}")
    return org, project_uuid


def launch_run(
    client: Client, cfg: Config, charter_path: Path, model: str, project: str | None,
    force: bool, sleep: Callable[[float], None] = time.sleep, now: Callable[[], str] = now_iso,
    account: str | None | _Unset = _UNSET, name: str | None = None,
) -> int:
    if name is not None and not valid_name(name):
        print(f"--name {name!r}: lowercase kebab-case, at most {ARCHIVE_NAME_MAX} characters (it names the archive directory)")
        return EXIT_PROBLEM
    live = live_name(cfg.profile, cfg.profiles)
    resolved_account = live if isinstance(account, _Unset) else account
    # The cap is per ACCOUNT (was: one run on the whole seat). claude.ai ran two Research tasks
    # concurrently on one account when measured (2026-09-25); several accounts are separate browsers.
    busy = running_on(cfg.runs_root, resolved_account, live)
    if len(busy) >= cfg.max_per_account and not force:
        ids = ", ".join(r.run_id for r in busy)
        print(f"a run is already in flight: account {resolved_account or 'live'} holds {len(busy)} of its "
              f"{cfg.max_per_account} ({ids}); wait (launch --wait) or pass --force")
        log("WARNING", f"deep-research: cap reached on {resolved_account} ({ids}); offered force or wait")
        return EXIT_PREFLIGHT

    charter_text = charter_path.read_text(encoding="utf-8")
    charter = parse_charter(charter_text)
    try:
        org, project_uuid = preflight(client, model, project)
    except (PreflightError, ApiError) as exc:
        print(f"preflight refused: {exc}")
        log("CRITICAL", f"deep-research: preflight refused: {exc}")
        return EXIT_PREFLIGHT

    conv = new_uuid()
    payload = completion_payload(build_web_prompt(charter), model, local_timezone(), project_uuid)
    response = client.start_research(org["uuid"], conv, payload)
    if response.status == 400 and project_uuid:
        # The project param name has a single source; fall back and say so.
        print("claude.ai rejected the project parameter; launching outside the project")
        log("WARNING", "deep-research: project_uuid rejected; launched without a project")
        project_uuid = None
        payload = completion_payload(build_web_prompt(charter), model, local_timezone(), None)
        response = client.start_research(org["uuid"], conv, payload)
    if response.status == 429:
        # A 429 is a hard reject with no stream: nothing started, so there is no run to keep.
        # The body says WHY, and the two reasons want opposite responses: an exhausted window
        # can only be waited out, while a short-term rate limit clears in minutes. Reporting
        # both as "usage window exhausted" sent a launch that extra usage already covered into
        # a three-day wait, with the body that said otherwise thrown away unread.
        kind, reset = refusal(response.text)
        detail = f"; resets at {_fmt_reset(reset)}" if reset else ""
        if kind in USAGE_REFUSALS:
            print(f"usage window exhausted ({kind}{detail}); the request was refused (see /usage in any session)")
            log("WARNING", f"deep-research: usage window exhausted (HTTP 429 {kind}{detail})")
            return EXIT_USAGE
        print(f"claude.ai refused the request: HTTP 429 {kind}{detail}; nothing started")
        print(f"  body: {response.text[:300]}")
        log("WARNING", f"deep-research: launch refused (HTTP 429 {kind}{detail})")
        return EXIT_PROBLEM
    if response.status != 200:
        print(f"launch failed: HTTP {response.status}: {response.text[:300]}")
        log("CRITICAL", f"deep-research: launch failed: HTTP {response.status}")
        return EXIT_PROBLEM
    # A 200 may carry a message_limit "exceeded" while still starting the run: extra usage
    # or credits cover it. So the limit signal is NOT fatal here; the engagement check below
    # decides. Only a run that never started is reported as usage-blocked.
    reset = exhausted(response.text)

    url = chat_url(conv)
    # The run is already researching server-side once the POST above succeeds; write
    # run.json now so a failure in the engagement poll below never orphans it.
    run_id = make_run_id(charter.question, datetime.now())
    out_dir = cfg.runs_root / run_id
    if out_dir.exists():
        # make_run_id has second resolution; the switch-and-retry path launches this exact
        # charter twice in quick succession, so a same-second collision is expected, not
        # rare. Disambiguate rather than silently overwrite the first run's record.
        suffix = 2
        while (cfg.runs_root / f"{run_id}-{suffix}").exists():
            suffix += 1
        run_id = f"{run_id}-{suffix}"
        out_dir = cfg.runs_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "charter.md").write_text(charter_text, encoding="utf-8")
    rec = RunRecord(
        run_id=run_id, question=charter.question, status=RunStatus.RUNNING.value,
        org_uuid=org["uuid"], conversation_uuid=conv, chat_url=url, model=model,
        charter=str(out_dir / "charter.md"), out_dir=str(out_dir), started_at=now(), name=name,
        task_id=None, project_uuid=project_uuid, account=resolved_account,
    )
    write_run(out_dir, rec)

    engaged, task_id = False, None
    try:
        for attempt in range(ENGAGEMENT_TRIES):
            thread = live_thread(client.conversation(org["uuid"], conv))
            if has_research_task(thread):
                engaged, task_id = True, research_task_id(thread)
                break
            if attempt < ENGAGEMENT_TRIES - 1:
                sleep(ENGAGEMENT_WAIT_S)
    except ApiError as exc:
        print(f"  engagement check could not read the conversation: {exc}; leaving the run as running")
        log("WARNING", f"deep-research: run {run_id} engagement check failed: {exc}")
        engaged, task_id = True, None

    if not engaged and reset:
        # No research task AND the response flagged the window exceeded: the cap actually
        # blocked this one. The record is kept as failed so the account state is on disk.
        # A run marked failed is never polled again, so a late engagement would research
        # unseen and bill extra usage (and duplicate after an auto-switch); stop it here.
        # Best effort: a dead stop_response must not turn a usage block into a crash.
        try:
            client.stop_response(org["uuid"], conv)
        except ApiError as exc:
            print(f"  could not stop the response: {exc}")
            log("WARNING", f"deep-research: run {run_id} stop_response failed: {exc}")
        update_run(out_dir, status=RunStatus.FAILED.value,
                   reason=f"usage window exhausted; no research task started (resets at {_fmt_reset(reset)})")
        print(f"usage window exhausted; no research run started; resets at {_fmt_reset(reset)}")
        log("WARNING", f"deep-research: run {run_id} usage-blocked; resets at {_fmt_reset(reset)}")
        return EXIT_USAGE
    if not engaged:
        update_run(out_dir, status=RunStatus.NEEDS_REPLY.value, reason="no research task within the engagement window")
        print(f"no research started; the assistant is waiting on you: {url}")
        print("  the conversation is kept; answer it there, or adjust the charter and relaunch")
        log("WARNING", f"deep-research: run {run_id} needs a reply")
        return EXIT_NEEDS_REPLY
    update_run(out_dir, task_id=task_id)
    if reset:
        print(f"  note: base usage window is exhausted (resets at {_fmt_reset(reset)}); extra usage is covering this run")
    # The URL is printed before the rename: the run exists on claude.ai either way, and a
    # dead API on the rename call must not cost Louis the link to a live research run.
    print(f"launched {run_id}")
    print(f"  chat:    {url}")
    client.rename(org["uuid"], conv, charter.question[:NAME_MAX])
    print(f"  status:  deep-research-web status {run_id}")
    print(f"  collect: deep-research-web collect {run_id}")
    log("INFO", f"deep-research: launched {run_id}; {model}; {len(charter.must_answer)} sub-questions")
    return EXIT_OK


def fetch_conversation(client: Client, rec: RunRecord) -> dict:
    """The one browser-bound half of collect: read the conversation and save it raw."""
    out = Path(rec.out_dir)
    conversation = client.conversation(rec.org_uuid, rec.conversation_uuid)
    out.mkdir(parents=True, exist_ok=True)
    (out / CONVERSATION_NAME).write_text(json.dumps(conversation, indent=2) + "\n", encoding="utf-8")
    return conversation


def collect_conversation(
    rec: RunRecord, conversation: dict, verify: bool = True, fetcher=fetch,
    now: Callable[[], str] = now_iso, archive_root: Path | None = None,
) -> tuple[int, dict[str, int]]:
    """Everything after the fetch. No client, no browser: grading fetches third-party pages
    for as long as it needs and must never hold the claude.ai session open while it does."""
    out = Path(rec.out_dir)
    cls = classify(conversation)
    print(f"{rec.run_id}  {cls.state.value}")
    print(f"  chat: {rec.chat_url}")

    if cls.state is ThreadState.RUNNING:
        print("  still running; try again later")
        return EXIT_RUNNING, {}
    if cls.state in (ThreadState.NEEDS_REPLY, ThreadState.EMPTY):
        reason = failure_reason(cls.state)
        body = cls.text or "(no assistant text)"
        (out / REPORT_NAME).write_text(
            f"# NOT A RESEARCH REPORT\n\nNo report was produced: {reason}. Assistant text:\n\n{body}\n",
            encoding="utf-8",
        )
        settled = (RunStatus.NEEDS_REPLY if cls.state is ThreadState.NEEDS_REPLY else RunStatus.FAILED).value
        update_run(out, status=settled, reason=reason, collected_at=now())
        print(f"  {settled}: {reason}; the assistant text is in report.md")
        log("WARNING", f"deep-research: run {rec.run_id} {settled} without research")
        return EXIT_PROBLEM, {}

    content = str(cls.artifact["content"])
    citations = citations_from_artifact(cls.artifact)
    graded = grade_citations(citations, content, fetcher=fetcher, verify=verify)
    charter = parse_charter((out / "charter.md").read_text(encoding="utf-8"))
    unanswered = unanswered_hint(charter.must_answer, content)
    write_report_files(out, content, citations, graded, unanswered)
    (out / DONE_SENTINEL).touch()
    rec = update_run(out, status=RunStatus.DONE.value, collected_at=now())
    if archive_root is not None:
        # The library copy is a courtesy on top of a finished collect: its failure is reported, never fatal.
        try:
            dest = archive_run(rec, archive_root)
            print(f"  archived: {dest}")
            print(f"  claims check (opt-in): deep-research-web check-claims {rec.run_id}")
        except Exception as exc:
            print(f"  archive failed: {exc}")
            log("WARNING", f"deep-research: archive failed for {rec.run_id}: {exc}")

    counts = count_grades(graded)
    print(f"  report: {out / REPORT_NAME}")
    print("  citations: " + ", ".join(f"{counts[k]} {k}" for k in counts))
    markers = cited_markers((out / REPORT_NAME).read_text(encoding="utf-8"))
    listed = {g.n for g in graded}
    if markers != listed:
        print(f"  MARKER GAP: report uses {sorted(markers)} but sources list {sorted(listed)}")
    flagged = [g for g in graded if g.grade is not Grade.QUOTED]
    if flagged:
        print("  not QUOTED, decide these yourself:")
        for g in flagged:
            print(f"    - [{g.n}] {g.url}  {g.grade.value}: {g.detail}")
    if unanswered:
        print("  possibly unanswered (word-overlap hint, not a verdict):")
        for q in unanswered:
            print(f"    - {q}")
    problems = markers != listed or any(g.grade in PROBLEM_GRADES for g in graded)
    level = "WARNING" if problems else "INFO"
    log(level, f"deep-research: collected {rec.run_id}; " + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    return (EXIT_PROBLEM if problems else EXIT_OK), counts


def collect_record(
    client: Client, rec: RunRecord, verify: bool = True, fetcher=fetch, archive_root: Path | None = None,
    now: Callable[[], str] = now_iso,
) -> tuple[int, dict[str, int]]:
    conversation = fetch_conversation(client, rec)
    return collect_conversation(rec, conversation, verify=verify, fetcher=fetcher, now=now, archive_root=archive_root)


def _open_session(profile: Path, headless: bool = False, create: bool = False) -> Session:
    # Headed on the machine's X session by default: headless Chrome never clears the
    # Cloudflare challenge on claude.ai (probed 2026-09-16), a headed one does in a second.
    return Session(profile, headless=headless, create=create)


def cmd_login(args, cfg: Config) -> int:
    if not _valid_name(args.name):
        print(f"invalid account name {args.name!r}: letters, digits, - and _ only")
        return EXIT_PROBLEM
    profile = cfg.profiles / args.name
    email = args.email
    if not email:
        try:
            email = email_for(read_usage(), args.name)
        except UsageUnavailable as exc:
            print(f"claude-usage could not name the account: {exc}; pass --email")
            return EXIT_PROBLEM
    if not email:
        print(f"claude-usage stores no account named {args.name}; pass --email")
        return EXIT_PROBLEM
    # 0700 up front: a profile dir Session.create=True makes below inherits it, but the
    # parent must exist and be private before any browser touches this account's cookies.
    cfg.profiles.mkdir(mode=0o700, parents=True, exist_ok=True)
    cfg.profiles.chmod(0o700)
    print("opening claude.ai on the X display; if a Cloudflare checkbox shows there, tick it")
    print("through the streaming bridge (up to 10 minutes), then answer the prompt below with")
    print("whatever the mail carries: the code, or the sign-in link (pasted whole)")
    with _open_session(profile, headless=args.headless, create=True) as s:
        if s.logged_in(challenge_timeout_s=LOGIN_CHALLENGE_S):
            print("already logged in")
        else:
            s.login(email, lambda: input("code from the claude.ai email, or the sign-in link: "))
        client = Client(s.api)
        # Report the account claude.ai names, never the expected label. A profile that is
        # already logged in keeps whatever account it held, and login short-circuits above
        # without switching it: echoing the config here reported an account switch that never
        # happened, and the wrong account then owned every usage refusal that followed.
        actual = client.account_email()
        try:
            org = choose_org(client.orgs())
        except ApiError as exc:
            # The browser reached the app but claude.ai does not accept the session yet, or
            # at all. Reporting the raw 403 here sent the reader looking for a broken API;
            # the fix is a fresh sign-in link or another try, so say that instead.
            print(f"the browser reached claude.ai but the session is not usable: {exc}")
            print(f"  ask claude.ai for a fresh sign-in link, then: deep-research-web login {args.name}")
            log("WARNING", f"deep-research: login left {args.name} without a usable session")
            return EXIT_LOGGED_OUT
    print(f"logged in as {actual or 'unknown: claude.ai did not say'}; "
          f"org {org.get('name')} ({org.get('uuid')})")
    if actual and actual.lower() != email.lower():
        print(f"  NOT the expected account: {args.name} is {email}, this profile is {actual}")
        print("  login cannot switch a live session; move the profile directory aside first:")
        print(f"    {profile}")
        return EXIT_PROBLEM
    print(f"saved as profile {args.name}; make it live with: deep-research-web switch {args.name}")
    return EXIT_OK


def _pick_candidates(cfg: Config, live: str | None, tried: frozenset[str] = frozenset()):
    """Read claude-usage, print a verdict per account, and return the accounts with room, best first."""
    try:
        accounts = read_usage()
    except UsageUnavailable as exc:
        print(f"the pick could not run: {exc}")
        return [], []
    try:
        order, verdicts = pick_all(accounts, saved_profiles(cfg.profiles), live, cfg.model, tried=tried)
    except (AttributeError, TypeError, KeyError) as exc:
        # claude-usage printed valid JSON in a shape the pick cannot walk: report it the same
        # way as UsageUnavailable rather than let it escape as a crash.
        print(f"the pick could not run: {exc}")
        return [], []
    for v in verdicts:
        print(verdict_line(v, chosen=bool(order) and v.name == order[0]))
    if not order:
        print("no saved profile has room")
        reset = earliest_reset(verdicts)
        if reset:
            print(f"  earliest window reopens {reset}")
    return order, verdicts


def _pick_next(cfg: Config, live: str | None) -> str | None:
    order, _ = _pick_candidates(cfg, live)
    return order[0] if order else None


def cmd_switch(args, cfg: Config) -> int:
    if args.name is not None and not _valid_name(args.name):
        print(f"invalid account name {args.name!r}: letters, digits, - and _ only")
        return EXIT_PROBLEM
    live = live_name(cfg.profile, cfg.profiles)
    name = args.name or _pick_next(cfg, live)
    if name is None:
        return EXIT_PROBLEM
    if name == live:
        print(f"{name} is already live")
        return EXIT_OK
    try:
        switch_to(cfg.profile, cfg.profiles, name)
    except ProfileError as exc:
        print(str(exc))
        return EXIT_PROBLEM
    print(f"live profile: {live or 'none'} -> {name}")
    log("INFO", f"deep-research: switched {live or 'none'} -> {name} by hand")
    return EXIT_OK


def cmd_profiles(args, cfg: Config) -> int:
    live = live_name(cfg.profile, cfg.profiles)
    saved = saved_profiles(cfg.profiles)
    print(f"live: {live or f'none ({cfg.profile} is not a symlink into {cfg.profiles})'}")
    try:
        accounts = read_usage()
        order, verdicts = pick_all(accounts, saved, live, cfg.model)
    except (UsageUnavailable, AttributeError, TypeError, KeyError) as exc:
        # No meters: still say which profiles exist, which is what this command is for.
        print(f"the pick could not run: {exc}")
        for name in sorted(saved):
            print(f"  {'-> ' if name == live else '   '}{name}")
        return EXIT_OK
    print()
    for line in profiles_table(verdicts, live, saved):
        print(line)
    others = sorted(v.name for v in verdicts if v.name not in saved)
    if others:
        print(f"\nno profile: {', '.join(others)}   (deep-research-web login <name>)")
    if order:
        print(f"switch would pick: {order[0]}")
    else:
        reset = earliest_reset(verdicts)
        tail = f"; earliest window reopens {reset}" if reset else ""
        print(f"no saved profile has room{tail}")
    return EXIT_OK


LAUNCH_LOCK = ".launch.lock"
WAIT_POLL_S = 60
_sleep, _clock = time.sleep, time.monotonic   # module hooks: tests drive --wait without real time


@contextmanager
def _launch_queue(cfg: Config):
    """Serialises launch DECISIONS: the room check and the launch that uses it happen under one lock,
    so two waiters never both take an account's last slot. Blocking, so waiters queue in arrival order."""
    cfg.runs_root.mkdir(parents=True, exist_ok=True)
    fd = os.open(cfg.runs_root / LAUNCH_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _spread_pick(cfg: Config, live: str | None) -> str | None:
    """Another saved account with room by claude-usage's rule and a free research slot. Never one that
    claude-usage marks priority >= 1 (Louis's own accounts are kept out of automatic use)."""
    try:
        accounts = read_usage()
        order, _ = pick_all(accounts, saved_profiles(cfg.profiles), live, cfg.model)
    except (UsageUnavailable, AttributeError, TypeError, KeyError):
        return None
    reserved = {a.get("name") for a in accounts if (a.get("priority") or 0) >= 1}
    for name in order:
        if name not in reserved and len(running_on(cfg.runs_root, name, live)) < cfg.max_per_account:
            return name
    return None


def _choose_account(cfg: Config, force: bool) -> tuple[str | None, list[RunRecord]]:
    """(the account to launch on, the runs filling it when it has NO room). The live account when it has
    a free slot; with cfg.spread, another work account that does; otherwise the live one, full."""
    live = live_name(cfg.profile, cfg.profiles)
    busy = running_on(cfg.runs_root, live, live)
    if force or len(busy) < cfg.max_per_account:
        return live, []
    if cfg.spread:
        other = _spread_pick(cfg, live)
        if other:
            return other, []
    return live, busy


def cmd_launch(args, cfg: Config) -> int:
    model = args.model or cfg.model
    project = None if args.no_project else (args.project or cfg.project)
    with _launch_queue(cfg):
        deadline = _clock() + 60 * max(0, int(getattr(args, "wait", 0) or 0))
        while True:
            chosen, busy = _choose_account(cfg, args.force)
            if not busy or _clock() >= deadline:
                break
            _sleep(WAIT_POLL_S)
        if busy:
            ids = ", ".join(r.run_id for r in busy)
            print(f"a run is already in flight: account {chosen or 'live'} holds {len(busy)} of its "
                  f"{cfg.max_per_account} ({ids}); wait (launch --wait) or pass --force")
            log("WARNING", f"deep-research: cap reached on {chosen} ({ids}); offered force or wait")
            return EXIT_PREFLIGHT
        if chosen != live_name(cfg.profile, cfg.profiles):
            print(f"the live account is full; launching on {chosen} (DEEP_RESEARCH_WEB_SPREAD=1)")
            log("INFO", f"deep-research: spread a launch onto {chosen}")
        return _launch_on(args, cfg, model, project, chosen)


def _launch_on(args, cfg: Config, model: str, project: str | None, chosen: str | None) -> int:
    def attempt(account: str | None = None) -> tuple[int, str | None]:
        # The account is read once here and passed into launch_run, never re-read: a
        # concurrent switch between opening the profile and writing the run record must
        # not attribute the run to an account it never ran on.
        account = account or live_name(cfg.profile, cfg.profiles)
        profile = (cfg.profiles / account) if account else cfg.profile
        with _open_session(profile) as s:
            s.require_login()
            code = launch_run(Client(s.api), cfg, Path(args.charter), model, project, args.force, account=account, name=args.name)
        return code, account

    code, refused = attempt(chosen)
    if code != EXIT_USAGE:
        return code
    # The live account's window refused the launch (either usage-block path of launch_run).
    # Walk the saved accounts that have room, most room first, switching and relaunching until
    # one takes it. An account that refuses is never asked again in this launch, whatever its
    # meters say. Nothing else ever switches: a flag, a preflight refusal or a challenge ends
    # the launch on the account it happened on.
    tried = {refused} if refused else set()
    while True:
        order, _ = _pick_candidates(cfg, refused, frozenset(tried))
        if not order:
            return EXIT_USAGE
        choice = order[0]
        try:
            switch_to(cfg.profile, cfg.profiles, choice)
        except ProfileError as exc:
            print(f"could not switch to {choice}: {exc}")
            return EXIT_USAGE
        print(f"{refused or 'the live profile'} refused on usage; retrying the launch on {choice}")
        log("WARNING", f"deep-research: {refused or 'live'} usage-refused; switched to {choice}")
        code, account = attempt()
        if code != EXIT_USAGE:
            return code
        refused = account or choice
        tried.add(refused)


def cmd_status(args, cfg: Config) -> int:
    if args.run_id:
        one = find_run(cfg.runs_root, args.run_id)
        runs = [one] if one is not None else []
    else:
        runs = find_runs(cfg.runs_root)
    if not runs:
        print(f"no run named {args.run_id}" if args.run_id else "no runs found")
        return EXIT_PROBLEM
    live = [r for r in runs if r.status == RunStatus.RUNNING.value]
    states: dict[str, str] = {}
    if live:
        for profile, group in group_by_profile(cfg.profile, cfg.profiles, live).items():
            with _open_session(profile) as s:
                s.require_login()
                client = Client(s.api)
                for r in group:
                    state = classify(client.conversation(r.org_uuid, r.conversation_uuid)).state
                    # EMPTY only means nothing has landed yet: after launch the engagement
                    # check guarantees a research task, so this is not a design-vocabulary state.
                    states[r.run_id] = RunStatus.RUNNING.value if state is ThreadState.EMPTY else state.value
                    # Persist needs-reply and nothing else. Such a run would otherwise age into
                    # stale after 90 minutes while it is really waiting on Louis. DONE stays the
                    # watcher's to write: it only polls runs marked running, so a done written
                    # here would steal the auto-collect and the Telegram ping with it.
                    if state is ThreadState.NEEDS_REPLY:
                        update_run(Path(r.out_dir), status=RunStatus.NEEDS_REPLY.value,
                                   reason=failure_reason(state))
    for r in runs:
        shown = states.get(r.run_id, r.status)
        print(f"{r.run_id}  {shown}  {r.chat_url}")
    return EXIT_OK


def cmd_collect(args, cfg: Config) -> int:
    rec = find_run(cfg.runs_root, args.run_id)
    if rec is None:
        print(f"no run named {args.run_id}")
        return EXIT_PROBLEM
    with collect_lock(Path(rec.out_dir)) as mine:
        if not mine:
            print(f"run {rec.run_id} is being collected by another process (the watcher); it will land on its own")
            return EXIT_RUNNING
        with _open_session(run_profile(cfg.profile, cfg.profiles, rec.account)) as s:
            s.require_login()
            conversation = fetch_conversation(Client(s.api), rec)
        # Grading fetches third-party pages; the browser is already closed by here.
        code, _ = collect_conversation(rec, conversation, verify=not args.no_verify, archive_root=cfg.archive_root)
    return code


def watcher_collect(cfg: Config) -> Callable[[RunRecord, dict], tuple[int, dict[str, int]]]:
    """The collect the watcher runs after a fetch: same path as the CLI, same archive root."""
    return lambda rec, conv: collect_conversation(rec, conv, archive_root=cfg.archive_root)


def cmd_archive(args, cfg: Config) -> int:
    if cfg.archive_root is None:
        print("no archive root configured: set DEEP_RESEARCH_WEB_ARCHIVE_ROOT in the env file")
        return EXIT_PROBLEM
    rec = find_run(cfg.runs_root, args.run_id)
    if rec is None:
        print(f"no run named {args.run_id}")
        return EXIT_PROBLEM
    if rec.status != RunStatus.DONE.value or not (Path(rec.out_dir) / REPORT_NAME).exists():
        print(f"{rec.run_id} is not collected ({rec.status}); collect it first")
        return EXIT_PROBLEM
    try:
        dest = archive_run(rec, cfg.archive_root, name=args.name)
    except ArchiveError as exc:
        print(str(exc))
        return EXIT_PROBLEM
    if args.name and args.name != rec.name:
        update_run(Path(rec.out_dir), name=args.name)
    print(f"archived: {dest}")
    return EXIT_OK


def cmd_check_claims(args, cfg: Config, runner=subprocess.run) -> int:
    """Opt-in only: nothing calls this but the user. A headless verifier re-reads the report against primary pages."""
    rec = find_run(cfg.runs_root, args.run_id)
    if rec is None:
        print(f"no run named {args.run_id}")
        return EXIT_PROBLEM
    if rec.status != RunStatus.DONE.value or not (Path(rec.out_dir) / REPORT_NAME).exists():
        print(f"{rec.run_id} is not collected ({rec.status}); collect it first")
        return EXIT_PROBLEM
    model = args.model or cfg.model
    print(f"checking {rec.run_id} with {model}; this fetches primary pages and can take a while")
    try:
        written = check_claims(rec, model, runner=runner, timeout_s=int(args.timeout) * 60)
    except ClaimsError as exc:
        print(f"claims check failed: {exc}")
        log("WARNING", f"deep-research: claims check failed for {rec.run_id}: {exc}")
        return EXIT_PROBLEM
    data = json.loads(written.read_text(encoding="utf-8"))
    print(f"  verdicts: {verdict_counts(data['claims'])}")
    for line in data["summary"]["refuted_or_materially_different"]:
        print(f"  refuted or different: {line}")
    for line in data["summary"]["unreachable"]:
        print(f"  unreachable: {line}")
    print(f"  most consequential: {data['summary']['most_consequential'].strip() or '(none)'}")
    print(f"  files: {written} and verification.md beside it")
    if cfg.archive_root is not None:
        try:
            print(f"  archived: {archive_run(rec, cfg.archive_root)}")
        except ArchiveError as exc:
            print(f"  archive failed: {exc}")
            return EXIT_PROBLEM
    log("INFO", f"deep-research: claims check of {rec.run_id}: {verdict_counts(data['claims'])}")
    return EXIT_OK


def cmd_stop(args, cfg: Config) -> int:
    rec = find_run(cfg.runs_root, args.run_id)
    if rec is None:
        print(f"no run named {args.run_id}")
        return EXIT_PROBLEM
    if rec.status != RunStatus.RUNNING.value:
        # Nothing is in flight: stopping would only rewrite a settled record as failed.
        print(f"run {rec.run_id} is {rec.status}; nothing to stop")
        return EXIT_OK
    with _open_session(run_profile(cfg.profile, cfg.profiles, rec.account)) as s:
        s.require_login()
        Client(s.api).stop_response(rec.org_uuid, rec.conversation_uuid)
    update_run(Path(rec.out_dir), status=RunStatus.FAILED.value, reason="stopped by user")
    print(f"stopped {rec.run_id}; the conversation stays at {rec.chat_url}")
    return EXIT_OK


def cmd_report(args, cfg: Config) -> int:
    """A collected report as Markdown: stdout by default so it redirects, or --out to a file.

    Disk only, deliberately: collect re-fetches the conversation and then fetches every cited
    page to grade it, so an export that quietly did network I/O would be a trap.
    """
    rec = find_run(cfg.runs_root, args.run_id)
    if rec is None:
        print(f"no run named {args.run_id}")
        return EXIT_PROBLEM
    report = Path(rec.out_dir) / REPORT_NAME
    if not report.exists():
        if rec.status == RunStatus.RUNNING.value:
            print(f"{rec.run_id} is still running; nothing to export yet")
            return EXIT_RUNNING
        print(f"{rec.run_id} is {rec.status} and has no {REPORT_NAME}")
        print(f"  collect it first: deep-research-web collect {rec.run_id}")
        return EXIT_PROBLEM
    content = report.read_text(encoding="utf-8")
    if args.out:
        dest = Path(args.out).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        print(f"wrote {dest}")
        return EXIT_OK
    # Nothing but the report on stdout: `report <id> > file.md` has to be the whole file.
    sys.stdout.write(content)
    return EXIT_OK


def cmd_list(args, cfg: Config) -> int:
    runs = list_all(cfg.runs_root)
    if not runs:
        print("no runs found")
        return EXIT_OK
    for r in runs:
        print(f"{r['run_id']}  {r['engine']}  {r['status']}  {r['link']}")
    return EXIT_OK


def cmd_watch(args, cfg: Config) -> int:
    from datetime import datetime, timezone as _tz

    from .notify import send
    from .watch import watch

    return watch(
        cfg,
        lambda profile: _open_session(profile),
        send,
        watcher_collect(cfg),
        lambda: datetime.now(_tz.utc),
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="deep-research-web", description="Run a research charter as a claude.ai Research conversation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="log a saved profile into claude.ai (email code)")
    p.add_argument("name", help="the claude-usage account name, e.g. work5")
    p.add_argument("--email", help="for an account claude-usage does not store")
    p.add_argument("--headless", action="store_true", help="experimental: headless Chrome is challenged by Cloudflare on this host")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("switch", help="make a saved profile live; no name picks from claude-usage's meters")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_switch)

    p = sub.add_parser("profiles", help="saved profiles, the live one, and each account's meters")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("launch", help="start a Research conversation from a charter")
    p.add_argument("--charter", required=True)
    p.add_argument("--model")
    p.add_argument("--project", help="claude.ai Project name (default from config)")
    p.add_argument("--no-project", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore the per-account research cap")
    p.add_argument("--wait", type=int, default=0, metavar="MINUTES",
                   help="wait up to MINUTES for a free research slot instead of refusing (launches queue in order)")
    p.add_argument("--name", help="explicit archive name, lowercase kebab-case (the library directory becomes <date>-<name>)")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("status", help="show run state")
    p.add_argument("run_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("collect", help="pull the report and grade its citations")
    p.add_argument("run_id")
    p.add_argument("--no-verify", action="store_true", help="skip page fetches; grades come back unchecked")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("archive", help="copy a collected run into the library under DEEP_RESEARCH_WEB_ARCHIVE_ROOT, optionally renamed")
    p.add_argument("run_id")
    p.add_argument("--name", help="explicit archive name; also recorded on the run")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("check-claims", help="opt-in: an independent headless verifier checks the report's claims against primary pages")
    p.add_argument("run_id")
    p.add_argument("--model", help="verifier model (default: the configured model)")
    p.add_argument("--timeout", type=int, default=60, help="minutes before the verifier is given up on (default 60)")
    p.set_defaults(func=cmd_check_claims)

    p = sub.add_parser("stop", help="stop the response of a running run")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("report", help="print a collected report as Markdown, or write it to --out")
    p.add_argument("run_id")
    p.add_argument("--out", help="write the report to this path instead of stdout")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("list", help="list every known claude-web run")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("watch", help="one watcher pass (the systemd timer runs this every 5 min)")
    p.set_defaults(func=cmd_watch)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    try:
        return args.func(args, cfg)
    # BrowserError first: it subclasses SessionError but is a broken browser, not a bad
    # profile, and the design's error table maps it to exit 1 with the Playwright error.
    except BrowserError as exc:
        print(str(exc))
        log("CRITICAL", f"deep-research: {exc}")
        return EXIT_PROBLEM
    except SessionError as exc:  # LoggedOut subclasses SessionError: one clause covers both
        print(str(exc))
        return EXIT_LOGGED_OUT
    except ApiError as exc:
        print(f"claude.ai API error: {exc}")
        log("CRITICAL", f"deep-research: api error: {exc}")
        return EXIT_PROBLEM
    except AmbiguousRunId as exc:
        print(str(exc))
        return EXIT_PROBLEM
    except NotifyError as exc:
        print(f"telegram: {exc}")
        log("CRITICAL", f"deep-research: telegram: {exc}")
        return EXIT_PROBLEM


if __name__ == "__main__":
    sys.exit(main())

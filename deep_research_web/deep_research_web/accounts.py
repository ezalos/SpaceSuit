# ABOUTME: Which claude.ai account the engine should run on, judged from `claude-usage stats --json`.
# ABOUTME: The pick mirrors claude-usage's own failover rule; the reader is the only part with I/O.
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

# claude-usage fails over only to a parked account below 85% on every window; the same bar here.
KEEP_BELOW = 85
# claude-usage's own freshness rule: a meter reading older than this is not room, it is a guess.
STALE_AFTER_MS = 15 * 60 * 1000
PLACEHOLDER_EMAIL = "?"


class UsageUnavailable(RuntimeError):
    pass


def read_usage(run=subprocess.run) -> list[dict]:
    """claude-usage's accounts list, metered live. Raises UsageUnavailable on any failure."""
    try:
        proc = run(["claude-usage", "stats", "--json"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UsageUnavailable(f"claude-usage did not run: {exc}") from exc
    if proc.returncode != 0:
        raise UsageUnavailable(f"claude-usage exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    try:
        accounts = json.loads(proc.stdout)["accounts"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UsageUnavailable(f"claude-usage printed no accounts JSON: {exc}") from exc
    if not isinstance(accounts, list):
        raise UsageUnavailable("claude-usage printed accounts that are not a list")
    return accounts


def email_for(accounts: list[dict], name: str) -> str | None:
    """The account's email, or None when claude-usage has none or only the placeholder "?"."""
    email = next((a.get("email") for a in accounts if a.get("name") == name), None)
    return None if email == PLACEHOLDER_EMAIL else email


@dataclass(frozen=True)
class Verdict:
    name: str
    email: str | None
    session: float | None
    weekly: float | None
    model: float | None
    kept: bool
    why: str
    weekly_reset: str | None = None
    state: str = ""       # the same verdict in two or three words, for the profiles table


def _percent(window: dict | None) -> float | None:
    return None if not window else window.get("percent")


def _model_percent(usage: dict, model: str) -> float | None:
    """The scoped weekly window of the configured model: its label, lowercased, is in the model id."""
    for entry in usage.get("scoped") or []:
        label = str(entry.get("label", "")).lower()
        if label and label in model.lower():
            return entry.get("percent")
    return None


def _fmt_weekly_reset(resets_at) -> str | None:
    """claude-usage's ISO weekly resetsAt as a compact UTC stamp, or the raw value if unparseable."""
    if not resets_at:
        return None
    try:
        dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
    except ValueError:
        return str(resets_at)
    return dt.strftime("%Y-%m-%d %H:%M") + "Z"


def _stale(usage: dict, now: float) -> bool:
    at = usage.get("at")
    if at is None:
        return True
    return (now * 1000 - at) > STALE_AFTER_MS


def _over_the_bar(v: dict) -> list[str]:
    """Which of the three windows sit at or above the bar, in reading order."""
    windows = (("5h", v["session"]), ("week", v["weekly"]), ("model", v["model"]))
    return [label for label, p in windows if p is not None and p >= KEEP_BELOW]


def _judge(account: dict, saved: set[str], live: str | None, model: str, now: float,
           tried: frozenset[str] = frozenset()) -> Verdict:
    usage = account.get("usage") or {}
    session, weekly = usage.get("session"), usage.get("weekly")
    name = account.get("name", "")
    v = dict(name=name, email=account.get("email"), session=_percent(session),
             weekly=_percent(weekly), model=_model_percent(usage, model),
             weekly_reset=_fmt_weekly_reset((weekly or {}).get("resetsAt")))
    if name == live:
        return Verdict(**v, kept=False, why="live now", state="live now")
    if name in tried:
        # Refused this launch already: its meters may still read as room, and do not.
        return Verdict(**v, kept=False, why="already refused this launch", state="refused just now")
    if name not in saved:
        return Verdict(**v, kept=False, why=f"no saved profile (deep-research-web login {name})",
                       state="no profile")
    if _stale(usage, now):
        return Verdict(**v, kept=False, why="meter reading older than 15 min: stale is not room",
                       state="stale meter")
    if v["session"] is None or v["weekly"] is None:
        return Verdict(**v, kept=False, why="a window was not read: an unread window is not room",
                       state="unread window")
    if (session or {}).get("locked") or (weekly or {}).get("locked"):
        return Verdict(**v, kept=False, why="a window is locked", state="locked window")
    over = _over_the_bar(v)
    if over:
        return Verdict(**v, kept=False, why=f"a window is at or above {KEEP_BELOW}%",
                       state=f"{'+'.join(over)} at {KEEP_BELOW}%+")
    return Verdict(**v, kept=True, why="room", state="room")


def pick_all(
    accounts: list[dict], saved: set[str], live: str | None, model: str,
    tried: frozenset[str] | set[str] = frozenset(),
    now: Callable[[], float] = time.time,
) -> tuple[list[str], list[Verdict]]:
    """Every saved account with room, most room first, and a verdict for each one considered.

    `tried` names accounts this launch already asked: they are dropped whatever their meters
    say, because a refusal is newer evidence than any reading. A row carrying "live": true is
    claude-usage's synthetic entry for whatever unstored account is currently active in the
    terminal; it names no saved profile and is dropped before judging, not merely marked
    dropped, so it never appears in the verdict list.
    """
    when = now()
    metered = [a for a in accounts if a.get("provider", "anthropic") == "anthropic"]
    known = {a.get("name") for a in metered}  # a live-only name is still known: never "no such account"
    judged = [a for a in metered if not a.get("live")]
    verdicts = [_judge(a, saved, live, model, when, frozenset(tried)) for a in judged]
    verdicts += [Verdict(n, None, None, None, None, False,
                         "claude-usage stores no anthropic account by this name", state="not metered")
                 for n in sorted(saved - known)]
    kept = sorted((v for v in verdicts if v.kept), key=lambda v: (v.weekly, v.session))
    return [v.name for v in kept], verdicts


def pick(
    accounts: list[dict], saved: set[str], live: str | None, model: str,
    now: Callable[[], float] = time.time,
) -> tuple[str | None, list[Verdict]]:
    """The saved account with the most weekly room, and a verdict for every account considered."""
    order, verdicts = pick_all(accounts, saved, live, model, now=now)
    return (order[0] if order else None), verdicts


def _reset_order(stamp: str) -> tuple[int, str]:
    """Sort key for a formatted reset stamp: readable ones by time, unreadable ones last.

    `_fmt_weekly_reset` passes a stamp it cannot parse straight through, and a plain text
    sort ranks such a value by its first character rather than by when it happens.
    """
    try:
        datetime.strptime(stamp, "%Y-%m-%d %H:%MZ")
    except ValueError:
        return (1, stamp)
    return (0, stamp)


def earliest_reset(verdicts: list[Verdict]) -> str | None:
    """The soonest weekly reset among the accounts that have no room, so an exit can name it."""
    stamps = sorted((v.weekly_reset for v in verdicts if not v.kept and v.weekly_reset), key=_reset_order)
    return stamps[0] if stamps else None


def profiles_table(verdicts: list[Verdict], live: str | None, saved: set[str]) -> list[str]:
    """The saved profiles as an aligned table: header first, the live profile marked with an arrow."""
    rows = [v for v in verdicts if v.name in saved]
    rows.sort(key=lambda v: (v.name != live, v.name))
    name_w = max([len(v.name) for v in rows] + [len("name")])
    mail_w = max([len(v.email or "-") for v in rows] + [len("account")])

    def pct(p):
        return "?" if p is None else f"{p:g}%"

    lines = [f"   {'name':<{name_w}}  {'account':<{mail_w}}  {'5h':>5}  {'week':>5}  "
             f"{'model':>5}  {'week resets':<17}  state"]
    for v in rows:
        mark = "-> " if v.name == live else "   "
        lines.append(f"{mark}{v.name:<{name_w}}  {v.email or '-':<{mail_w}}  {pct(v.session):>5}  "
                     f"{pct(v.weekly):>5}  {pct(v.model):>5}  {v.weekly_reset or '-':<17}  {v.state}")
    return lines


def verdict_line(v: Verdict, chosen: bool) -> str:
    def pct(p):
        return "?" if p is None else f"{p:g}%"
    weekly = pct(v.weekly) + (f" (resets {v.weekly_reset})" if v.weekly_reset else "")
    meters = f"session {pct(v.session)}  weekly {weekly}  model {pct(v.model)}"
    return f"{'->' if chosen else '  '} {v.name:<8} {v.email or '-':<28} {meters}  {'kept' if v.kept else 'dropped'}: {v.why}"

# ABOUTME: Tests for the account pick over claude-usage's JSON: every keep/drop rule and the reader.
# ABOUTME: Pure: fixture dicts shaped like `claude-usage stats --json`, a fake subprocess runner.
import json
import subprocess
import time

import pytest

from deep_research_web.accounts import (
    earliest_reset, pick_all, profiles_table,
    KEEP_BELOW, UsageUnavailable, email_for, pick, read_usage, verdict_line,
)

MODEL = "claude-fable-5-1"


def acct(name, session=10, weekly=10, fable=None, locked=None, provider="anthropic",
         at=None, live=False, weekly_reset=None):
    usage = {
        "at": int(time.time() * 1000) if at is None else at,
        "session": None if session is None else {"percent": session, "locked": locked},
        "weekly": None if weekly is None else {"percent": weekly, "locked": None, "resetsAt": weekly_reset},
        "scoped": [] if fable is None else [{"label": "Fable", "percent": fable}],
    }
    row = {"name": name, "email": f"{name}@example.test", "provider": provider, "usage": usage}
    if live:
        row["live"] = True
    return row


def test_picks_the_lowest_weekly_among_saved_profiles():
    accounts = [acct("a", weekly=40), acct("b", weekly=11), acct("c", weekly=5)]
    choice, verdicts = pick(accounts, saved={"a", "b"}, live=None, model=MODEL)
    assert choice == "b"  # c has the most room but no saved profile
    assert next(v for v in verdicts if v.name == "c").why.startswith("no saved profile")


def test_never_picks_the_live_account():
    choice, _ = pick([acct("a", weekly=1), acct("b", weekly=50)], saved={"a", "b"}, live="a", model=MODEL)
    assert choice == "b"


def test_drops_an_account_at_the_threshold_on_any_window():
    accounts = [acct("s", session=KEEP_BELOW), acct("w", weekly=KEEP_BELOW), acct("f", fable=KEEP_BELOW),
                acct("ok", session=84, weekly=84, fable=84)]
    choice, verdicts = pick(accounts, saved={"s", "w", "f", "ok"}, live=None, model=MODEL)
    assert choice == "ok"
    assert all(not v.kept for v in verdicts if v.name != "ok")


def test_an_unread_window_is_not_room():
    choice, verdicts = pick([acct("a", session=None)], saved={"a"}, live=None, model=MODEL)
    assert choice is None and "not read" in verdicts[0].why


def test_a_locked_window_is_not_room():
    choice, verdicts = pick([acct("a", locked=True)], saved={"a"}, live=None, model=MODEL)
    assert choice is None and "locked" in verdicts[0].why


def test_a_scoped_entry_for_another_model_is_ignored():
    choice, _ = pick([acct("a", fable=99)], saved={"a"}, live=None, model="claude-opus-5")
    assert choice == "a"


def test_ties_on_weekly_go_to_the_lower_session():
    choice, _ = pick([acct("a", session=30, weekly=20), acct("b", session=5, weekly=20)],
                     saved={"a", "b"}, live=None, model=MODEL)
    assert choice == "b"


def test_non_anthropic_accounts_are_not_candidates():
    choice, verdicts = pick([acct("g", provider="openai")], saved={"g"}, live=None, model=MODEL)
    assert choice is None
    assert next(v for v in verdicts if v.name == "g").why.startswith("claude-usage stores no")


def test_a_saved_profile_claude_usage_does_not_meter_is_listed_not_kept():
    choice, verdicts = pick([], saved={"ghost"}, live=None, model=MODEL)
    assert choice is None and verdicts[0].name == "ghost" and not verdicts[0].kept


def test_email_for_names_the_account():
    assert email_for([acct("work5")], "work5") == "work5@example.test"
    assert email_for([acct("work5")], "nope") is None


class _Proc:
    def __init__(self, code, out, err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_read_usage_returns_the_accounts_list():
    out = json.dumps({"at": 1, "accounts": [acct("a")]})
    assert read_usage(run=lambda *a, **k: _Proc(0, out))[0]["name"] == "a"


@pytest.mark.parametrize("proc", [_Proc(1, "", "boom"), _Proc(0, "not json"), _Proc(0, '{"at": 1}')])
def test_read_usage_raises_on_a_bad_answer(proc):
    with pytest.raises(UsageUnavailable):
        read_usage(run=lambda *a, **k: proc)


def test_read_usage_raises_when_the_tool_is_missing():
    def missing(*a, **k):
        raise FileNotFoundError("claude-usage")
    with pytest.raises(UsageUnavailable):
        read_usage(run=missing)


def test_verdict_line_marks_the_choice():
    _, [v] = pick([acct("a", session=45, weekly=11, fable=3)], saved={"a"}, live=None, model=MODEL)
    line = verdict_line(v, chosen=True)
    assert line.startswith("->") and "a@example.test" in line and "weekly 11%" in line and "room" in line


def test_the_live_account_reads_live_now():
    choice, verdicts = pick([acct("a", weekly=1), acct("b", weekly=50)], saved={"a", "b"}, live="a", model=MODEL)
    assert choice == "b"
    assert next(v for v in verdicts if v.name == "a").why == "live now"


def test_a_stale_meter_reading_is_not_room():
    stale_at = int(time.time() * 1000) - 16 * 60 * 1000  # 16 minutes old
    choice, verdicts = pick([acct("a", at=stale_at)], saved={"a"}, live=None, model=MODEL)
    assert choice is None
    assert "stale is not room" in verdicts[0].why


def test_a_missing_at_is_stale():
    row = acct("a")
    del row["usage"]["at"]
    choice, verdicts = pick([row], saved={"a"}, live=None, model=MODEL)
    assert choice is None
    assert "stale is not room" in verdicts[0].why


def test_a_fresh_reading_still_wins_on_an_injected_now():
    at = 1_000_000_000_000  # an arbitrary fixed instant
    choice, verdicts = pick([acct("a", at=at)], saved={"a"}, live=None, model=MODEL,
                            now=lambda: at / 1000 + 60)  # one minute later: still fresh
    assert choice == "a"
    fourteen_minutes_later = at / 1000 + 14 * 60
    choice, _ = pick([acct("a", at=at)], saved={"a"}, live=None, model=MODEL, now=lambda: fourteen_minutes_later)
    assert choice == "a"
    sixteen_minutes_later = at / 1000 + 16 * 60
    choice, _ = pick([acct("a", at=at)], saved={"a"}, live=None, model=MODEL, now=lambda: sixteen_minutes_later)
    assert choice is None


def test_a_live_row_is_dropped_from_the_verdict_list_entirely():
    # claude-usage's synthetic row for the currently active, unstored terminal account:
    # it names no saved profile and must not even be printed as a dropped candidate.
    choice, verdicts = pick([acct("a", live=True), acct("b")], saved={"a", "b"}, live=None, model=MODEL)
    assert choice == "b"
    assert [v.name for v in verdicts] == ["b"]


def test_email_for_the_placeholder_email_returns_none():
    row = {"name": "x", "email": "?", "provider": "anthropic", "usage": {}}
    assert email_for([row], "x") is None


def test_verdict_line_shows_the_weekly_reset_time_when_claude_usage_gives_one():
    accounts = [acct("a", weekly=91, weekly_reset="2026-09-21T17:00:00Z")]
    _, [v] = pick(accounts, saved={"a"}, live=None, model=MODEL)
    line = verdict_line(v, chosen=False)
    assert "weekly 91% (resets 2026-09-21 17:00Z)" in line


def test_verdict_line_keeps_the_plain_weekly_percent_when_there_is_no_reset():
    _, [v] = pick([acct("a", weekly=20)], saved={"a"}, live=None, model=MODEL)
    assert "weekly 20%" in verdict_line(v, chosen=False)


def test_pick_all_orders_every_candidate_by_room():
    accounts = [acct("a", weekly=40), acct("b", weekly=11), acct("c", weekly=25)]
    order, _ = pick_all(accounts, saved={"a", "b", "c"}, live=None, model=MODEL)
    assert order == ["b", "c", "a"]


def test_pick_all_drops_an_account_already_tried():
    accounts = [acct("a", weekly=10), acct("b", weekly=20)]
    order, verdicts = pick_all(accounts, saved={"a", "b"}, live=None, model=MODEL, tried={"a"})
    assert order == ["b"]
    tried = next(v for v in verdicts if v.name == "a")
    assert not tried.kept and "refused" in tried.why and tried.state == "refused just now"


def test_pick_returns_the_first_of_pick_all():
    accounts = [acct("a", weekly=40), acct("b", weekly=11)]
    choice, _ = pick(accounts, saved={"a", "b"}, live=None, model=MODEL)
    order, _ = pick_all(accounts, saved={"a", "b"}, live=None, model=MODEL)
    assert choice == order[0] == "b"


def test_state_names_which_window_is_over_the_bar():
    accounts = [acct("s", session=90), acct("w", weekly=90), acct("m", fable=90),
                acct("both", session=90, weekly=90), acct("ok")]
    _, verdicts = pick_all(accounts, saved={"s", "w", "m", "both", "ok"}, live=None, model=MODEL)
    state = {v.name: v.state for v in verdicts}
    assert state["s"] == "5h at 85%+" and state["w"] == "week at 85%+" and state["m"] == "model at 85%+"
    assert state["both"] == "5h+week at 85%+" and state["ok"] == "room"


def test_state_of_the_other_drop_reasons():
    stale = [{"name": "old", "email": "old@example.test", "provider": "anthropic",
              "usage": {"at": 0, "session": {"percent": 1}, "weekly": {"percent": 1}, "scoped": []}}]
    _, [v] = pick_all(stale, saved={"old"}, live=None, model=MODEL)
    assert v.state == "stale meter"
    _, verdicts = pick_all([acct("live1"), acct("nope")], saved={"live1"}, live="live1", model=MODEL)
    state = {v.name: v.state for v in verdicts}
    assert state["live1"] == "live now" and state["nope"] == "no profile"


def test_profiles_table_shows_only_saved_profiles_and_marks_the_live_one():
    _, verdicts = pick_all([acct("a", session=96, weekly=22, fable=6), acct("b", weekly=100), acct("c")],
                           saved={"a", "b"}, live="a", model=MODEL)
    lines = profiles_table(verdicts, live="a", saved={"a", "b"})
    assert lines[0].split() == ["name", "account", "5h", "week", "model", "week", "resets", "state"]
    assert [l.split()[0] for l in lines[1:]] == ["->", "b"]  # the live one first, marked
    assert "96%" in lines[1] and "live now" in lines[1]
    assert not any("c" == l.split()[0] for l in lines)


def test_earliest_reset_is_the_soonest_among_accounts_without_room():
    late = acct("late", weekly=100); late["usage"]["weekly"]["resetsAt"] = "2026-09-25T04:00:00+00:00"
    soon = acct("soon", weekly=100); soon["usage"]["weekly"]["resetsAt"] = "2026-09-22T12:00:00+00:00"
    _, verdicts = pick_all([late, soon], saved={"late", "soon"}, live=None, model=MODEL)
    assert earliest_reset(verdicts) == "2026-09-22 12:00Z"
    _, none = pick_all([acct("a")], saved={"a"}, live=None, model=MODEL)
    assert earliest_reset(none) is None


def test_earliest_reset_puts_an_unreadable_stamp_last():
    # _fmt_weekly_reset passes a stamp it cannot parse straight through, and a plain text
    # sort then ranks "1794470400000" before any "2026-..." date. Chronology decides instead.
    raw = acct("raw", weekly=100); raw["usage"]["weekly"]["resetsAt"] = "1794470400000"
    iso = acct("iso", weekly=100); iso["usage"]["weekly"]["resetsAt"] = "2026-09-22T12:00:00+00:00"
    _, verdicts = pick_all([raw, iso], saved={"raw", "iso"}, live=None, model=MODEL)
    assert earliest_reset(verdicts) == "2026-09-22 12:00Z"
    _, only_raw = pick_all([raw], saved={"raw"}, live=None, model=MODEL)
    assert earliest_reset(only_raw) == "1794470400000"   # still reported when it is all there is


def test_profiles_table_renders_a_saved_profile_claude_usage_does_not_meter():
    _, verdicts = pick_all([acct("known", weekly=10)], saved={"known", "ghost"}, live="known", model=MODEL)
    lines = profiles_table(verdicts, live="known", saved={"known", "ghost"})
    ghost = next(l for l in lines if l.split()[0] == "ghost")
    assert ghost.split()[1] == "-" and ghost.count("?") == 3 and "not metered" in ghost
    # Every row's 5-hour figure ends in the same column as the header's "5h", ghost included.
    last = lines[0].index("5h") + 1
    assert all(line[last] != " " for line in lines[1:])

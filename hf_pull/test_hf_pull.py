#!/usr/bin/env python3
# ABOUTME: Tests for hf_pull: checksums must match git/sha256sum, the night window must be
# ABOUTME: judged in the LINE's timezone never this machine's, and worker_plan() must never
# ABOUTME: let per-worker rate * job count exceed the configured aggregate. No network, no mocks.
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hf_pull  # noqa: E402


def test_git_blob_sha1_matches_git():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b'{"a": 1}\n' * 100)
    try:
        want = subprocess.run(["git", "hash-object", f.name], capture_output=True, text=True, check=True).stdout.strip()
        assert hf_pull.digest(f.name, lfs=False, size=os.path.getsize(f.name)) == want
    finally:
        os.unlink(f.name)


def test_lfs_sha256_matches_hashlib():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(os.urandom(3 << 20))
    try:
        want = hashlib.sha256(open(f.name, "rb").read()).hexdigest()
        assert hf_pull.digest(f.name, lfs=True, size=os.path.getsize(f.name)) == want
    finally:
        os.unlink(f.name)


def test_parse_rate():
    assert hf_pull.parse_rate("25M") == 25 * 1024**2
    assert hf_pull.parse_rate("800k") == 800 * 1024
    assert hf_pull.parse_rate("1g") == 1024**3
    assert hf_pull.parse_rate("4096") == 4096


# --- concurrency: the aggregate-cap invariant --------------------------------
# The bug these guard: --limit-rate caps ONE curl. Running N of them at the configured
# rate multiplies aggregate bandwidth by N -- this is the exact 254 GB / 73-outage
# incident. Every worker_plan() output, for any (rate, jobs), must keep
# per_worker * workers at or under the configured aggregate. Never weaken this.


def test_worker_plan_never_exceeds_the_aggregate_cap():
    for rate in (hf_pull.parse_rate("50M"), hf_pull.parse_rate("10M"), hf_pull.parse_rate("800k"), 1, 5, 12345):
        for jobs in (1, 2, 8, 16, 64, 1000, 1_000_000):
            workers, per_worker = hf_pull.worker_plan(rate, jobs)
            assert workers >= 1
            assert per_worker >= 1
            assert per_worker * workers <= rate, (rate, jobs, workers, per_worker)


def test_worker_plan_matches_the_worked_example():
    # 50M aggregate over 8 jobs floors to 6M/worker (50/8 = 6.25) -- the numbers used
    # in the README and the startup line ("8 jobs x 6M").
    rate = hf_pull.parse_rate("50M")
    workers, per_worker = hf_pull.worker_plan(rate, 8)
    assert workers == 8
    assert per_worker == rate // 8
    assert hf_pull.fmt_rate(per_worker) == "6M"


def test_worker_plan_with_one_job_reproduces_the_old_single_curl_behaviour():
    rate = hf_pull.parse_rate("50M")
    workers, per_worker = hf_pull.worker_plan(rate, 1)
    assert (workers, per_worker) == (1, rate)


def test_worker_plan_shrinks_jobs_instead_of_flooring_the_rate_to_zero():
    # A silly --jobs relative to a tiny rate must not push any worker's --limit-rate
    # toward 0 -- curl treats --limit-rate 0 as UNLIMITED, which is the failure mode
    # this whole tool exists to prevent.
    rate = hf_pull.parse_rate("800k")
    workers, per_worker = hf_pull.worker_plan(rate, 1000)
    assert workers < 1000
    assert per_worker > 0
    assert per_worker * workers <= rate


def test_fmt_rate():
    assert hf_pull.fmt_rate(50 * 1024**2) == "50M"
    assert hf_pull.fmt_rate(6 * 1024**2) == "6M"
    assert hf_pull.fmt_rate(800 * 1024) == "800k"
    assert hf_pull.fmt_rate(512) == "512"


def test_curl_cmd_places_the_given_rate_after_limit_rate():
    cmd = hf_pull.curl_cmd("/tmp/x.curlrc", 6553600, "/tmp/out.part")
    assert cmd[cmd.index("--limit-rate") + 1] == "6553600"
    assert cmd[cmd.index("-K") + 1] == "/tmp/x.curlrc"
    assert cmd[cmd.index("-o") + 1] == "/tmp/out.part"
    assert "-C" in cmd and cmd[cmd.index("-C") + 1] == "-"


# --- the night window -------------------------------------------------------
# The bug these guard: the box clock and the line are in different zones ON PURPOSE,
# so "wait until night" read off `date` does the opposite of the right thing.

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
LA = zoneinfo.ZoneInfo("America/Los_Angeles")


def at(zone, y, m, d, hh, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=zone)


def test_window_is_a_half_open_interval():
    assert not hf_pull.window_open(at(PARIS, 2026, 9, 18, 0, 59))
    assert hf_pull.window_open(at(PARIS, 2026, 9, 18, 1, 0))
    assert hf_pull.window_open(at(PARIS, 2026, 9, 18, 6, 59))
    assert not hf_pull.window_open(at(PARIS, 2026, 9, 18, 7, 0))


def test_next_open_today_then_tomorrow():
    n = hf_pull.next_open(at(PARIS, 2026, 9, 18, 0, 30))
    assert (n.date(), n.hour) == (datetime.date(2026, 9, 18), 1)
    n = hf_pull.next_open(at(PARIS, 2026, 9, 18, 20, 0))
    assert (n.date(), n.hour) == (datetime.date(2026, 9, 19), 1)


def test_the_window_falls_in_louis_afternoon_not_his_night():
    opens = at(PARIS, 2026, 9, 18, 1).astimezone(LA)
    shuts = at(PARIS, 2026, 9, 18, 7).astimezone(LA)
    assert (opens.hour, shuts.hour) == (16, 22)
    assert opens.date() == datetime.date(2026, 9, 17)  # the DAY BEFORE


def test_machine_midnight_is_the_worst_hour_at_the_line():
    # 01:00 here -- what an agent reading `date` would call night -- is 10:00 there.
    line = at(LA, 2026, 9, 18, 1).astimezone(PARIS)
    assert line.hour == 10
    assert not hf_pull.window_open(line)


def test_offset_is_not_constant_across_the_dst_gap():
    # EU falls back 2026-10-25, the US not until 2026-11-01: 8 h apart for a week.
    sept = at(PARIS, 2026, 9, 18, 1).astimezone(LA).hour
    gap = at(PARIS, 2026, 10, 28, 1).astimezone(LA).hour
    after = at(PARIS, 2026, 11, 4, 1).astimezone(LA).hour
    assert (sept, gap, after) == (16, 17, 16)


def test_next_open_names_a_real_instant_across_a_transition():
    # 2026-10-25 02:00 CEST -> 01:00 CET in Paris; must not be a phantom time.
    n = hf_pull.next_open(at(PARIS, 2026, 10, 24, 23, 0))
    assert (n.date(), n.hour) == (datetime.date(2026, 10, 25), 1)
    assert n.astimezone(datetime.timezone.utc).hour == 23


def test_fmt_delta():
    assert hf_pull.fmt_delta(datetime.timedelta(minutes=95)) == "1 h 35 m"
    assert hf_pull.fmt_delta(datetime.timedelta(minutes=5)) == "0 h 05 m"


def _with_line_tz(value, fn):
    saved = hf_pull.LINE_TZ
    hf_pull.LINE_TZ = value
    try:
        return fn()
    finally:
        hf_pull.LINE_TZ = saved


def test_report_without_a_zone_is_unknown_not_open():
    text, is_open = _with_line_tz("", hf_pull.window_report)
    assert is_open is None and "HF_PULL_LINE_TZ" in text


def test_report_with_a_bogus_zone_is_unknown():
    assert _with_line_tz("Middle/Earth", hf_pull.window_report)[1] is None


def test_report_names_both_clocks():
    text, is_open = _with_line_tz("Europe/Paris", hf_pull.window_report)
    assert isinstance(is_open, bool)
    assert "Europe/Paris" in text and "this machine" in text


# --- configuration ----------------------------------------------------------


def test_config_file_parses_comments_blanks_and_quotes():
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "env")
        with open(path, "w") as fh:
            fh.write('# a note\n\nHF_PULL_LINE_TZ="Europe/Paris"\nHF_PULL_RATE=25M\n')
        cfg = hf_pull.config_file(path)
        assert cfg["HF_PULL_LINE_TZ"] == "Europe/Paris"
        assert cfg["HF_PULL_RATE"] == "25M"
        assert hf_pull.config_file(os.path.join(d, "absent")) == {}
    finally:
        shutil.rmtree(d)


def test_env_beats_config_beats_fallback():
    cfg = {"HF_PULL_RATE": "10M"}
    os.environ["HF_PULL_RATE"] = "99M"
    try:
        assert hf_pull.setting("HF_PULL_RATE", cfg) == "99M"
    finally:
        del os.environ["HF_PULL_RATE"]
    assert hf_pull.setting("HF_PULL_RATE", cfg) == "10M"
    assert hf_pull.setting("HF_PULL_RATE", {}) == "50M"


def test_fallbacks_match_the_doctrine():
    # 50M = 400 Mb/s, under the 440 Mb/s measured clean on the line 2026-09-10.
    assert hf_pull.FALLBACKS["HF_PULL_RATE"] == "50M"
    assert hf_pull.FALLBACKS["HF_PULL_NIGHT_GB"] == "40"
    assert hf_pull.WINDOW == (1, 7)
    # SpaceSuit is public and a timezone names a place: none may be hardcoded.
    assert hf_pull.FALLBACKS["HF_PULL_LINE_TZ"] == ""
    assert hf_pull.FALLBACKS["HF_PULL_JOBS"] == "8"


def test_jobs_setting_resolves_env_then_config_then_fallback():
    cfg = {"HF_PULL_JOBS": "4"}
    os.environ["HF_PULL_JOBS"] = "16"
    try:
        assert hf_pull.setting("HF_PULL_JOBS", cfg) == "16"
    finally:
        del os.environ["HF_PULL_JOBS"]
    assert hf_pull.setting("HF_PULL_JOBS", cfg) == "4"
    assert hf_pull.setting("HF_PULL_JOBS", {}) == "8"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)

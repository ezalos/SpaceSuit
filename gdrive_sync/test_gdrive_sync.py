#!/usr/bin/env python3
# ABOUTME: Tests for gdrive_sync: the bisync command must carry every safety flag, state transitions
# ABOUTME: must halt/notify exactly as designed. rclone is a recording fake on PATH; no network, no Drive.
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gdrive_sync  # noqa: E402


@pytest.fixture
def env_file(tmp_path):
    state = tmp_path / "state"
    local = tmp_path / "Drive"
    local.mkdir()
    filters = tmp_path / "filters"
    filters.write_text("+ /backup/research/**\n- **\n")
    p = tmp_path / "env"
    p.write_text(textwrap.dedent(f"""\
        # comment
        GDRIVE_REMOTE=gdrive:
        GDRIVE_LOCAL={local}
        GDRIVE_FILTERS={filters}
        GDRIVE_STATE_DIR={state}
        RCLONE_CONFIG_PASS=already-resolved
    """))
    return p


@pytest.fixture
def fake_rclone(tmp_path, monkeypatch):
    """A recording rclone: appends its argv to calls.log, prints FAKE_RCLONE_OUT, exits FAKE_RCLONE_EXIT."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    script = bindir / "rclone"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        printf '%s\\n' "$*" >> "{log}"
        printf '%s\\n' "${{FAKE_RCLONE_OUT:-}}"
        exit "${{FAKE_RCLONE_EXIT:-0}}"
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.delenv("FAKE_RCLONE_EXIT", raising=False)
    monkeypatch.delenv("FAKE_RCLONE_OUT", raising=False)
    return log


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_load_env_parses_and_requires_keys(tmp_path):
    p = tmp_path / "env"
    p.write_text("A=1\n# c\nGDRIVE_REMOTE=\"gdrive:\"\n")
    assert gdrive_sync.load_env(p)["GDRIVE_REMOTE"] == "gdrive:"
    with pytest.raises(SystemExit):
        gdrive_sync.Config.from_env(gdrive_sync.load_env(p))


def test_bisync_argv_carries_every_safety_flag(env_file):
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    argv = gdrive_sync.bisync_argv(cfg, ts="20260914T180000Z")
    s = " ".join(argv)
    assert argv[:4] == ["rclone", "bisync", "gdrive:", str(cfg.local)]
    for flag in (
        f"--filters-file {cfg.filters}", "--create-empty-src-dirs", "--compare size,modtime",
        "--conflict-resolve path1", "--conflict-loser num", "--conflict-suffix conflict",
        "--max-delete 10", "--check-access",
        f"--backup-dir2 {cfg.state_dir}/backup/20260914T180000Z",
        "--resilient", "--recover", "--max-lock 10m", "--bwlimit 25M",
        "--drive-skip-gdocs", "--drive-skip-shortcuts", f"--workdir {cfg.state_dir}/workdir",
    ):
        assert flag in s, flag
    assert "--dry-run" not in s and "--resync" not in s and "--force" not in s


def test_bisync_argv_variants(env_file):
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    assert "--dry-run" in gdrive_sync.bisync_argv(cfg, dry_run=True, ts="t")
    r = " ".join(gdrive_sync.bisync_argv(cfg, resync=True, ts="t"))
    assert "--resync --resync-mode path1" in r
    assert "--force" in gdrive_sync.bisync_argv(cfg, force=True, ts="t")


def test_check_argv_is_read_only_combined(env_file):
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    s = " ".join(gdrive_sync.check_argv(cfg))
    assert s.startswith(f"rclone check gdrive: {cfg.local}")
    assert f"--filter-from {cfg.filters}" in s and "--combined -" in s
    assert "--drive-skip-gdocs" in s and "--drive-skip-shortcuts" in s


def test_diff_prints_legend_and_passes_through(env_file, fake_rclone, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RCLONE_OUT", "* backup/research/a.pdf")
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "1")  # rclone check exits 1 on differences
    rc = gdrive_sync.main(["--env", str(env_file), "diff"], notifier=lambda t: None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "* differ" in out and "* backup/research/a.pdf" in out
    assert calls(fake_rclone)[0].startswith("check gdrive:")


def test_plan_is_bisync_dry_run(env_file, fake_rclone):
    rc = gdrive_sync.main(["--env", str(env_file), "plan"], notifier=lambda t: None)
    assert rc == 0
    c = calls(fake_rclone)[0]
    assert c.startswith("bisync gdrive:") and "--dry-run" in c and "--max-delete 10" in c


@pytest.mark.slow
def test_pass_ref_reexecs_exactly_once_under_secrets(tmp_path, fake_rclone, monkeypatch):
    """A pass:// ref makes the module exec `secrets run -- <self>` once; the child runs rclone. No loop."""
    local = tmp_path / "Drive2"
    local.mkdir()
    filters = tmp_path / "filters2"
    filters.write_text("- **\n")
    env_p = tmp_path / "env2"
    env_p.write_text(
        f"GDRIVE_REMOTE=gdrive:\nGDRIVE_LOCAL={local}\nGDRIVE_FILTERS={filters}\n"
        f"GDRIVE_STATE_DIR={tmp_path / 'state2'}\nRCLONE_CONFIG_PASS=pass://share/item/Secret\n")
    bindir = tmp_path / "bin"
    log = tmp_path / "secrets.log"
    fake_secrets = bindir / "secrets"
    fake_secrets.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        printf '%s\\n' "$*" >> "{log}"
        shift 2
        RCLONE_CONFIG_PASS=resolved-by-fake exec "$@"
    """))
    fake_secrets.chmod(0o755)
    monkeypatch.delenv(gdrive_sync.REEXEC_SENTINEL, raising=False)
    monkeypatch.delenv("RCLONE_CONFIG_PASS", raising=False)
    proc = subprocess.run([sys.executable, gdrive_sync.__file__, "--env", str(env_p), "plan"],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert len(log.read_text().splitlines()) == 1          # exactly one `secrets run`
    assert any(c.startswith("bisync gdrive:") for c in calls(fake_rclone))
    assert "resolved-by-fake" not in proc.stdout + proc.stderr and "pass://" not in proc.stdout + proc.stderr


def test_unresolved_ref_after_secrets_dies_instead_of_looping(env_file, fake_rclone, monkeypatch):
    p = env_file.read_text().replace("RCLONE_CONFIG_PASS=already-resolved", "RCLONE_CONFIG_PASS=pass://x/y/Secret")
    env_file.write_text(p)
    monkeypatch.setenv(gdrive_sync.REEXEC_SENTINEL, "1")
    monkeypatch.setenv("RCLONE_CONFIG_PASS", "pass://x/y/Secret")
    with pytest.raises(SystemExit) as e:
        gdrive_sync.main(["--env", str(env_file), "plan"], notifier=lambda t: None)
    assert e.value.code == 2
    assert calls(fake_rclone) == []


def _state(env_file):
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    return json.loads(cfg.state_file.read_text())


def test_run_success_writes_state_and_no_notify(env_file, fake_rclone):
    sent = []
    rc = gdrive_sync.main(["--env", str(env_file), "run"], notifier=sent.append)
    assert rc == 0 and sent == []
    st = _state(env_file)
    assert st["last_result"] == "ok" and st["consecutive_failures"] == 0 and st["halted"] is False
    assert st["last_success"] is not None
    c = calls(fake_rclone)[0]
    assert c.startswith("bisync gdrive:") and "--dry-run" not in c and "--force" not in c


def test_run_force_passes_force(env_file, fake_rclone):
    gdrive_sync.main(["--env", str(env_file), "run", "--force"], notifier=lambda t: None)
    assert "--force" in calls(fake_rclone)[0]


def test_run_critical_halts_notifies_once_and_skips_until_resync(env_file, fake_rclone, monkeypatch):
    sent = []
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "7")
    monkeypatch.setenv("FAKE_RCLONE_OUT", "ERROR : Bisync critical error: Safety abort: too many deletes")
    assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=sent.append) == 7
    st = _state(env_file)
    assert st["halted"] is True and st["halt_notified"] is True and st["last_result"] == "halted"
    assert len(sent) == 1 and "HALTED" in sent[0] and "gdrive-sync resync" in sent[0] and "too many deletes" in sent[0]
    # second run: bisync is NOT invoked again, no second message
    assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=sent.append) == 7
    assert len(calls(fake_rclone)) == 1 and len(sent) == 1


def test_run_transient_failures_escalate_once_at_three(env_file, fake_rclone, monkeypatch):
    sent = []
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "1")
    for i in range(4):
        assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=sent.append) == 1
    assert _state(env_file)["consecutive_failures"] == 4 and _state(env_file)["halted"] is False
    assert len(sent) == 1 and "3 consecutive" in sent[0]
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "0")
    assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=sent.append) == 0
    assert _state(env_file)["consecutive_failures"] == 0 and len(sent) == 1


def test_run_skips_when_lock_held(env_file, fake_rclone):
    import fcntl
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(cfg.state_dir / "lock", "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=lambda t: None) == 0
        assert calls(fake_rclone) == []
    finally:
        fh.close()


def test_resync_requires_yes_then_clears_halt(env_file, fake_rclone, monkeypatch):
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "7")
    gdrive_sync.main(["--env", str(env_file), "run"], notifier=lambda t: None)
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "0")
    assert gdrive_sync.main(["--env", str(env_file), "resync"], notifier=lambda t: None) == 2
    assert len(calls(fake_rclone)) == 2  # the halted run + the diff shown by resync; no bisync yet
    assert gdrive_sync.main(["--env", str(env_file), "resync", "--yes"], notifier=lambda t: None) == 0
    assert "--resync --resync-mode path1" in calls(fake_rclone)[-1]
    st = _state(env_file)
    assert st["halted"] is False and st["halt_notified"] is False and st["consecutive_failures"] == 0


def test_check_fails_when_halted_stale_or_missing(env_file, fake_rclone, monkeypatch):
    assert gdrive_sync.main(["--env", str(env_file), "check"], notifier=lambda t: None) == 1  # no state yet
    gdrive_sync.main(["--env", str(env_file), "run"], notifier=lambda t: None)
    assert gdrive_sync.main(["--env", str(env_file), "check"], notifier=lambda t: None) == 0
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    st = json.loads(cfg.state_file.read_text())
    st["last_success"] = "2020-01-01T00:00:00+00:00"
    cfg.state_file.write_text(json.dumps(st))
    assert gdrive_sync.main(["--env", str(env_file), "check"], notifier=lambda t: None) == 1
    monkeypatch.setenv("FAKE_RCLONE_EXIT", "7")
    gdrive_sync.main(["--env", str(env_file), "run"], notifier=lambda t: None)
    assert gdrive_sync.main(["--env", str(env_file), "check"], notifier=lambda t: None) == 1


def test_status_prints_state(env_file, fake_rclone, capsys):
    gdrive_sync.main(["--env", str(env_file), "run"], notifier=lambda t: None)
    assert gdrive_sync.main(["--env", str(env_file), "status"], notifier=lambda t: None) == 0
    out = capsys.readouterr().out
    assert "last_success" in out and "halted: False" in out


def test_halt_reason_extracts_the_critical_line():
    out = "INFO  : Synching Path1\nERROR : Bisync critical error: Access test failed\nERROR : Bisync aborted."
    assert gdrive_sync.halt_reason(out) == "Bisync critical error: Access test failed"


def test_halt_notification_is_retried_until_delivered(env_file, fake_rclone, monkeypatch):
    outcomes = iter([False, True])
    sent = []

    def flaky(text):
        sent.append(text)
        return next(outcomes)

    monkeypatch.setenv("FAKE_RCLONE_EXIT", "7")
    assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=flaky) == 7
    assert _state(env_file)["halt_notified"] is False and len(sent) == 1
    assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=flaky) == 7   # halted: retries the send
    assert _state(env_file)["halt_notified"] is True and len(sent) == 2
    assert gdrive_sync.main(["--env", str(env_file), "run"], notifier=flaky) == 7   # delivered: silent
    assert len(sent) == 2 and len(calls(fake_rclone)) == 1


def test_resync_waits_for_an_in_progress_run(env_file, fake_rclone):
    import fcntl
    import threading
    import time
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(cfg.state_dir / "lock", "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    result = {}
    t = threading.Thread(daemon=True, target=lambda: result.setdefault(
        "rc", gdrive_sync.main(["--env", str(env_file), "resync", "--yes"], notifier=lambda m: None)))
    try:
        t.start()
        time.sleep(0.5)
        assert not any(c.startswith("bisync") for c in calls(fake_rclone))   # diff ran, bisync is waiting
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        t.join(timeout=10)
    assert result["rc"] == 0 and any("--resync --resync-mode path1" in c for c in calls(fake_rclone))


def test_included_roots_reads_plus_lines(tmp_path):
    f = tmp_path / "filters"
    f.write_text("# c\n+ /backup/research/**\n+ /notes/**\n- **\n")
    assert gdrive_sync.included_roots(f) == ["backup/research", "notes"]


def test_markers_touches_both_sides(env_file, fake_rclone):
    cfg = gdrive_sync.Config.from_env(gdrive_sync.load_env(env_file))
    assert gdrive_sync.main(["--env", str(env_file), "markers"], notifier=lambda t: None) == 0
    assert (cfg.local / "backup/research/RCLONE_TEST").is_file()
    assert any(c.startswith("touch gdrive:backup/research/RCLONE_TEST") for c in calls(fake_rclone))
    assert all("lsf" in c or "touch" in c for c in calls(fake_rclone))

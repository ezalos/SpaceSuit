#!/usr/bin/env python3
# ABOUTME: Tests for gdrive_sync: the bisync command must carry every safety flag, state transitions
# ABOUTME: must halt/notify exactly as designed. rclone is a recording fake on PATH; no network, no Drive.
import json
import os
import stat
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

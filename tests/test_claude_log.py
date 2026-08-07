"""Tests for the dotfiles/bin/claude-log helper script.

The script appends one structured line per call to a target log file.
Format: 'YYYY-MM-DD skill-name [LEVEL] : message'
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "dotfiles" / "bin" / "claude-log"
LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} (?P<skill>\S+) \[(?P<level>INFO|WARNING|CRITICAL)\] : (?P<msg>.+)$"
)


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path


def run(args, env_overrides=None, log_path=None):
    env = os.environ.copy()
    if log_path is not None:
        env["CLAUDE_LOG_FILE"] = str(log_path)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} not found"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_appends_one_line(log_dir):
    log = log_dir / "lessons.md"
    result = run(["my-skill", "INFO", "hello world"], log_path=log)
    assert result.returncode == 0, result.stderr
    assert log.exists()
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    m = LINE_RE.match(lines[0])
    assert m, f"line not matching format: {lines[0]!r}"
    assert m["skill"] == "my-skill"
    assert m["level"] == "INFO"
    assert m["msg"] == "hello world"


def test_creates_log_file_if_missing(log_dir):
    log = log_dir / "subdir" / "lessons.md"
    assert not log.exists()
    result = run(["my-skill", "INFO", "first"], log_path=log)
    assert result.returncode == 0, result.stderr
    assert log.exists()


def test_rejects_unknown_level(log_dir):
    log = log_dir / "lessons.md"
    result = run(["my-skill", "DEBUG", "should fail"], log_path=log)
    assert result.returncode == 2
    assert "level" in result.stderr.lower()
    assert not log.exists()


def test_rejects_missing_args(log_dir):
    log = log_dir / "lessons.md"
    result = run(["my-skill", "INFO"], log_path=log)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower() or "argument" in result.stderr.lower()


def test_two_calls_two_lines(log_dir):
    log = log_dir / "lessons.md"
    run(["my-skill", "INFO", "first"], log_path=log)
    run(["my-skill", "WARNING", "second"], log_path=log)
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert "[INFO]" in lines[0]
    assert "[WARNING]" in lines[1]


def test_concurrent_calls_no_interleave(log_dir):
    """Hammer the lock with N parallel invocations; verify all lines land cleanly."""
    log = log_dir / "lessons.md"
    procs = []
    N = 20
    for i in range(N):
        env = os.environ.copy()
        env["CLAUDE_LOG_FILE"] = str(log)
        p = subprocess.Popen(
            [str(SCRIPT), "my-skill", "INFO", f"msg-{i}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        procs.append(p)
    for p in procs:
        p.wait()
    lines = log.read_text().splitlines()
    assert len(lines) == N, f"expected {N} lines, got {len(lines)}: {lines!r}"
    for line in lines:
        assert LINE_RE.match(line), f"line not matching format: {line!r}"


def test_default_log_path_is_claude_lessons(tmp_path):
    """Without CLAUDE_LOG_FILE set, default is ~/.claude/lessons.md.

    We override HOME to redirect, so the test doesn't touch the real file.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("CLAUDE_LOG_FILE", None)
    result = subprocess.run(
        [str(SCRIPT), "my-skill", "INFO", "hello"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    expected = fake_home / ".claude" / "lessons.md"
    assert expected.exists()

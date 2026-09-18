"""Tests for the dotfiles/bin/claude-badge tmux status helper.

The script maps tmux panes to the state badge of the Claude Code session
running in each pane, reading Claude Code's own ~/.claude/sessions/<pid>.json.
Each pane arrives as `%id=/dev/tty`; the tty is what makes the match exact,
because pane ids are only unique within one tmux server.
"""
import fcntl
import json
import os
import subprocess
import termios
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "dotfiles" / "bin" / "claude-badge"


def spawn_on_fresh_tty():
    """A live process whose controlling terminal is a new pty, like claude in a pane."""
    master, slave = os.openpty()

    def take_controlling_tty():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        ["sleep", "60"], stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=take_controlling_tty,
    )
    tty = os.ttyname(slave)
    return proc, tty, (master, slave)


@pytest.fixture
def claude_on_tty():
    procs = []

    def make():
        proc, tty, fds = spawn_on_fresh_tty()
        procs.append((proc, fds))
        return proc.pid, tty

    yield make
    for proc, fds in procs:
        proc.kill()
        proc.wait()
        for fd in fds:
            os.close(fd)


@pytest.fixture
def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def write_session(sessions_dir, pid, pane, status):
    # Same shape and key order as Claude Code writes (compact JSON.stringify).
    data = {
        "pid": pid,
        "sessionId": f"s-{pid}-{pane}",
        "cwd": "/tmp",
        "tmux": f"work@2026-09-18-10h00:@7.{pane}",
        "name": "some-session",
        "status": status,
        "statusUpdatedAt": 1789761954398,
    }
    if pane is None:
        del data["tmux"]
    path = sessions_dir / f"{pid}-{pane}.json"
    path.write_text(json.dumps(data, separators=(",", ":")))
    return path


def run(sessions_dir, *panes, procfs=None):
    env = os.environ.copy()
    env["CLAUDE_BADGE_SESSIONS_DIR"] = str(sessions_dir)
    if procfs is not None:
        env["CLAUDE_BADGE_PROCFS"] = str(procfs)
    return subprocess.run(
        [str(SCRIPT), *panes], capture_output=True, text=True, env=env
    )


def test_script_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} not found"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


@pytest.mark.parametrize(
    "status,badge",
    [("busy", "⏳"), ("shell", "⏳"), ("idle", "✅"), ("waiting", "🔴")],
)
def test_status_maps_to_badge(tmp_path, claude_on_tty, status, badge):
    pid, tty = claude_on_tty()
    write_session(tmp_path, pid, "%12", status)
    result = run(tmp_path, f"%12={tty}")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{badge} "


def test_ps_path_without_procfs(tmp_path, claude_on_tty, dead_pid):
    # macOS has no /proc: the script falls back to ps. Same verdicts either way.
    no_procfs = tmp_path / "no-proc"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    pid, tty = claude_on_tty()
    _, other_tty = claude_on_tty()
    write_session(sessions, pid, "%12", "waiting")
    write_session(sessions, dead_pid, "%13", "busy")
    assert run(sessions, f"%12={tty}", procfs=no_procfs).stdout == "🔴 "
    assert run(sessions, f"%12={other_tty}", procfs=no_procfs).stdout == ""
    assert run(sessions, f"%13={tty}", procfs=no_procfs).stdout == ""


def test_tty_nr_encoding_matches_the_kernel(tmp_path, claude_on_tty):
    # The procfs path recomputes the pts device number; os.stat of the tty is the
    # kernel's own answer, so the two must agree for every pty the test creates.
    pid, tty = claude_on_tty()
    rdev = os.stat(tty).st_rdev
    n = int(tty.removeprefix("/dev/pts/"))
    assert (os.major(rdev), os.minor(rdev)) == (136, n)
    stat = Path(f"/proc/{pid}/stat").read_text()
    tty_nr = int(stat.rsplit(")", 1)[1].split()[4])
    assert tty_nr == (n & 0xFF) | (136 << 8) | ((n & ~0xFF) << 12)


def test_unknown_status_reads_as_working(tmp_path, claude_on_tty):
    # Same fail-safe as tls classify.map_activity: never claim a session is
    # stopped or needs the user on a status this script does not know.
    pid, tty = claude_on_tty()
    write_session(tmp_path, pid, "%12", "some-future-status")
    assert run(tmp_path, f"%12={tty}").stdout == "⏳ "


def test_dead_pid_is_ignored(tmp_path, claude_on_tty, dead_pid):
    # A crashed Claude leaves its session file behind; it must not keep a badge.
    _, tty = claude_on_tty()
    write_session(tmp_path, dead_pid, "%12", "busy")
    assert run(tmp_path, f"%12={tty}").stdout == ""


def test_same_pane_id_on_another_server_is_ignored(tmp_path, claude_on_tty):
    # Pane ids restart at %0 on every tmux server. A session in %12 of server A
    # must not badge %12 of server B: its process is on a different tty.
    pid_a, _ = claude_on_tty()
    _, tty_b = claude_on_tty()
    write_session(tmp_path, pid_a, "%12", "waiting")
    assert run(tmp_path, f"%12={tty_b}").stdout == ""


def test_other_panes_are_ignored(tmp_path, claude_on_tty):
    pid, tty = claude_on_tty()
    write_session(tmp_path, pid, "%12", "busy")
    assert run(tmp_path, f"%13={tty}").stdout == ""


def test_pane_id_is_matched_exactly(tmp_path, claude_on_tty):
    # %1 must not match a session living in %12.
    pid, tty = claude_on_tty()
    write_session(tmp_path, pid, "%12", "waiting")
    assert run(tmp_path, f"%1={tty}").stdout == ""


def test_one_badge_per_claude_pane_in_argument_order(tmp_path, claude_on_tty):
    pid_a, tty_a = claude_on_tty()
    pid_b, tty_b = claude_on_tty()
    _, tty_shell = claude_on_tty()
    write_session(tmp_path, pid_a, "%12", "idle")
    write_session(tmp_path, pid_b, "%40", "waiting")
    result = run(tmp_path, f"%40={tty_b}", f"%7={tty_shell}", f"%12={tty_a}")
    assert result.stdout == "🔴✅ "


def test_session_outside_tmux_is_ignored(tmp_path, claude_on_tty):
    pid, tty = claude_on_tty()
    write_session(tmp_path, pid, None, "waiting")
    assert run(tmp_path, f"%12={tty}").stdout == ""


def test_corrupt_file_costs_only_itself(tmp_path, claude_on_tty):
    pid, tty = claude_on_tty()
    (tmp_path / "999.json").write_text("{not json")
    write_session(tmp_path, pid, "%12", "idle")
    result = run(tmp_path, f"%12={tty}")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "✅ "


def test_missing_sessions_dir_prints_nothing(tmp_path):
    result = run(tmp_path / "absent", "%12=/dev/pts/0")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_no_panes_prints_nothing(tmp_path, claude_on_tty):
    pid, _ = claude_on_tty()
    write_session(tmp_path, pid, "%12", "busy")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""

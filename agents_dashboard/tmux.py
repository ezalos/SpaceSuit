# ABOUTME: Enumerate tmux panes and associate each with a Claude Code process.
# ABOUTME: Strictly read-only - this module must never send keys or write to a pane.

from __future__ import annotations

import subprocess
from dataclasses import dataclass

# Trailing fields have fixed positions so the parser can rsplit; the session
# name is first because it is the only field that may itself contain ':'.
PANE_FORMAT = (
    "#{session_name}:#{window_index}:#{pane_index}:#{pane_current_path}"
    ":#{pane_tty}:#{window_activity}:#{session_attached}:#{session_activity}"
    ":#{pane_current_command}"
)
_PANE_FIELDS = 9


@dataclass
class TmuxPane:
    session: str
    window_index: int
    pane_index: int
    cwd: str
    tty: str
    # Appended with defaults so existing positional construction keeps working.
    command: str = ""
    quiet_since: float = 0.0        # epoch seconds, last output in this window
    session_attached: bool = False
    session_activity: float = 0.0   # epoch seconds, last activity in the session


def subprocess_runner(argv: list[str]) -> str:
    """Run a command and return stdout, or '' if it fails for any reason."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def list_sessions(runner=subprocess_runner) -> list[str]:
    out = runner(["tmux", "list-sessions", "-F", "#{session_name}"])
    return [line for line in out.splitlines() if line.strip()]


def list_panes(runner=subprocess_runner) -> list[TmuxPane]:
    out = runner(["tmux", "list-panes", "-a", "-F", PANE_FORMAT])
    panes = []
    for line in out.splitlines():
        # rsplit: session names may contain ':' but the trailing fields cannot.
        parts = line.rsplit(":", _PANE_FIELDS - 1)
        if len(parts) != _PANE_FIELDS:
            continue
        session, window, pane, cwd, tty, activity, attached, sess_activity, command = parts
        try:
            panes.append(
                TmuxPane(
                    session=session,
                    window_index=int(window),
                    pane_index=int(pane),
                    cwd=cwd,
                    tty=tty,
                    command=command,
                    quiet_since=float(activity),
                    session_attached=attached == "1",
                    session_activity=float(sess_activity),
                )
            )
        except ValueError:
            continue
    return panes


def claude_pid_for_tty(tty: str, runner=subprocess_runner) -> int | None:
    """Find a claude process on this tty at any depth.

    `ps -t` lists every process on the terminal regardless of depth, on both Linux
    and macOS. tmux-save.sh learned this the hard way: a `ps -g` plus GNU-only
    `ps --ppid` approach saw only the pane shell on macOS and detected no Claude
    at all.
    """
    out = runner(["ps", "-t", tty.removeprefix("/dev/"), "-o", "pid=,comm="])
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        pid, comm = fields[0], fields[1]
        if comm == "claude" or comm.endswith("/claude"):
            try:
                return int(pid)
            except ValueError:
                continue
    return None


def capture_pane(
    session: str, window_index: int, pane_index: int, lines: int = 30,
    runner=subprocess_runner,
) -> str:
    target = f"{session}:{window_index}.{pane_index}"
    return runner(["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"])


def normalise_tty(tty: str) -> str:
    """tmux reports /dev/pts/5; ps reports pts/5. Normalise to the ps form."""
    return tty.removeprefix("/dev/")


def claude_pids_by_tty(runner=subprocess_runner) -> dict[str, int]:
    """Map every tty running Claude to its pid, in one `ps`.

    Replaces one `ps -t` per pane. Profiled over 30 panes, the per-pane form
    cost 1.048 s - about 60% of a full collection - because each call is a
    process spawn. One `ps -eo` costs roughly 0.03 s.

    Keys are bare ttys (`pts/5`), the form `ps` prints. Callers holding a tmux
    tty must pass it through `normalise_tty` first.
    """
    out = runner(["ps", "-eo", "pid=,tty=,comm="])
    found: dict[str, int] = {}
    for line in out.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3:
            continue
        pid_text, tty, comm = fields[0], fields[1], fields[2].strip()
        if comm != "claude" and not comm.endswith("/claude"):
            continue
        if tty in found:
            continue  # first wins, matching claude_pid_for_tty
        try:
            found[tty] = int(pid_text)
        except ValueError:
            continue
    return found

# ABOUTME: Maps on-disk facts plus session liveness onto a run state.
# ABOUTME: Pure so every branch is testable without spawning a background session.
from __future__ import annotations

from enum import Enum
from pathlib import Path

DONE_SENTINEL = "DONE"
REPORT_NAME = "report.md"
SOURCES_NAME = "sources.md"
RESULT_NAME = "run-result.json"


class RunState(str, Enum):
    RUNNING = "running"
    DONE = "done"
    INCOMPLETE = "incomplete"
    LOST = "lost"


def resolve_state(out_dir: Path, session_alive: bool) -> RunState:
    # The sentinel is written last, so its presence is the only completion signal
    # that does not race a partially written report.
    if (out_dir / DONE_SENTINEL).exists():
        return RunState.DONE
    if session_alive:
        return RunState.RUNNING
    if (out_dir / REPORT_NAME).exists():
        return RunState.INCOMPLETE
    return RunState.LOST

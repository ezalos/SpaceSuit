# ABOUTME: Read Claude Code's own per-session status files under ~/.claude/sessions/.
# ABOUTME: These are first-party: status is idle|busy|shell, so liveness needs no scraping.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class ClaudeSession:
    pid: int
    session_id: str
    cwd: str
    status: str
    status_updated_at: float  # epoch seconds
    name: str


def load_all(sessions_dir: Path = DEFAULT_SESSIONS_DIR) -> dict[int, ClaudeSession]:
    """Load every session file, keyed by pid. Corrupt files are skipped."""
    sessions: dict[int, ClaudeSession] = {}
    try:
        paths = sorted(sessions_dir.glob("*.json"))
    except OSError:
        return sessions

    for path in paths:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            # Valid JSON but not a session object (a bare list, string, ...).
            # Nothing to salvage - skip the file rather than guess a shape.
            continue
        pid = data.get("pid")
        session_id = data.get("sessionId")
        if not isinstance(pid, int) or not session_id:
            continue

        # A single malformed field must cost at most this one session, never
        # the other 24 panes: coerce to a safe default instead of letting a
        # wrong-typed value blow up arithmetic (statusUpdatedAt) or a later
        # dict lookup (status, in classify.map_activity). The session still
        # shows up on the dashboard, just with a neutral/default value for
        # the one field that was bad.
        raw_status_updated_at = data.get("statusUpdatedAt")
        if not isinstance(raw_status_updated_at, (int, float)):
            raw_status_updated_at = 0
        raw_status = data.get("status")
        if not isinstance(raw_status, str):
            raw_status = ""

        sessions[pid] = ClaudeSession(
            pid=pid,
            session_id=session_id,
            cwd=data.get("cwd") or "",
            status=raw_status or "",
            # Claude Code writes milliseconds; the rest of this package uses seconds.
            status_updated_at=raw_status_updated_at / 1000.0,
            name=data.get("name") or "",
        )
    return sessions


def project_slug(cwd: str) -> str:
    """Claude Code slugs a project dir by replacing '/' AND '_' with '-'.

    Derived empirically against all 24 live sessions. Handling only '/' scored
    20/24: it silently lost every session whose path contained an underscore.
    """
    return cwd.replace("/", "-").replace("_", "-")


def transcript_path(
    session: ClaudeSession, projects_dir: Path = DEFAULT_PROJECTS_DIR
) -> Path:
    """The slug-derived transcript path. Pure - does not touch the filesystem."""
    return projects_dir / project_slug(session.cwd) / f"{session.session_id}.jsonl"


def find_transcript(
    session: ClaudeSession, projects_dir: Path = DEFAULT_PROJECTS_DIR
) -> Path:
    """Locate a session's transcript, falling back to a lookup by session id.

    No live project directory contains a character outside [a-zA-Z0-9-], so the
    evidence cannot tell "slash and underscore" apart from a broader "every
    non-alphanumeric becomes a dash". Rather than guess, fall back to globbing
    for the session id, which is a UUID and globally unique: that lookup is
    right whatever the slug scheme is, and self-heals if it ever changes.

    Returns the slug path when nothing is found, so callers see a non-existent
    path and degrade to an unknown phase rather than raising.
    """
    direct = transcript_path(session, projects_dir)
    if direct.exists():
        return direct
    try:
        for candidate in projects_dir.glob(f"*/{session.session_id}.jsonl"):
            return candidate
    except OSError:
        pass
    return direct

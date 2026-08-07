# ABOUTME: Maps a pane cwd to its Claude project dir and locates transcript files.
# ABOUTME: Slug rule mirrors tmux-restore.sh; a glob fallback covers any path it gets wrong.
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")


def project_slug(cwd: str) -> str:
    """Encode a working directory the way Claude Code names its project dirs.

    Every non-alphanumeric character becomes '-'. Handling only '/' misses
    underscore dirs (monorepo_quater), which once made their saved session IDs
    look absent and sent trestore to the picker.
    """
    return _NON_ALNUM.sub("-", cwd)


def transcript_path(cwd: str, session_id: str, projects_dir: Path):
    """Locate one conversation's .jsonl, or None if it is gone.

    Tries the slugged dir first, then falls back to a glob across every project
    dir. The fallback means an imperfect slug rule degrades to a slower lookup
    instead of a wrong 'conversation missing' verdict.
    """
    direct = Path(projects_dir) / project_slug(cwd) / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    for found in Path(projects_dir).glob(f"*/{session_id}.jsonl"):
        return found
    return None


def list_transcripts(cwd: str, projects_dir: Path) -> list:
    """Every conversation in this cwd's project dir, as (session_id, mtime)."""
    d = Path(projects_dir) / project_slug(cwd)
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("*.jsonl"):
        try:
            out.append((f.stem, f.stat().st_mtime))
        except OSError:
            continue
    return sorted(out)

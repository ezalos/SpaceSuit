# ABOUTME: Resolves a human-readable title for a Claude conversation from its .jsonl.
# ABOUTME: Chain is ai-title -> last-prompt -> first user message -> "(untitled)", memoised.
from __future__ import annotations

import json
from pathlib import Path

from .claude_paths import transcript_path

UNTITLED = "(untitled)"

# Reading a whole transcript is not an option: they reach 84MB. Titles live in
# small records, and ai-title/last-prompt are rewritten as the session grows, so
# the newest one is at the end. Read a tail window and scan it backwards.
TAIL_BYTES = 256 * 1024


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _tail_lines(path: Path) -> list:
    with path.open("rb") as fh:
        try:
            fh.seek(-TAIL_BYTES, 2)
            chunk = fh.read()
            # A partial first line is likely after seeking into the middle.
            chunk = chunk.split(b"\n", 1)[1] if b"\n" in chunk else b""
        except OSError:
            fh.seek(0)
            chunk = fh.read()
    return chunk.decode("utf-8", "replace").splitlines()


def _scan(lines, wanted_type: str, field: str) -> str:
    """Newest matching record's field, scanning from the end."""
    for line in reversed(lines):
        line = line.strip()
        if not line or wanted_type not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") == wanted_type and rec.get(field):
            return str(rec[field])
    return ""


def _first_user_message(path: Path) -> str:
    """Read forwards from the top; only needed when both title records are absent."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or '"user"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "user":
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        return str(block["text"])
    return ""


class TitleResolver:
    """Titles for conversations, cached by session id for the life of one run."""

    def __init__(self, projects_dir: Path):
        self.projects_dir = Path(projects_dir)
        self._cache = {}

    def title_for(self, cwd: str, session_id: str) -> str:
        if session_id in self._cache:
            return self._cache[session_id]
        title = self._resolve(cwd, session_id)
        self._cache[session_id] = title
        return title

    def _resolve(self, cwd: str, session_id: str) -> str:
        path = transcript_path(cwd, session_id, self.projects_dir)
        if path is None:
            return UNTITLED
        try:
            lines = _tail_lines(path)
        except OSError:
            return UNTITLED
        for wanted_type, field in (("ai-title", "aiTitle"), ("last-prompt", "lastPrompt")):
            found = _scan(lines, wanted_type, field)
            if found:
                return _collapse(found)
        try:
            found = _first_user_message(path)
        except OSError:
            return UNTITLED
        return _collapse(found) if found else UNTITLED

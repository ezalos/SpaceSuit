# ABOUTME: Detect a pending permission prompt or unsent input in captured pane text.
# ABOUTME: The only module coupled to Claude Code's TUI shape; degrades to None, never guesses.

from __future__ import annotations

import re

from .models import WaitingReason

PROMPT_GLYPH = "❯"  # the heavy right-pointing angle used as the input prompt

# Two independent markers, both required, so ordinary prose containing the
# question cannot trigger a false permission flag.
_PERMISSION_QUESTION = re.compile(r"^\s*Do you want to (proceed|create|make)", re.M)
_PERMISSION_CHOICES = re.compile(r"^\s*\D?\s*1\.\s+Yes", re.M)


def _has_permission_prompt(text: str) -> bool:
    return bool(_PERMISSION_QUESTION.search(text) and _PERMISSION_CHOICES.search(text))


def _has_unsent_input(text: str) -> bool:
    """A prompt line carrying text the user never submitted.

    Numbered-choice lines also start with the glyph, so any line whose remainder
    looks like a menu selection is excluded.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(PROMPT_GLYPH):
            continue
        rest = stripped[len(PROMPT_GLYPH):].strip()
        if not rest:
            continue
        if re.match(r"^\d+\.\s", rest):  # a menu choice, not typed input
            continue
        return True
    return False


def scan(pane_text: str) -> WaitingReason | None:
    """Return the TUI-only waiting reason visible in this pane, if any."""
    if not pane_text:
        return None
    if _has_permission_prompt(pane_text):
        return WaitingReason.PERMISSION
    if _has_unsent_input(pane_text):
        return WaitingReason.UNSENT_INPUT
    return None

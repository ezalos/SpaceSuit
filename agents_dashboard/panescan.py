# ABOUTME: Detect unsent input (text typed at the prompt, never submitted) in captured pane text.
# ABOUTME: The only module coupled to Claude Code's TUI shape; degrades to None, never guesses.

from __future__ import annotations

import re

from .models import WaitingReason

PROMPT_GLYPH = "❯"  # the heavy right-pointing angle used as the input prompt

# Permission prompts used to be scraped here too. Claude Code now reports them
# first-party (status "waiting", waitingFor "permission prompt"), which
# collect.py reads; unsent input is the one reason it does not report.


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
    if _has_unsent_input(pane_text):
        return WaitingReason.UNSENT_INPUT
    return None

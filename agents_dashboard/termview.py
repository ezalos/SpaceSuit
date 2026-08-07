# ABOUTME: Render a Snapshot as an aligned, coloured terminal grid for `tls`.
# ABOUTME: Pure - the caller resolves width, colour and clock and passes them in.

from __future__ import annotations

import os
import time
import unicodedata

from .models import (
    Activity,
    PhaseEvidence,
    SessionCard,
    Snapshot,
    WaitingReason,
    WindowRecord,
)

RESET = "\x1b[0m"
BOLD_CYAN = "\x1b[1;36m"
GREEN = "\x1b[32m"
AMBER = "\x1b[33m"
RED = "\x1b[31m"
BLUE = "\x1b[34m"
DIM = "\x1b[2m"
DIM_RED = "\x1b[2;31m"
BRIGHT = "\x1b[1m"

# Idle is deliberately not grey: it IS waiting on Louis, just not blocked, so
# it should register without competing with the warning states.
STATE_STYLE = {
    WaitingReason.PERMISSION: (RED, "⚠ permission"),
    WaitingReason.QUESTION: (AMBER, "⚠ question"),
    WaitingReason.UNSENT_INPUT: (BLUE, "⚠ unsent"),
    WaitingReason.IDLE: (AMBER, "○ idle"),
}
WORKING_STYLE = (GREEN, "◉ working")
NONE_CELL = "·"


def _char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Columns a string occupies, counting CJK and emoji as two.

    Titles come from `aiTitle` and routinely contain both; len() would
    under-count and overflow the row.
    """
    return sum(_char_width(c) for c in text)


def _truncate(text: str, budget: int) -> str:
    if display_width(text) <= budget:
        return text
    out, used = [], 0
    for char in text:
        w = _char_width(char)
        if used + w > budget - 1:
            break
        out.append(char)
        used += w
    return "".join(out) + "…"


def _wrap_text(text: str, first_budget: int, rest_budget: int) -> list[str]:
    """Word-wrap `text` into a first line sized `first_budget` and as many
    `rest_budget`-sized continuation lines as needed, measured in display
    columns (`display_width`, not `len`) so CJK and emoji - which titles
    routinely contain - count as two.

    Wraps on word boundaries. A single word wider than the budget it lands
    on is hard-broken character by character, so one long path or URL can
    never overflow a line.

    The row line only gets a fragment when that's worth having: the first
    word must fit within `first_budget`, *and* `first_budget` must be at
    least 15 columns. A fitting word on a narrower budget still reads as
    an orphan - "Improve" alone, with the rest of the title one line
    below it, looks broken rather than deliberate. When either condition
    fails, the row gets nothing and the whole text starts on the first
    continuation line instead - the same path taken when `first_budget`
    is <= 0.
    """
    words = text.split()
    if not words:
        return [""]

    rest_budget = max(1, rest_budget)
    if first_budget < 15 or display_width(words[0]) > first_budget:
        first_budget = 0
    lines: list[str] = []
    line = ""
    budget = first_budget
    for word in words:
        while True:
            if line:
                candidate = f"{line} {word}"
                if display_width(candidate) <= budget:
                    line = candidate
                    break
                lines.append(line)
                line, budget = "", rest_budget
                continue
            if display_width(word) <= budget:
                line = word
                break
            if budget <= 0:
                lines.append(line)  # emits the empty first line, once
                budget = rest_budget
                continue
            # `word` alone overflows `budget`: hard-break it. The first
            # character is always taken even if it alone exceeds budget,
            # so a pathologically narrow budget still makes progress
            # instead of looping forever on the same word.
            piece, piece_width = "", 0
            for ch in word:
                w = _char_width(ch)
                if piece and piece_width + w > budget:
                    break
                piece += ch
                piece_width += w
                if piece_width >= budget:
                    break
            lines.append(piece)
            word = word[len(piece):]
            budget = rest_budget
    lines.append(line)
    return lines


def _pad(text: str, width: int, right: bool = False) -> str:
    # Clamp before padding: a label ("⚠ permission" is 12 columns wide, one
    # over its 11-wide STATE column) or an unbounded duration string (a pane
    # quiet for decades renders "20075d") must not be allowed to widen its
    # column, or every fixed-width column after it - and the width budget the
    # title truncation depends on - drifts out from under it.
    if display_width(text) > width:
        text = _truncate(text, width)
    gap = max(0, width - display_width(text))
    return (" " * gap + text) if right else (text + " " * gap)


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _colour(text: str, style: str, color: bool) -> str:
    return f"{style}{text}{RESET}" if color and style else text


def _collapse_home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def _waiting_style(age: float) -> str:
    """Warms with age: a five-minute wait and a fifteen-hour one differ."""
    if age >= 86400:
        return DIM_RED
    if age >= 43200:
        return BRIGHT
    return ""


# (name, width, right-aligned). Numbers and durations sit right, labels left.
_BASE_COLUMNS = (("WIN", 5, True), ("CMD", 8, False), ("QUIET", 5, True))
_PHASE_COLUMN = ("PHASE", 8, False)
_TAIL_COLUMNS = (("STATE", 11, False), ("WAITING", 7, True), ("TASKS", 5, True))


def _columns(show_phase: bool):
    return _BASE_COLUMNS + ((_PHASE_COLUMN,) if show_phase else ()) + _TAIL_COLUMNS


def _row(window: WindowRecord, now: float, color: bool, show_phase: bool,
         title_budget: int, cont_budget: int, cont_indent: int) -> list[str]:
    """Build one row by walking `_columns(show_phase)` - the same tuple the
    header reads - rather than repeating each column's width as a literal.
    A column's width and alignment must come from exactly one place: two
    independent copies that happen to agree today can silently drift apart
    the moment either one is edited alone, moving the header without moving
    the rows (or the reverse), and the resulting misalignment would only
    ever surface as "the header text changed", never as its real cause.

    Returns the row's physical lines: the fixed columns plus as much of the
    title/cwd as fits `title_budget`, followed by any continuation lines
    the wrap needed, each indented to `cont_indent` (the CMD column start)
    and wrapped to `cont_budget`.
    """
    target = f"{window.window_index}.{window.pane_index}"
    quiet = _duration(now - window.quiet_since) if window.quiet_since else NONE_CELL
    pane = window.claude

    if pane is None:
        values = {"WIN": target, "CMD": window.command, "QUIET": quiet,
                  "PHASE": NONE_CELL, "STATE": NONE_CELL, "WAITING": NONE_CELL,
                  "TASKS": NONE_CELL}
        cells = [_pad(values[name], width, right) for name, width, right in _columns(show_phase)]
        frags = _wrap_text(_collapse_home(window.cwd), title_budget, cont_budget)
        first = "  ".join(cells) + (("  " + frags[0]) if frags[0] else "")
        rest = [" " * cont_indent + frag for frag in frags[1:]]
        return [_colour(line, DIM, color) for line in [first, *rest]]

    if pane.activity is Activity.WORKING or pane.waiting_reason is None:
        style, label = WORKING_STYLE
        waiting = NONE_CELL
        wait_style = ""
    else:
        style, label = STATE_STYLE[pane.waiting_reason]
        age = now - (pane.waiting_since or now)
        waiting = _duration(age)
        wait_style = _waiting_style(age)

    tasks = f"{pane.tasks.completed}/{pane.tasks.total}" if pane.tasks.known else NONE_CELL
    mark = "?" if pane.phase_evidence is PhaseEvidence.EDITS else ""

    # Only STATE and WAITING carry colour; every other column renders as
    # plain padded text (`_colour` is a no-op when its style is "").
    values = {"WIN": target, "CMD": window.command, "QUIET": quiet,
              "PHASE": pane.phase.value + mark, "STATE": label,
              "WAITING": waiting, "TASKS": tasks}
    styles = {"STATE": style, "WAITING": wait_style}

    cells = [_colour(_pad(values[name], width, right), styles.get(name, ""), color)
             for name, width, right in _columns(show_phase)]
    frags = _wrap_text(pane.title, title_budget, cont_budget)
    first = "  ".join(cells) + (("  " + frags[0]) if frags[0] else "")
    rest = [_colour(" " * cont_indent + frag, DIM, color) for frag in frags[1:]]
    return [first, *rest]


def _header(show_phase: bool) -> str:
    cells = [_pad(name, width, right) for name, width, right in _columns(show_phase)]
    return "  ".join(cells) + "  TITLE"


# The one leading indent shared by the header and every data row, so a
# column header always sits directly over the column it labels. Previously
# the header carried its own "  " (2-space) literal while rows carried " "
# (1-space); they happened to use the same _columns() widths after Fix
# Round 2, but the header text still landed one column to the right of the
# data it names - the header failing at the one thing a header must do.
# Session-name lines are labels, not rows, and keep their own indent.
_GUTTER = " "


def _continuation_indent(show_phase: bool) -> int:
    """Column at which a wrapped title/cwd's continuation lines start: the
    CMD column's start, so an overflowing title reads as more of the same
    row rather than a new one.

    Derived from `_columns(show_phase)` - `_GUTTER` plus the WIN column's
    width plus the two-space gap that follows it - rather than written as
    its own literal. A literal here would repeat exactly the defect this
    file has had removed twice already in review: a constant that
    documents behaviour it does not control, so a real width change moves
    the columns and silently leaves this indent pointing at the wrong
    place instead of failing loudly.
    """
    first_width = _columns(show_phase)[0][1]
    return len(_GUTTER) + first_width + 2


def render_terminal(snapshot: Snapshot, width: int = 100, color: bool = True,
                    show_phase: bool = False, now: float | None = None) -> str:
    """Render the snapshot as an aligned grid."""
    now = now if now is not None else time.time()
    columns = _columns(show_phase)
    fixed = sum(w for _, w, _ in columns)
    gaps = 2 * len(columns)  # two spaces after each column, incl. before TITLE
    # No floor here (there used to be `max(20, ...)`): on a narrow terminal
    # that floor forced every row to at least 74 columns regardless of the
    # real width, which is exactly what overflowed the phone-width (65 col)
    # case. Whether the row line actually gets a title fragment - versus a
    # lone orphan word - depends on the first word's own width, which this
    # function doesn't have; that decision lives in `_wrap_text`.
    title_budget = width - fixed - gaps - 1  # -1 for the row's leading space
    cont_indent = _continuation_indent(show_phase)
    cont_budget = max(1, width - cont_indent)

    header_line = _GUTTER + _header(show_phase)
    lines = [header_line]
    # Freshest session nearest the prompt, as today's tls does.
    for card in sorted(snapshot.cards, key=lambda c: c.activity):
        lines.append("")
        label = "(attached)" if card.attached else "(detached)"
        style = GREEN if card.attached else AMBER
        # Full session name, e.g. "alfred@2026-08-01-15h45": the name
        # proper stays bold cyan, and the "@timestamp" suffix (if any)
        # dims out of the way instead of being dropped, so the whole name
        # is still there to scan but the noisy part doesn't compete for
        # attention. `partition` (not `split`) so a name with no "@"
        # renders unchanged - bold cyan, nothing dimmed.
        stem, sep, suffix = card.name.partition("@")
        rendered_name = _colour(stem, BOLD_CYAN, color)
        if sep:
            rendered_name += _colour(sep + suffix, DIM, color)
        lines.append("  " + rendered_name + " " + _colour(label, style, color))
        for window in card.windows:
            row_lines = _row(window, now, color, show_phase,
                             title_budget, cont_budget, cont_indent)
            lines.append(_GUTTER + row_lines[0])
            lines.extend(row_lines[1:])
    # Repeated at the bottom so a long listing is readable without
    # scrolling back to see what a column means. Same blank-line spacing
    # as the top, and built from the one `header_line` computed above so
    # the two occurrences can never drift apart from each other.
    lines.append("")
    lines.append(header_line)
    return "\n".join(lines) + "\n"

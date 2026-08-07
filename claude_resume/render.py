# ABOUTME: Renders the resume table: agreeing panes collapse to a count, differing panes expand.
# ABOUTME: Chosen over a full grid because ~22 of 26 panes agree on a typical restore.
from __future__ import annotations

SHORT_ID = 8
SAME = "="
ABSENT = "-"


def _short(session_id: str) -> str:
    if not session_id:
        return ABSENT
    return session_id[:SHORT_ID]


def _differing(columns: list) -> list:
    """Panes whose cells are not identical across every column, in stable order."""
    if len(columns) < 2:
        return []
    base = columns[0].assignment
    out = []
    for pane in sorted(base, key=lambda p: p.sort_key):
        if any(col.assignment.get(pane, "") != base[pane] for col in columns[1:]):
            out.append(pane)
    return out


def _clip(text: str, room: int) -> str:
    if room <= 1:
        return ""
    return text if len(text) <= room else text[: room - 1] + "…"


def render(columns: list, titles, width: int = 100, expand: bool = False) -> str:
    base = columns[0].assignment
    panes = sorted(base, key=lambda p: p.sort_key)
    if not panes:
        return "No Claude panes in this snapshot; nothing to resume."

    differing = _differing(columns)
    agreeing = [p for p in panes if p not in set(differing)]

    lines = []
    sessions = len({p.session for p in panes})
    lines.append(f"Claude resume - {len(panes)} panes across {sessions} sessions")
    lines.append("")

    header = "  " + "   ".join(
        f"[{c.key}] {c.label}" + (" (default)" if c.key == "1" else "") for c in columns
    )
    lines.append(_clip(header, width))
    lines.append("")

    if agreeing:
        word = "pane agrees" if len(agreeing) == 1 else "panes agree"
        suffix = "" if expand else "    [v] list them"
        lines.append(f"  {len(agreeing)} {word} across all columns -> resume most recent{suffix}")
        if expand:
            for pane in agreeing:
                sid = base[pane]
                title = titles.title_for(pane.cwd, sid) if sid else ""
                lines.append(_clip(f"    {pane.label}  {_short(sid)}  {title}", width))
        lines.append("")

    if differing:
        word = "pane differs" if len(differing) == 1 else "panes differ"
        lines.append(f"  {len(differing)} {word}:")
        lines.append("")
        for pane in differing:
            lines.append(_clip(f"  {pane.label}   {pane.cwd}", width))
            for col in columns:
                sid = col.assignment.get(pane, "")
                if col.key != "1" and sid == base.get(pane, ""):
                    lines.append(f"    [{col.key}] {SAME:>8}")
                    continue
                title = titles.title_for(pane.cwd, sid) if sid else ""
                lines.append(_clip(f"    [{col.key}] {_short(sid):>8}  {title}", width))
            lines.append("")

    keys = "/".join(c.key for c in columns)
    lines.append(f"  {keys} take column   d pane-by-pane   h more history   Enter default   q none")
    # rstrip because an absent or same cell has no title, which would otherwise
    # leave trailing spaces on the line.
    return "\n".join(line.rstrip() for line in lines)

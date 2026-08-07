# ABOUTME: Reads tsave snapshots: which Claude conversation each pane held, and when.
# ABOUTME: state.tsv columns are session, win, win_name, layout, pane, cwd, is_claude, win_active, claude_id.
from __future__ import annotations

from pathlib import Path

from .models import PaneKey, SnapshotRef

IS_CLAUDE_COL = 6
EXPECTED_COLS = 9


def read_panes(snapshot_dir: Path) -> dict:
    """Every Claude pane in this snapshot, mapped to its recorded conversation id.

    Rows that do not parse are skipped rather than raising: a snapshot can be
    written while tmux is going down, and one bad row must not cost the restore.
    """
    state = Path(snapshot_dir) / "state.tsv"
    out = {}
    try:
        text = state.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < EXPECTED_COLS:
            continue
        if cols[IS_CLAUDE_COL] != "1":
            continue
        try:
            key = PaneKey(cols[0], int(cols[1]), int(cols[4]), cols[5])
        except ValueError:
            continue
        out[key] = cols[8].strip()
    return out


def _read_line(path: Path, default: str) -> str:
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return first[0].strip() if first else default
    except OSError:
        return default


def _as_ref(d: Path):
    if not (d / "state.tsv").is_file():
        return None
    return SnapshotRef(
        path=d,
        saved_at=_read_line(d / "saved_at", "?"),
        origin=_read_line(d / "origin", "unknown"),
    )


def list_snapshots(save_dir: Path, history_dir: Path) -> list:
    """Live save first, then history newest-first.

    History dir names are YYYY-MM-DD_HH-MM-SS, so a reverse lexical sort is
    chronological. Dirs without a state.tsv are half-written and skipped.
    """
    refs = []
    live = _as_ref(Path(save_dir))
    if live is not None:
        refs.append(live)
    history = Path(history_dir)
    if history.is_dir():
        for d in sorted(history.iterdir(), key=lambda p: p.name, reverse=True):
            if not d.is_dir():
                continue
            ref = _as_ref(d)
            if ref is not None:
                refs.append(ref)
    return refs

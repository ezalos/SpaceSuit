# ABOUTME: Builds the resume table's columns: most recent, change-driven history, manual checkpoint.
# ABOUTME: A column whose cells would all equal column 1 is never produced.
from __future__ import annotations

from .candidates import assign_most_recent
from .models import Column
from .snapshots import read_panes


def _short_when(saved_at: str) -> str:
    """'2026-08-03 13:00:00' -> '08-03 13:00'. Falls back to the raw string."""
    try:
        date, time = saved_at.split(" ")
        return f"{date[5:]} {time[:5]}"
    except (ValueError, IndexError):
        return saved_at


def _assignment_from(ref, panes) -> dict:
    """What every pane would resume if this snapshot's record were used."""
    recorded = read_panes(ref.path)
    return {pane: recorded.get(pane, "") for pane in panes}


def build_columns(source_ids: dict, candidates: dict, snapshots: list,
                  max_history: int = 1) -> list:
    """Column 1 plus up to max_history change-driven columns plus a manual column.

    Change-driven means: walking snapshots newest to oldest, take the first whose
    assignment differs from every column already chosen. Columns at fixed time
    offsets were rejected because most panes hold one conversation for weeks, so
    those columns render as all-= and waste the width.
    """
    panes = list(source_ids)
    base = Column("1", "most recent", assign_most_recent(source_ids, candidates))
    columns = [base]
    seen = [base.assignment]

    def is_new(assignment: dict) -> bool:
        if not any(assignment.values()):
            return False
        return all(assignment != prior for prior in seen)

    used_manual = False
    for ref in snapshots:
        if len(columns) > max_history:
            break
        assignment = _assignment_from(ref, panes)
        if not is_new(assignment):
            continue
        # A manual snapshot picked up by the change-driven walk still gets the
        # manual label; otherwise the checkpoint you took by hand would show as
        # an anonymous timestamp and the dedicated pass below would suppress it
        # as a duplicate.
        label = _short_when(ref.saved_at)
        if ref.origin == "manual":
            label = f"manual {label}"
            used_manual = True
        columns.append(Column(str(len(columns) + 1), label, assignment))
        seen.append(assignment)

    if not used_manual:
        for ref in snapshots:
            if ref.origin != "manual":
                continue
            assignment = _assignment_from(ref, panes)
            if not is_new(assignment):
                break
            columns.append(
                Column(str(len(columns) + 1), f"manual {_short_when(ref.saved_at)}", assignment)
            )
            break

    return columns

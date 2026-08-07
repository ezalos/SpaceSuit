# ABOUTME: Dataclasses shared across the claude_resume package.
# ABOUTME: PaneKey is the identity that joins a pane to itself across snapshots.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PaneKey:
    """Identity of a Claude pane across snapshots.

    cwd is part of the key because it is what maps the pane to a Claude project
    dir. A pane that changed directory is, for resume purposes, a different pane.
    """

    session: str
    window: int
    pane: int
    cwd: str

    @property
    def sort_key(self) -> tuple:
        return (self.session, self.window, self.pane)

    @property
    def label(self) -> str:
        return f"{self.session}  w{self.window}.{self.pane}"


@dataclass
class Candidate:
    """One conversation a pane could resume.

    lineage distinguishes a conversation this pane actually held from a
    "stranger" merely sitting in the same project dir. Project dirs collect
    conversations no pane ever ran: /security-review sessions and orphans from
    closed panes. Ranking by mtime alone let those hijack live panes, which is
    what scrambled the 2026-08-03 restore, so strangers are a last resort only
    (see assign_most_recent).
    """

    session_id: str
    mtime: float
    exists: bool
    lineage: bool


@dataclass
class SnapshotRef:
    """A tsave snapshot on disk."""

    path: Path
    saved_at: str
    origin: str


@dataclass
class Column:
    """One resume policy: what every pane would get if you picked this column."""

    key: str
    label: str
    assignment: dict = field(default_factory=dict)

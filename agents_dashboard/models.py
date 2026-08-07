# ABOUTME: Enums and dataclasses describing one Claude session, one tmux card, one snapshot.
# ABOUTME: Pure data - no I/O, no classification logic (that lives in classify.py).

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    DESIGN = "design"
    IMPLEM = "implem"
    REVIEW = "review"
    WRAP_UP = "wrap_up"
    UNKNOWN = "unknown"


class Activity(str, Enum):
    WORKING = "working"
    WAITING = "waiting"


class WaitingReason(str, Enum):
    PERMISSION = "permission"
    QUESTION = "question"
    UNSENT_INPUT = "unsent_input"
    IDLE = "idle"


class PhaseEvidence(str, Enum):
    """How much the phase label is actually worth.

    Measured on the live board: 14 of 25 sessions were labelled `implem` purely
    because they had edited some files, which is true of nearly every session
    that has ever run. Those labels rendered identically to the 9 that were
    backed by an actual skill invocation, so the board looked far more certain
    than it was. Carrying the provenance lets the UI stop pretending they are
    the same kind of claim.
    """

    PLAN_MODE = "plan_mode"   # the session is literally in plan mode
    SKILL = "skill"           # a mapped skill invocation was found
    EDITS = "edits"           # guessed from edit activity alone — weak
    NONE = "none"             # nothing to go on


@dataclass(frozen=True)
class PhaseSignal:
    """One phase-signalling event found in a transcript tail, in file order."""

    kind: str  # "skill" | "edit_burst"
    name: str


@dataclass(frozen=True)
class TaskProgress:
    """Outstanding work a session declared through TaskCreate/TaskUpdate.

    This is the only hard evidence of unfinished work in a transcript. Sessions
    that never used the task tools report `known=False` rather than zero, so the
    UI can say "not tracked" instead of implying the session has nothing left.
    """

    known: bool = False
    total: int = 0
    completed: int = 0

    @property
    def outstanding(self) -> int:
        return max(0, self.total - self.completed)


@dataclass
class PaneRecord:
    """One Claude Code session, located in a tmux pane."""

    session_id: str
    tmux_session: str
    window_index: int
    pane_index: int
    cwd: str
    phase: Phase = Phase.UNKNOWN
    phase_evidence: PhaseEvidence = PhaseEvidence.NONE
    activity: Activity = Activity.WORKING
    waiting_reason: WaitingReason | None = None
    waiting_since: float | None = None  # epoch seconds
    tasks: TaskProgress = field(default_factory=TaskProgress)
    title: str = ""
    model: str = ""
    git_branch: str = ""

    @property
    def phase_is_guess(self) -> bool:
        """True when the phase is inferred from edit activity alone."""
        return self.phase_evidence is PhaseEvidence.EDITS

    @property
    def attach(self) -> str:
        return f"tmux attach -t {self.tmux_session}:{self.window_index}.{self.pane_index}"


@dataclass
class WindowRecord:
    """One tmux window/pane, with its Claude session attached when it has one."""

    window_index: int
    pane_index: int
    command: str
    cwd: str
    quiet_since: float  # epoch seconds, when this pane last drew output
    claude: PaneRecord | None = None


# Local copy of the urgency order. classify.py imports models, so models
# cannot import classify; this tuple is the one duplicated fact, and
# test_urgency_order_matches_classify below pins the two together.
_URGENCY_ORDER = (
    WaitingReason.PERMISSION,
    WaitingReason.QUESTION,
    WaitingReason.UNSENT_INPUT,
    WaitingReason.IDLE,
)


def _pane_urgency(pane: PaneRecord) -> tuple[int, float]:
    reason = pane.waiting_reason
    rank = _URGENCY_ORDER.index(reason) if reason in _URGENCY_ORDER else len(_URGENCY_ORDER)
    # An unknown wait (waiting_since is None, or coerced to the falsy 0 by a
    # corrupt statusUpdatedAt in claude_sessions.py) must sort LAST within its
    # urgency group, not first: `or 0.0` treated a broken status file as the
    # oldest possible wait, so it jumped the queue instead of falling to the
    # bottom. collect.card_key and termview._row use `or now` instead - both
    # already have a clock in scope and mean something different ("how long
    # has this card/row been idle, as of now"), so they are left alone.
    return (rank, pane.waiting_since or float("inf"))


@dataclass
class SessionCard:
    """One tmux session and every window inside it."""

    name: str
    windows: list[WindowRecord] = field(default_factory=list)
    attached: bool = False
    activity: float = 0.0  # epoch seconds, session_activity

    @property
    def panes(self) -> list[PaneRecord]:
        """Claude panes, most urgent first.

        A property, not a field: `windows` is the single source of truth. The
        ordering differs deliberately from `windows` - the web dashboard wants
        worst-first, a terminal listing wants numeric order, so each consumer
        reads the collection that matches its job.

        Because this returns a new list, it must never be mutated. Build
        `windows` instead.
        """
        claude = [w.claude for w in self.windows if w.claude is not None]
        claude.sort(key=_pane_urgency)
        return claude

    @property
    def not_started(self) -> bool:
        """A tmux session with no Claude in any window."""
        return not self.panes


@dataclass
class Snapshot:
    generated_at: float
    cards: list[SessionCard] = field(default_factory=list)

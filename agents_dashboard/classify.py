# ABOUTME: Phase rules, activity mapping and urgency ordering for the dashboard.
# ABOUTME: Pure functions over already-extracted signals - no filesystem, no subprocess.

from __future__ import annotations

from .models import Activity, Phase, PhaseEvidence, PhaseSignal, WaitingReason

# A skill invocation only signals a phase if it appears here. Anything else is
# ignored rather than guessed at: a wrong confident label is worse than "unknown".
SKILL_PHASES: dict[str, Phase] = {
    "wrap-up": Phase.WRAP_UP,
    "finishing-a-development-branch": Phase.WRAP_UP,
    "requesting-code-review": Phase.REVIEW,
    "code-review": Phase.REVIEW,
    "security-review": Phase.REVIEW,
    "receiving-code-review": Phase.REVIEW,
    "verification-before-completion": Phase.REVIEW,
    "brainstorming": Phase.DESIGN,
    "writing-plans": Phase.DESIGN,
    "executing-plans": Phase.IMPLEM,
    "test-driven-development": Phase.IMPLEM,
    "subagent-driven-development": Phase.IMPLEM,
    # Observed in live sessions and unambiguously implementation work. Added
    # after a survey of every skill actually invoked across the 24 live
    # transcripts; skills without a clear phase (share-file, pull-uploads,
    # add-dotfile, claude-in-chrome) are deliberately left unmapped so they
    # cannot drag a session into a wrong phase.
    "systematic-debugging": Phase.IMPLEM,
}

ACTIVITY_BY_STATUS: dict[str, Activity] = {
    "busy": Activity.WORKING,
    "shell": Activity.WORKING,
    "idle": Activity.WAITING,
}

URGENCY: dict[WaitingReason, int] = {
    WaitingReason.PERMISSION: 0,
    WaitingReason.QUESTION: 1,
    WaitingReason.UNSENT_INPUT: 2,
    WaitingReason.IDLE: 3,
}


def urgency_rank(reason: WaitingReason | None) -> int:
    """Lower is more urgent. Sessions that are working sort last."""
    if reason is None:
        return len(URGENCY)
    return URGENCY[reason]


def classify_phase(signals: list[PhaseSignal], mode: str | None) -> Phase:
    """Plan mode wins; then the most recent mapped skill; then edit activity.

    Skills outrank edits rather than competing with them by recency, and that
    reverses an earlier draft of this design. The draft argued that a session
    which invoked `wrap-up` and then edited files should read as `implem`.
    Measurement on 24 live sessions refuted it twice over:

    - Edits are constant and skills are rare, so an edit burst positioned at the
      last edit swamped every skill. All 24 sessions classified as `implem` -
      exactly as useless as the all-`unknown` result it replaced.
    - The motivating example was simply wrong about the workflow. Wrapping up
      *involves* editing (memory files, lessons, commits); so does fixing review
      findings, and so does writing a spec during design. Edits after a skill are
      usually part of that skill's phase, not a departure from it.

    Among skills, the most recent still wins - that part held up.

    Edit activity is the fallback, not the headline: it only decides the phase
    when no mapped skill appears anywhere in the scanned window. Be honest about
    what that means - on the live machine 15 of the 21 sessions that classified
    as `implem` have no mapped skill within 4 MB, so their `implem` reflects
    "this session has been editing files", which is weak evidence. `unknown`
    remains the answer when there is neither.
    """
    return classify_phase_with_evidence(signals, mode)[0]


def classify_phase_with_evidence(
    signals: list[PhaseSignal], mode: str | None
) -> tuple[Phase, PhaseEvidence]:
    """Same rules as `classify_phase`, but says how good the answer is.

    The phase alone was misleading in practice. On the live board 14 of 25
    sessions reached `implem` through the edit fallback with no mapped skill
    anywhere in 4 MB, and rendered identically to the 9 that had real evidence.
    "This session has edited files" is true of essentially every session, so
    presenting it as a phase overstated what was known.

    Returning the provenance keeps the useful fallback while letting callers
    show it as the guess it is.
    """
    if mode == "plan":
        return Phase.DESIGN, PhaseEvidence.PLAN_MODE

    for signal in reversed(signals):
        if signal.kind == "skill":
            phase = SKILL_PHASES.get(signal.name)
            if phase is not None:
                return phase, PhaseEvidence.SKILL

    if any(signal.kind == "edit_burst" for signal in signals):
        return Phase.IMPLEM, PhaseEvidence.EDITS

    return Phase.UNKNOWN, PhaseEvidence.NONE


def map_activity(status: str) -> Activity:
    """Map Claude Code's own status to our activity axis.

    An unrecognised status maps to WORKING, not WAITING: failing safe here means
    a future status value cannot invent a blocker that pulls Louis's attention.
    A non-string status (e.g. a malformed session file smuggling a list into
    the field) is coerced to that same fail-safe default rather than raising -
    dict.get() on an unhashable key (a list) crashes with TypeError before its
    own default value would ever apply, and one bad session must not take down
    every pane in the snapshot.
    """
    if not isinstance(status, str):
        return Activity.WORKING
    return ACTIVITY_BY_STATUS.get(status, Activity.WORKING)

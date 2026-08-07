# ABOUTME: Join tmux, Claude session files and transcript tails into one Snapshot.
# ABOUTME: Every source is injectable, so the whole join is testable with no tmux present.

from __future__ import annotations

import time
from dataclasses import asdict

from . import claude_sessions, panescan, transcripts, tmux
from .classify import classify_phase_with_evidence, map_activity, urgency_rank
from .models import Activity, PaneRecord, SessionCard, Snapshot, WaitingReason, WindowRecord


def detect_question(entries: list[dict]) -> bool:
    """True when the last assistant turn asked Louis something explicitly."""
    for entry in reversed(entries):
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("name") == "AskUserQuestion":
                return True
        return False  # only the most recent assistant turn counts
    return False


def build_snapshot(
    now, panes, sessions, pid_lookup, transcript_reader, pane_capturer
) -> Snapshot:
    by_pid = dict(sessions)
    cards: dict[str, SessionCard] = {}

    for pane in panes:
        card = cards.setdefault(pane.session, SessionCard(name=pane.session))
        # Session-level facts arrive on every pane; last one wins, they agree.
        card.attached = pane.session_attached
        card.activity = pane.session_activity

        record = None
        pid = pid_lookup(pane.tty)
        session = by_pid.get(pid) if pid is not None else None
        if session is not None:
            info = transcript_reader(session)
            activity = map_activity(session.status)

            reason = None
            if activity is Activity.WAITING:
                # Only ever computed for waiting sessions: text in the prompt
                # box while the agent works is type-ahead, not a dropped
                # thread. This guard is also why the fragile pane capture
                # rarely runs.
                text = pane_capturer(pane.session, pane.window_index, pane.pane_index)
                reason = panescan.scan(text) or (
                    WaitingReason.QUESTION if info.asked_question else WaitingReason.IDLE
                )

            phase, evidence = classify_phase_with_evidence(info.signals, info.mode)
            record = PaneRecord(
                session_id=session.session_id,
                tmux_session=pane.session,
                window_index=pane.window_index,
                pane_index=pane.pane_index,
                cwd=session.cwd or pane.cwd,
                phase=phase,
                phase_evidence=evidence,
                activity=activity,
                waiting_reason=reason,
                waiting_since=session.status_updated_at if reason else None,
                tasks=info.tasks,
                title=info.title or session.name,
                model=info.model,
                git_branch=info.git_branch,
            )

        card.windows.append(
            WindowRecord(
                window_index=pane.window_index,
                pane_index=pane.pane_index,
                command=pane.command,
                cwd=pane.cwd,
                quiet_since=pane.quiet_since,
                claude=record,
            )
        )

    working_rank = urgency_rank(None)  # the rank given to a session that is working

    def card_key(card: SessionCard):
        if not card.panes:
            return (2, 0, 0.0)  # not-started cards sort last
        best = min(urgency_rank(p.waiting_reason) for p in card.panes)
        oldest = min((p.waiting_since or now) for p in card.panes)
        # Cards with at least one waiting pane sort ahead of all-working cards;
        # within that, most urgent reason first, then longest wait first.
        return (0 if best < working_rank else 1, best, oldest)

    for card in cards.values():
        card.windows.sort(key=lambda w: (w.window_index, w.pane_index))

    return Snapshot(generated_at=now, cards=sorted(cards.values(), key=card_key))


def collect(now: float | None = None, with_phase: bool = True) -> Snapshot:
    """Wire the real sources together.

    `with_phase=False` skips the 4 MB phase scan, which profiled at 0.434 s
    across 20 sessions. The terminal view does not show phase by default, so
    it opts out; the web dashboard keeps the default.
    """
    sessions = claude_sessions.load_all()
    pids = tmux.claude_pids_by_tty()

    def read_transcript(session):
        path = claude_sessions.find_transcript(session)
        if with_phase:
            info = transcripts.read_for_phase(path)
        else:
            info = transcripts.read(path)
            # Strip all phase inputs (signals and mode) so classify_phase_with_evidence
            # returns UNKNOWN. The flag's contract is "phase was not scanned, do not
            # trust this field"; a phase that silently means different things depending
            # on a flag is a trap for any later consumer.
            info = transcripts.strip_phase_inputs(info)
        info.asked_question = detect_question(transcripts.read_tail(path))
        return info

    return build_snapshot(
        now=now if now is not None else time.time(),
        panes=tmux.list_panes(),
        sessions=sessions,
        pid_lookup=lambda tty: pids.get(tmux.normalise_tty(tty)),
        transcript_reader=read_transcript,
        pane_capturer=tmux.capture_pane,
    )


def snapshot_to_dict(snapshot: Snapshot) -> dict:
    return {
        "generated_at": snapshot.generated_at,
        "cards": [
            {
                "name": card.name,
                "not_started": card.not_started,
                "panes": [
                    {**asdict(pane), "attach": pane.attach} for pane in card.panes
                ],
            }
            for card in snapshot.cards
        ],
    }

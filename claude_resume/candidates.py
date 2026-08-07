# ABOUTME: Builds each pane's candidate conversations and assigns one per pane.
# ABOUTME: Reservation plus lineage tiers make the pane-swap and stranger-promotion bugs impossible.
from __future__ import annotations

from pathlib import Path

from .claude_paths import list_transcripts, transcript_path
from .models import Candidate, PaneKey
from .snapshots import read_panes


def build_candidates(source_ids: dict, snapshots: list, projects_dir: Path) -> dict:
    """Candidate conversations per pane, newest first.

    Three sources: the id the pane holds in the snapshot being restored, ids the
    same pane held in older snapshots (together, its "lineage"), and
    conversations in the pane's cwd that no pane holds ("strangers").
    """
    projects_dir = Path(projects_dir)

    # Conversations any pane holds in the restored snapshot. Reserved: only their
    # own pane may take them.
    reserved = {sid: pane for pane, sid in source_ids.items() if sid}

    # Historical ids, keyed by pane. Read each snapshot once.
    historical = {}
    for ref in snapshots:
        for pane, sid in read_panes(ref.path).items():
            if sid:
                historical.setdefault(pane, []).append(sid)

    # mtimes per cwd, read once per directory rather than once per pane.
    by_cwd = {}
    for pane in source_ids:
        if pane.cwd not in by_cwd:
            by_cwd[pane.cwd] = dict(list_transcripts(pane.cwd, projects_dir))

    out = {}
    for pane, own in source_ids.items():
        mtimes = by_cwd.get(pane.cwd, {})
        ids = []
        if own:
            ids.append(own)
        ids.extend(historical.get(pane, []))
        for sid in mtimes:
            owner = reserved.get(sid)
            if owner is None or owner == pane:
                ids.append(sid)

        # Everything this pane actually held, as opposed to strangers that merely
        # live in the same project dir.
        lineage_ids = set()
        if own:
            lineage_ids.add(own)
        lineage_ids.update(historical.get(pane, []))

        seen, cands = set(), []
        for sid in ids:
            if sid in seen:
                continue
            seen.add(sid)
            is_lineage = sid in lineage_ids
            mtime = mtimes.get(sid)
            if mtime is None:
                found = transcript_path(pane.cwd, sid, projects_dir)
                if found is None:
                    cands.append(Candidate(sid, 0.0, False, is_lineage))
                    continue
                try:
                    mtime = found.stat().st_mtime
                except OSError:
                    cands.append(Candidate(sid, 0.0, False, is_lineage))
                    continue
            cands.append(Candidate(sid, mtime, True, is_lineage))

        cands.sort(key=lambda c: (-c.mtime, c.session_id))
        out[pane] = cands
    return out


def assign_most_recent(source_ids: dict, candidates: dict) -> dict:
    """One conversation per pane: the newest that pane may legitimately have.

    Reservation is the invariant: a conversation another pane held in the source
    snapshot is never offered here, at any tier. That is what stops two panes in
    one cwd from trading conversations, which is what the old mtime-ordered
    greedy walk did.

    Tiers exist because ranking on mtime alone is not safe. A project dir also
    holds conversations no pane ever ran: /security-review sessions and orphans
    from closed panes. On the 2026-08-03 crash restore, mtime-only handed a
    49-line /security-review session a pane and dropped a 1.4MB real one.
    """
    reserved = {sid: pane for pane, sid in source_ids.items() if sid}
    out, taken = {}, set()

    def eligible(pane: PaneKey, cand: Candidate) -> bool:
        if not cand.exists or cand.session_id in taken:
            return False
        owner = reserved.get(cand.session_id)
        return owner is None or owner == pane

    panes = sorted(source_ids, key=lambda p: p.sort_key)

    # Tier 1, identity: a pane keeps the conversation it was actually running,
    # whenever that transcript still exists. This is the answer for every pane
    # on current data.
    for pane in panes:
        own = source_ids.get(pane)
        if not own or own in taken:
            continue
        c = next((c for c in candidates.get(pane, [])
                  if c.session_id == own and c.exists), None)
        if c is not None:
            out[pane] = own
            taken.add(own)

    # Tier 2, this pane's own past: the newest conversation the pane itself held.
    for pane in panes:
        if pane in out:
            continue
        c = next((c for c in candidates.get(pane, [])
                  if c.lineage and eligible(pane, c)), None)
        if c is not None:
            out[pane] = c.session_id
            taken.add(c.session_id)

    # Tier 3, last resort: a stranger from the cwd. Only reached once a pane's
    # entire lineage is gone from disk, so a tool-spawned session can never
    # displace a live pane.
    for pane in panes:
        if pane in out:
            continue
        c = next((c for c in candidates.get(pane, []) if eligible(pane, c)), None)
        out[pane] = c.session_id if c else ""
        if c is not None:
            taken.add(c.session_id)
    return out

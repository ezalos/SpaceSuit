# ABOUTME: Tail-read a Claude Code transcript and extract phase signals plus metadata.
# ABOUTME: Never reads a whole transcript - the corpus is 3.6 GB, largest file 49.7 MB.

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import PhaseSignal, TaskProgress

TAIL_BYTES = 262_144
# Phase scanning reads a bigger window than the 256 KB tail: skill invocations
# were measured 1-4 MB back on the live machine, because a session invokes
# `brainstorming` early and then talks for megabytes. Reading only the tail left
# all 24 live sessions unclassified.
#
# An earlier draft escalated through a ladder of windows, stopping at the first
# that produced any signal. That was worse than useless: edits are always present
# in the tail, so the ladder stopped immediately and never reached the skills -
# 23 of 24 sessions still found no skill at all. Measurement settled it: reading
# the full 4 MB for every one of the 24 live sessions costs 0.3 s total, so the
# ladder bought nothing and cost a mechanism. One flat cap instead.
PHASE_SCAN_BYTES = 4_194_304
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}

_PHASE_CACHE: dict[tuple[str, int, int], "TranscriptInfo"] = {}
_PHASE_CACHE_MAX = 512


# Fields of TranscriptInfo that feed phase classification.
# Used to strip phase inputs when with_phase=False, ensuring the flag's contract
# is maintained: phase was not scanned, do not trust this field.
# When TranscriptInfo is extended, whoever adds a field must decide whether it
# feeds phase and update this accordingly.
PHASE_INPUT_FIELDS = {"signals", "mode"}

# Fields of TranscriptInfo that are display metadata, not phase inputs.
# Preserved when stripping phase for with_phase=False.
DISPLAY_FIELDS = {"title", "git_branch", "model", "asked_question", "tasks"}


@dataclass
class TranscriptInfo:
    signals: list[PhaseSignal] = field(default_factory=list)
    mode: str | None = None
    title: str = ""
    git_branch: str = ""
    model: str = ""
    asked_question: bool = False
    tasks: TaskProgress = field(default_factory=TaskProgress)


def _blank_value(field: dataclasses.Field) -> object:
    """Get the blank value for a field: its dataclass default."""
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()  # fresh list, never shared
    if field.default is not dataclasses.MISSING:
        return field.default
    raise TypeError(f"{field.name} is a phase input with no default to blank to")


def strip_phase_inputs(info: TranscriptInfo) -> TranscriptInfo:
    """Return a copy with every phase input blanked and all display metadata kept.

    Driven by PHASE_INPUT_FIELDS rather than by naming fields inline: adding a
    name there must be sufficient to close a leak, and adding a display field
    must not require editing this function. An earlier version hardcoded the
    field names, so the tuple documented behaviour it did not control — the
    tuple said `mode` was a phase input while the code would have cleared it
    either way, and a correctly-classified new display field was silently wiped.
    """
    blanks = {f.name: _blank_value(f)
              for f in dataclasses.fields(TranscriptInfo)
              if f.name in PHASE_INPUT_FIELDS}
    return dataclasses.replace(info, **blanks)


def read_tail(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict]:
    """Parse the last `max_bytes` of a JSONL transcript.

    Seeks to the end rather than reading forward: transcripts reach 49.7 MB and a
    full parse is a correctness bug, not merely slow. When the file is larger than
    the window the first line read is almost certainly a fragment, so it is dropped.
    """
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, 2)
            start = max(0, size - max_bytes)
            fh.seek(start)
            if start > 0:
                fh.readline()  # discard the partial first line
            raw = fh.read()
    except OSError:
        return []

    entries = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue  # a truncated or malformed line is not worth failing over
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _tool_uses(entry: dict) -> list[dict]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def extract(entries: list[dict], edit_burst_threshold: int = 3) -> TranscriptInfo:
    """Pull phase signals and display metadata out of parsed transcript entries.

    Signals are recorded with the index of the entry they came from and sorted at
    the end, so `classify_phase`'s "most recent event wins" rule stays correct
    when an edit burst and a skill invocation interleave.
    """
    info = TranscriptInfo()
    indexed: list[tuple[int, PhaseSignal]] = []
    edit_indices: list[int] = []
    # Task ids this session created, and the last status each was given. The
    # only hard evidence of unfinished work in a transcript - a phase label is
    # an inference, whereas "3 of 7 tasks still open" is something the session
    # said about itself.
    task_status: dict[str, str] = {}
    tasks_created = 0

    for index, entry in enumerate(entries):
        entry_type = entry.get("type")
        if entry_type == "mode":
            # Observed values on the live corpus: "normal" and "content" only -
            # never "plan". Kept because it is a real, distinct field (and a
            # future value here might matter), but it drives no classification
            # rule today; the plan-mode override reads permission-mode below.
            info.mode = entry.get("mode") or info.mode
            continue
        if entry_type == "permission-mode":
            # This, not type:mode, is where plan mode actually lives. Measured
            # against the live transcript corpus: 2,430 type:mode entries, none
            # "plan"; permission-mode entries carry "auto" and "plan". Feeds
            # the same info.mode field as type:mode above, so "last one wins"
            # by file order holds across both entry types combined.
            info.mode = entry.get("permissionMode") or info.mode
            continue
        if entry_type == "ai-title":
            info.title = entry.get("aiTitle") or info.title
            continue

        if entry.get("gitBranch"):
            info.git_branch = entry["gitBranch"]
        message = entry.get("message")
        if isinstance(message, dict) and message.get("model"):
            info.model = message["model"]

        for block in _tool_uses(entry):
            name = block.get("name")
            if name in EDIT_TOOLS:
                edit_indices.append(index)
                continue
            if name == "TaskCreate":
                # Ids are assigned by the harness and not echoed in the call, so
                # count creations and match updates by their own id. A create we
                # never see an update for stays counted as outstanding, which is
                # the safe direction: it under-claims completion.
                tasks_created += 1
                continue
            if name == "TaskUpdate":
                payload = block.get("input") or {}
                tid, status = payload.get("taskId"), payload.get("status")
                if tid is not None and isinstance(status, str):
                    task_status[str(tid)] = status
                continue
            if name != "Skill":
                continue
            # A skill invoked by a subagent is that subagent's business, not the
            # phase of the main thread.
            caller = block.get("caller") or {}
            if caller.get("type", "direct") != "direct":
                continue
            skill = (block.get("input") or {}).get("skill") or ""
            if skill:
                indexed.append((index, PhaseSignal(kind="skill", name=skill.split(":")[-1])))

    # Edits are counted across the WHOLE window, not per entry. Claude emits
    # roughly one tool call per entry, so a per-entry threshold essentially never
    # fired - a live session with 13 edits in its tail produced zero signals.
    # The burst is one signal, positioned at the last edit so that a skill
    # invoked after the edits still wins.
    if len(edit_indices) >= edit_burst_threshold:
        indexed.append((edit_indices[-1], PhaseSignal(kind="edit_burst", name="edit_burst")))

    # Tasks can be updated in a window that no longer contains their creation,
    # so the true total is whichever is larger: creations seen, or distinct ids
    # touched. Otherwise a long session shows "2 of 1 done".
    total = max(tasks_created, len(task_status))
    completed = sum(1 for s in task_status.values() if s == "completed")
    info.tasks = TaskProgress(known=total > 0, total=total,
                              completed=min(completed, total))

    indexed.sort(key=lambda pair: pair[0])
    info.signals = [signal for _, signal in indexed]
    return info


def read(path: Path, max_bytes: int = TAIL_BYTES) -> TranscriptInfo:
    return extract(read_tail(path, max_bytes))


def read_for_phase(path: Path, max_bytes: int = PHASE_SCAN_BYTES) -> TranscriptInfo:
    """Read the phase-scan window, memoised on (path, size, window).

    The cap is deliberate. One live transcript is 41 MB and holds its only skill
    invocation at the very start; reading 41 MB to find it is not worth it, and
    `unknown` is the honest answer for a session whose last thousands of turns
    say nothing about phase.

    `max_bytes` is part of the cache key so a call at a different depth cannot
    silently receive a result computed for another one.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return TranscriptInfo()

    cache_key = (str(path), size, max_bytes)
    cached = _PHASE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    info = read(path, max_bytes=max_bytes)
    _PHASE_CACHE[cache_key] = info
    if len(_PHASE_CACHE) > _PHASE_CACHE_MAX:
        _PHASE_CACHE.clear()  # crude bound; the cost of a miss is one re-read
    return info

# ABOUTME: Walks a claude.ai conversation to its live thread and classifies where a Research run stands.
# ABOUTME: Pure over the conversation JSON; stop_reason is deliberately ignored because research is asynchronous.
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

RESEARCH_TASK = "launch_extended_search_task"
ARTIFACT_TOOL = "artifacts"
_TASK_ID_RE = re.compile(r"wf-[0-9a-fA-F-]+")


class ThreadState(str, Enum):
    DONE = "done"
    RUNNING = "running"
    NEEDS_REPLY = "needs-reply"
    EMPTY = "empty"


# What to tell Louis when a run produced no report. NEEDS_REPLY means the assistant spoke and
# never launched research: a clarifying question, a refusal and a plain answer are
# indistinguishable in this JSON (no field marks any of them), and all three want the same
# thing from him, so the reason names the action rather than guessing which one it was. EMPTY
# means the thread carries no assistant content at all, a different thing to look at.
FAILURE_REASONS = {
    ThreadState.NEEDS_REPLY: "the assistant replied without launching research; answer it in the chat",
    ThreadState.EMPTY: "no assistant content in the conversation",
}


def failure_reason(state: ThreadState) -> str:
    return FAILURE_REASONS[state]


@dataclass(frozen=True)
class Classified:
    state: ThreadState
    artifact: dict | None
    text: str


def live_thread(conversation: dict) -> list[dict]:
    messages = {m.get("uuid"): m for m in conversation.get("chat_messages") or []}
    leaf = conversation.get("current_leaf_message_uuid")
    thread: list[dict] = []
    seen: set[str] = set()
    while leaf and leaf in messages and leaf not in seen:
        seen.add(leaf)
        thread.append(messages[leaf])
        leaf = messages[leaf].get("parent_message_uuid")
    if not thread:
        # Snapshots and old captures carry no leaf pointer; index order is the best left.
        return sorted(messages.values(), key=lambda m: m.get("index", 0))
    thread.reverse()
    return thread


def _blocks(message: dict, kind: str, name: str | None = None):
    for block in message.get("content") or []:
        if block.get("type") == kind and (name is None or block.get("name") == name):
            yield block


def _assistant(thread):
    return (m for m in thread if m.get("sender") == "assistant")


def newest_artifact(thread: list[dict]) -> dict | None:
    found = None
    for message in _assistant(thread):
        for block in _blocks(message, "tool_use", ARTIFACT_TOOL):
            inp = block.get("input") or {}
            if inp.get("content"):
                found = inp
    return found


def has_research_task(thread: list[dict]) -> bool:
    return any(any(_blocks(m, "tool_use", RESEARCH_TASK)) for m in _assistant(thread))


def research_task_id(thread: list[dict]) -> str | None:
    for message in _assistant(thread):
        for block in _blocks(message, "tool_result", RESEARCH_TASK):
            for part in block.get("content") or []:
                text = part.get("text") or ""
                try:
                    task = json.loads(text).get("task_id")
                    if task:
                        return str(task)
                except (ValueError, AttributeError):
                    pass
                m = _TASK_ID_RE.search(text)
                if m:
                    return m.group(0)
    return None


def assistant_text(thread: list[dict]) -> str:
    parts = [b.get("text", "") for m in _assistant(thread) for b in _blocks(m, "text")]
    return "\n\n".join(p for p in parts if p)


def classify(conversation: dict) -> Classified:
    thread = live_thread(conversation)
    text = assistant_text(thread)
    artifact = newest_artifact(thread)
    if artifact:
        return Classified(ThreadState.DONE, artifact, text)
    if has_research_task(thread):
        return Classified(ThreadState.RUNNING, None, text)
    if any(m.get("stop_reason") for m in _assistant(thread)):
        return Classified(ThreadState.NEEDS_REPLY, None, text)
    return Classified(ThreadState.EMPTY, None, "")

# ABOUTME: Shared test builders: conversation JSON in the captured claude.ai shape, a fake API, a fetcher stub.
# ABOUTME: Every shape mirrors the design doc's captured examples so tests exercise real field names.
from __future__ import annotations

import json

import pytest

from deep_research_web.payload import ROOT_PARENT_UUID as ROOT


@pytest.fixture(autouse=True)
def _claude_log_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_LOG_FILE", str(tmp_path / "lessons.md"))


def text_block(text: str) -> dict:
    return {"type": "text", "text": text, "citations": []}


def tool_use(name: str, input: dict | None = None, id: str | None = None) -> dict:
    block = {"type": "tool_use", "name": name, "input": input or {}}
    if id:
        block["id"] = id
    return block


def tool_result(name: str, text: str) -> dict:
    return {"type": "tool_result", "name": name, "content": [{"type": "text", "text": text}], "is_error": False}


def artifact_block(content: str, citations: list[dict] | None = None) -> dict:
    return tool_use("artifacts", {
        "command": "create", "id": "compass_artifact_wf-abc_text/markdown", "title": "Report",
        "type": "text/markdown", "language": None, "content": content,
        "md_citations": citations or [], "source": "c", "version_uuid": "v1",
    })


def citation(url: str, start: int, end: int, title: str = "", origin: str = "web_fetch") -> dict:
    return {"uuid": f"c-{start}", "title": title or url, "url": url, "origin_tool_name": origin,
            "metadata": {"type": "generic_metadata"}, "sources": [], "start_index": start, "end_index": end}


def human(text: str, uuid: str, parent: str = ROOT) -> dict:
    return {"uuid": uuid, "sender": "human", "content": [text_block(text)], "parent_message_uuid": parent, "index": 0}


def assistant(blocks: list[dict], uuid: str, parent: str, stop_reason: str | None = None) -> dict:
    msg = {"uuid": uuid, "sender": "assistant", "content": blocks, "parent_message_uuid": parent, "index": 1}
    if stop_reason:
        msg["stop_reason"] = stop_reason
    return msg


def conversation(messages: list[dict], leaf: str | None, **top) -> dict:
    conv = {"uuid": "conv-1", "name": "", "chat_messages": messages, "current_leaf_message_uuid": leaf,
            "settings": {"enabled_web_search": True}}
    conv.update(top)
    return conv


def research_started(stop_reason: str | None = "end_turn") -> dict:
    """A conversation mid-research: task launched, turn ended, no artifact yet."""
    return conversation([
        human("q", "h1"),
        assistant([
            tool_use("launch_extended_search_task", {"query": "q"}, id="toolu_1"),
            tool_result("launch_extended_search_task", json.dumps({"task_id": "wf-123"})),
        ], "a1", "h1", stop_reason=stop_reason),
    ], leaf="a1")


def research_done(content: str = "# Report\n\nClaim one.", citations: list[dict] | None = None) -> dict:
    return conversation([
        human("q", "h1"),
        assistant([
            tool_use("launch_extended_search_task", {"query": "q"}, id="toolu_1"),
            tool_result("launch_extended_search_task", json.dumps({"task_id": "wf-123"})),
            artifact_block(content, citations),
        ], "a1", "h1", stop_reason="end_turn"),
    ], leaf="a1")


class FakeApi:
    """Routes {(METHOD, path-without-query): ApiResponse | callable(body) -> ApiResponse}; records calls."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, dict | None, bool]] = []

    def __call__(self, method, path, body=None, stream=False, timeout_ms=None):
        from deep_research_web.client import ApiResponse
        self.calls.append((method, path, body, stream))
        route = self.routes.get((method, path.split("?")[0]))
        if route is None:
            return ApiResponse(404, "")
        return route(body) if callable(route) else route


def fetcher_stub(pages: dict[str, tuple[int, str]]):
    """Fetcher over a {url: (status, body)} map; a missing url is a network error."""

    def fetch(url, timeout=None):
        if url not in pages:
            raise OSError(f"unreachable: {url}")
        return pages[url]

    return fetch


@pytest.fixture
def runs_root(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    return root

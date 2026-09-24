# ABOUTME: Tests for the endpoint wrappers against a recording fake API.
# ABOUTME: Every path and method here is a captured contract with claude.ai.
import json

import pytest
from conftest import FakeApi

from deep_research_web.client import ApiError, ApiResponse, Client, choose_org, exhausted

ORGS = [
    {"uuid": "console", "name": "api only", "capabilities": ["api"]},
    {"uuid": "chat-small", "name": "chat", "capabilities": ["chat"]},
    {"uuid": "chat-max", "name": "max", "capabilities": ["chat", "claude_max", "claude_pro"], "active_flags": []},
]


def test_choose_org_prefers_chat_with_most_capabilities():
    assert choose_org(ORGS)["uuid"] == "chat-max"
    with pytest.raises(ApiError):
        choose_org([ORGS[0]])


def test_orgs_and_flags():
    api = FakeApi({("GET", "/api/organizations"): ApiResponse(200, json.dumps(ORGS))})
    client = Client(api)
    org = choose_org(client.orgs())
    assert client.flags(org) == []
    assert client.flags({"active_flags": [{"type": "consumer_first_warning", "expires_at": "x"}]})[0]["type"] == "consumer_first_warning"


def test_model_available_maps_200_and_404_and_raises_otherwise():
    api = FakeApi({
        ("GET", "/api/organizations/o/model_configs/claude-fable-5-1"): ApiResponse(200, "{}"),
        ("GET", "/api/organizations/o/model_configs/broken"): ApiResponse(500, "boom"),
    })
    client = Client(api)
    assert client.model_available("o", "claude-fable-5-1") is True
    assert client.model_available("o", "claude-nope") is False
    with pytest.raises(ApiError) as exc:
        client.model_available("o", "broken")
    assert exc.value.status == 500


def test_find_project_by_exact_name():
    api = FakeApi({("GET", "/api/organizations/o/projects"): ApiResponse(200, json.dumps(
        [{"uuid": "p1", "name": "Deep research"}, {"uuid": "p2", "name": "Other"}]))})
    client = Client(api)
    assert client.find_project("o", "Deep research") == "p1"
    assert client.find_project("o", "deep research") is None


def test_start_research_streams_to_the_completion_path():
    api = FakeApi({("POST", "/api/organizations/o/chat_conversations/c/completion"): ApiResponse(200, "event: message_stop\r\n\r\n")})
    r = Client(api).start_research("o", "c", {"prompt": "q"})
    assert r.status == 200
    assert api.calls == [("POST", "/api/organizations/o/chat_conversations/c/completion", {"prompt": "q"}, True)]


def test_rename_puts_the_name_and_accepts_202():
    api = FakeApi({("PUT", "/api/organizations/o/chat_conversations/c"): ApiResponse(202, "")})
    Client(api).rename("o", "c", "Which vector db?")
    assert api.calls[-1][2] == {"name": "Which vector db?"}
    with pytest.raises(ApiError):
        Client(FakeApi()).rename("o", "c", "x")


def test_conversation_asks_for_rendered_messages_with_all_tools():
    api = FakeApi({("GET", "/api/organizations/o/chat_conversations/c"): ApiResponse(200, json.dumps({"uuid": "c"}))})
    assert Client(api).conversation("o", "c") == {"uuid": "c"}
    assert api.calls[-1][1] == "/api/organizations/o/chat_conversations/c?rendering_mode=messages&render_all_tools=true"


def test_stop_response_posts():
    api = FakeApi({("POST", "/api/organizations/o/chat_conversations/c/stop_response"): ApiResponse(200, "")})
    Client(api).stop_response("o", "c")
    assert api.calls[-1][0] == "POST"


def test_exhausted_reads_the_message_limit_event():
    stream = 'event: message_limit\r\ndata: {"type":"message_limit","message_limit":{"type":"exceeded_limit","resetsAt":1760000000}}\r\n\r\n'
    assert exhausted(stream) == "1760000000"
    ok = 'data: {"type":"message_limit","message_limit":{"type":"within_limit","resetsAt":null}}\r\n\r\n'
    assert exhausted(ok) is None
    assert exhausted("") is None

# ABOUTME: Tests for the captured completion payload and the anthropic-* header set.
# ABOUTME: The literals here are the contract with claude.ai; changing them is a design change.
import itertools

from deep_research_web.payload import (
    COMPASS_MODE, ROOT_PARENT_UUID, api_headers, completion_payload, local_timezone,
)


def _uuids():
    counter = itertools.count(1)
    return lambda: f"u{next(counter)}"


def test_payload_carries_the_research_flag_and_web_search():
    p = completion_payload("hello", "claude-fable-5-1", "Europe/Paris", uuids=_uuids())
    params = p["create_conversation_params"]
    assert params["compass_mode"] == COMPASS_MODE == "advanced"
    assert params["paprika_mode"] is None
    assert params["is_temporary"] is False
    assert params["model"] == p["model"] == "claude-fable-5-1"
    assert "project_uuid" not in params
    assert {"name": "web_search", "type": "web_search_v0"} in p["tools"]
    assert {"name": "artifacts", "type": "artifacts_v0"} in p["tools"]
    assert p["parent_message_uuid"] == ROOT_PARENT_UUID == "00000000-0000-4000-8000-000000000000"
    assert p["rendering_mode"] == "messages"
    assert p["turn_message_uuids"] == {"human_message_uuid": "u1", "assistant_message_uuid": "u2"}
    assert p["prompt"] == "hello" and p["timezone"] == "Europe/Paris"
    assert p["attachments"] == [] and p["files"] == [] and p["sync_sources"] == []


def test_project_uuid_lands_in_create_params():
    p = completion_payload("q", "m", "UTC", project_uuid="proj-1", uuids=_uuids())
    assert p["create_conversation_params"]["project_uuid"] == "proj-1"


def test_headers_come_from_cookies_not_invention():
    cookies = {"anthropic-device-id": "dev", "ajs_anonymous_id": "anon", "activitySessionId": "act"}
    h = api_headers(cookies)
    assert h["anthropic-client-platform"] == "web_claude_ai"
    assert h["anthropic-device-id"] == "dev"
    assert h["anthropic-anonymous-id"] == "anon"
    assert h["x-activity-session-id"] == "act"
    assert h["content-type"] == "application/json"
    assert "accept" not in h
    assert "anthropic-device-id" not in api_headers({})


def test_stream_adds_event_stream_accept():
    assert api_headers({}, stream=True)["accept"] == "text/event-stream"


def test_local_timezone_is_a_zone_name():
    assert "/" in local_timezone() or local_timezone() == "UTC"

# ABOUTME: Builds the claude.ai completion payload that starts a Research conversation, and the header set.
# ABOUTME: Every literal was captured from the web app; capture dates are in the design doc.
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable, Mapping

ROOT_PARENT_UUID = "00000000-0000-4000-8000-000000000000"
# Observed on 2026-05-14 (lm-assist route notes). Application-level, not required, but
# the web app sends it on every call and we want to look like the web app.
CLIENT_SHA = "8a753cbf88e19be0f5f67efefb1b07840b6402e9"
# The Research flag, captured May 2026 (LT-0I). Single source: launch verifies engagement
# rather than trusting it.
COMPASS_MODE = "advanced"
TOOLS = [
    {"name": "web_search", "type": "web_search_v0"},
    {"name": "artifacts", "type": "artifacts_v0"},
]
DEFAULT_STYLE = {
    "type": "default", "key": "Default", "name": "Normal", "nameKey": "normal_style_name",
    "prompt": "Normal\n", "summary": "Default responses from Claude",
    "summaryKey": "normal_style_summary", "isDefault": True,
}
# claude.ai stores these as cookies and echoes them as headers.
COOKIE_HEADERS = {
    "anthropic-device-id": "anthropic-device-id",
    "ajs_anonymous_id": "anthropic-anonymous-id",
    "activitySessionId": "x-activity-session-id",
}


def new_uuid() -> str:
    return str(uuid.uuid4())


def local_timezone() -> str:
    tz = os.environ.get("TZ", "").strip()
    if tz:
        return tz
    etc = Path("/etc/timezone")
    if etc.exists():
        name = etc.read_text(encoding="utf-8").strip()
        if name:
            return name
    return "UTC"


def completion_payload(
    prompt: str,
    model: str,
    timezone: str,
    project_uuid: str | None = None,
    uuids: Callable[[], str] = new_uuid,
) -> dict:
    params: dict = {
        "name": "",
        "model": model,
        "include_conversation_preferences": True,
        "paprika_mode": None,
        "compass_mode": COMPASS_MODE,
        "is_temporary": False,
    }
    if project_uuid:
        params["project_uuid"] = project_uuid
    return {
        "prompt": prompt,
        "timezone": timezone,
        "locale": "en-US",
        "model": model,
        "tools": [dict(t) for t in TOOLS],
        "turn_message_uuids": {"human_message_uuid": uuids(), "assistant_message_uuid": uuids()},
        "attachments": [],
        "files": [],
        "sync_sources": [],
        "rendering_mode": "messages",
        "parent_message_uuid": ROOT_PARENT_UUID,
        "personalized_styles": [dict(DEFAULT_STYLE)],
        "create_conversation_params": params,
    }


def api_headers(cookies: Mapping[str, str], stream: bool = False) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-version": "1.0.0",
        "anthropic-client-sha": CLIENT_SHA,
    }
    for cookie, header in COOKIE_HEADERS.items():
        if cookies.get(cookie):
            headers[header] = cookies[cookie]
    if stream:
        headers["accept"] = "text/event-stream"
    return headers

# ABOUTME: Typed wrappers over the claude.ai internal API, one per endpoint the engine uses.
# ABOUTME: Nothing else in the package knows a URL; the api callable is injected so tests run without a browser.
from __future__ import annotations

import json
from typing import Any, Callable, NamedTuple

CONVERSATION_QUERY = "rendering_mode=messages&render_all_tools=true"


class ApiResponse(NamedTuple):
    status: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text) if self.text else None


ApiCall = Callable[..., ApiResponse]


class ApiError(RuntimeError):
    def __init__(self, status: int, path: str, text: str):
        super().__init__(f"{path}: HTTP {status}: {text[:300]}")
        self.status = status
        self.path = path
        self.text = text


def choose_org(orgs: list[dict]) -> dict:
    """The org whose capabilities include chat; most capabilities wins (Console orgs are api-only)."""
    chat = [o for o in orgs if "chat" in (o.get("capabilities") or [])]
    if not chat:
        raise ApiError(0, "/api/organizations", "no organization with the chat capability")
    return max(chat, key=lambda o: len(o.get("capabilities") or []))


def exhausted(stream_text: str) -> str | None:
    """The reset time when the stream carried a message_limit event saying the window is exceeded."""
    for line in stream_text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if event.get("type") != "message_limit":
            continue
        limit = event.get("message_limit") or {}
        kind = str(limit.get("type") or limit.get("status") or "")
        if "exceeded" in kind:
            return str(limit.get("resetsAt") or limit.get("resets_at") or "unknown")
    return None


# The 429 error types that mean "the window is out, waiting is the only cure". Anything else
# a 429 carries (a short-term rate limit, an overloaded upstream) is retryable now.
USAGE_REFUSALS = frozenset({"exceeded_limit", "message_limit_exceeded", "usage_limit_exceeded"})


def refusal(body: str) -> tuple[str, str | None]:
    """The error type and reset time a 429 body carries; ("unknown", None) when it carries neither.

    A 429 body is plain JSON, not the SSE stream exhausted() reads, and claude.ai sometimes
    nests the real type and reset inside a JSON string in error.message.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return "unknown", None
    if not isinstance(payload, dict):
        return "unknown", None
    error = payload.get("error")
    if not isinstance(error, dict):
        error = payload
    kind = str(error.get("type") or payload.get("type") or "unknown")
    reset = error.get("resetsAt") or error.get("resets_at")
    message = error.get("message")
    if reset is None and isinstance(message, str):
        try:
            inner = json.loads(message)
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            kind = str(inner.get("type") or kind)
            reset = inner.get("resetsAt") or inner.get("resets_at")
    return kind, (str(reset) if reset is not None else None)


class Client:
    def __init__(self, api: ApiCall):
        self._api = api

    def _get_json(self, path: str) -> Any:
        r = self._api("GET", path)
        if r.status != 200:
            raise ApiError(r.status, path, r.text)
        return r.json()

    def account_email(self) -> str | None:
        """The email claude.ai reports for this session, so no caller has to trust a configured label.

        None when the endpoint is unavailable: an identity read is diagnostic, and failing it
        must not take down a command that would otherwise have worked.
        """
        try:
            account = self._get_json("/api/account")
        except ApiError:
            return None
        if isinstance(account, dict) and account.get("email_address"):
            return str(account["email_address"])
        return None

    def orgs(self) -> list[dict]:
        return list(self._get_json("/api/organizations") or [])

    def flags(self, org: dict) -> list[dict]:
        return list(org.get("active_flags") or [])

    def model_available(self, org_uuid: str, model: str) -> bool:
        path = f"/api/organizations/{org_uuid}/model_configs/{model}"
        r = self._api("GET", path)
        if r.status == 200:
            return True
        if r.status == 404:
            return False
        raise ApiError(r.status, path, r.text)

    def projects(self, org_uuid: str) -> list[dict]:
        return list(self._get_json(f"/api/organizations/{org_uuid}/projects") or [])

    def find_project(self, org_uuid: str, name: str) -> str | None:
        for p in self.projects(org_uuid):
            if p.get("name") == name:
                return str(p.get("uuid"))
        return None

    def start_research(self, org_uuid: str, conversation_uuid: str, payload: dict) -> ApiResponse:
        path = f"/api/organizations/{org_uuid}/chat_conversations/{conversation_uuid}/completion"
        return self._api("POST", path, body=payload, stream=True)

    def rename(self, org_uuid: str, conversation_uuid: str, name: str) -> None:
        path = f"/api/organizations/{org_uuid}/chat_conversations/{conversation_uuid}"
        r = self._api("PUT", path, body={"name": name})
        if r.status not in (200, 202):
            raise ApiError(r.status, path, r.text)

    def conversation(self, org_uuid: str, conversation_uuid: str) -> dict:
        return self._get_json(
            f"/api/organizations/{org_uuid}/chat_conversations/{conversation_uuid}?{CONVERSATION_QUERY}"
        )

    def stop_response(self, org_uuid: str, conversation_uuid: str) -> None:
        path = f"/api/organizations/{org_uuid}/chat_conversations/{conversation_uuid}/stop_response"
        r = self._api("POST", path, body={})
        if r.status >= 300:
            raise ApiError(r.status, path, r.text)

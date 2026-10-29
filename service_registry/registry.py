# ABOUTME: services.yaml load/save/validate plus two-way reconciliation against observed state.
# ABOUTME: Declared-but-down AND running-but-undeclared are both findings; the second stops drift.
from __future__ import annotations

import re
from pathlib import Path

import yaml

REQUIRED_FIELDS = ("id", "host", "kind", "intended", "start", "stop", "check")
VALID_INTENDED = ("running", "stopped", "on-demand")

# `owner`: the first 8 hex characters of the registering Claude session's UUID (ruling of 2026-10-29). A lane's
# ack/close proves a unit belongs to ANOTHER lane only through such an id, so a new entry must carry one. Entries
# registered before the field existed stay valid without it and gain it when an updating command touches them.
OWNER_FIELD = "owner"
OWNER = re.compile(r"^[0-9a-f]{8}$")
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
SESSION_UUID = re.compile(r"^([0-9a-f]{8})(?:-|$)")


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()) or []


def save(services: list[dict], path: Path) -> None:
    path.write_text(yaml.safe_dump(services, sort_keys=False, default_flow_style=False))


def session_owner(env: dict[str, str]) -> str | None:
    """The 8-hex owner id of the session in `env` (its CLAUDE_CODE_SESSION_ID), or None when it is unset or malformed."""
    found = SESSION_UUID.match(env.get(SESSION_ENV, "").strip())
    return found.group(1) if found else None


def validate(services: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()

    for entry in services:
        sid = entry.get("id", "<no id>")
        for field in REQUIRED_FIELDS:
            if not entry.get(field):
                problems.append(f"{sid}: missing required field '{field}'")
        intended = entry.get("intended")
        if intended not in VALID_INTENDED:
            problems.append(
                f"{sid}: 'intended' is {intended!r}, must be one of {VALID_INTENDED}"
            )
        # A service deliberately off is exactly the case that needs a written reason,
        # otherwise nobody can tell it apart from something that silently died.
        if intended == "stopped" and not entry.get("why"):
            problems.append(f"{sid}: intended=stopped requires a 'why'")
        owner = entry.get(OWNER_FIELD)
        # A str check first: an unquoted all-digit id loads from YAML as an int, which no reader matches as an id.
        if OWNER_FIELD in entry and not (isinstance(owner, str) and OWNER.match(owner)):
            problems.append(
                f"{sid}: '{OWNER_FIELD}' is {entry[OWNER_FIELD]!r}, must be a session's first 8 hex characters"
            )
        if sid in seen:
            problems.append(f"duplicate id: {sid}")
        seen.add(sid)

    return problems


def register(services: list[dict], **fields) -> list[dict]:
    sid = fields.get("id")
    if any(s.get("id") == sid for s in services):
        raise ValueError(f"service {sid!r} is already registered; use `service set`")
    return services + [fields]


def reconcile(declared: list[dict], observed_running: set[str]) -> dict:
    by_id = {s["id"]: s for s in declared}

    should_run_but_stopped = sorted(
        sid for sid, s in by_id.items()
        if s.get("intended") == "running" and sid not in observed_running
    )
    should_be_stopped_but_running = sorted(
        sid for sid, s in by_id.items()
        if s.get("intended") == "stopped" and sid in observed_running
    )
    undeclared_running = sorted(observed_running - set(by_id))

    return {
        "should_run_but_stopped": should_run_but_stopped,
        "should_be_stopped_but_running": should_be_stopped_but_running,
        "undeclared_running": undeclared_running,
    }

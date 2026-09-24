# ABOUTME: services.yaml load/save/validate plus two-way reconciliation against observed state.
# ABOUTME: Declared-but-down AND running-but-undeclared are both findings; the second stops drift.
from __future__ import annotations

from pathlib import Path

import yaml

REQUIRED_FIELDS = ("id", "host", "kind", "intended", "start", "stop", "check")
VALID_INTENDED = ("running", "stopped", "on-demand")


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()) or []


def save(services: list[dict], path: Path) -> None:
    path.write_text(yaml.safe_dump(services, sort_keys=False, default_flow_style=False))


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

# ABOUTME: Spawns the detached claude --bg research session and records its manifest.
# ABOUTME: The only impure module; the subprocess runner is injected so logic stays testable.
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .charter import Charter, render_charter
from .manifest import (
    MANIFEST_NAME,
    Manifest,
    active_runs,
    find_runs,
    make_run_id,
    write_manifest,
)
from .runner import build_runner_prompt

CONCURRENCY_CAP = 2

# `claude --bg` prints "backgrounded <sep> <id>"; the separator is a middle dot in
# practice, but do not make the parser depend on one glyph surviving a version bump.
_SESSION_RE = re.compile(r"backgrounded\s*[^\w\s]?\s*([0-9a-f]{6,})", re.IGNORECASE)


class LaunchError(RuntimeError):
    """Raised when a detached run cannot be started, or must not be."""


def parse_session_id(stdout: str) -> str:
    match = _SESSION_RE.search(stdout)
    if not match:
        raise LaunchError(f"could not find a session id in claude output:\n{stdout}")
    return match.group(1)


# `claude agents` refuses a non-TTY stdout and tells you to use --json instead, so the
# JSON listing is the only form usable from a script. A background entry looks like:
#   {"id": "4c00ef07", "kind": "background", "sessionId": "4c00ef07-0c5c-...",
#    "status": "idle", "state": "done"}
# `status` stays "idle" after a run finishes, so `state` is the liveness field.
DEAD_STATES = {"done", "exited", "stopped", "failed", "killed"}


def session_alive(session_id: str, runner=subprocess.run) -> bool:
    try:
        result = runner(
            ["claude", "agents", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    try:
        entries = json.loads(result.stdout or "[]")
    except (json.JSONDecodeError, TypeError):
        return False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        matches = entry.get("id") == session_id or str(
            entry.get("sessionId", "")
        ).startswith(session_id)
        if matches:
            return str(entry.get("state", "")).lower() not in DEAD_STATES
    return False


def launch(
    charter: Charter,
    out_dir: Path,
    runs_root: Path,
    model: str = "fable",
    effort: str = "max",
    now: datetime | None = None,
    notify_script: str | None = None,
    force: bool = False,
    runner=subprocess.run,
) -> Manifest:
    out_dir = Path(out_dir)
    if (out_dir / MANIFEST_NAME).exists():
        raise LaunchError(f"{out_dir} already holds a run; use a fresh directory")

    running = active_runs(find_runs(Path(runs_root))) if Path(runs_root).exists() else []
    if len(running) >= CONCURRENCY_CAP and not force:
        ids = ", ".join(m.run_id for m in running)
        raise LaunchError(
            f"{len(running)} runs already running ({ids}); "
            f"cap is {CONCURRENCY_CAP}. Re-run with force to override."
        )

    now = now or datetime.now().astimezone()
    out_dir.mkdir(parents=True, exist_ok=True)
    charter_path = out_dir / "charter.md"
    charter_path.write_text(render_charter(charter), encoding="utf-8")

    prompt = build_runner_prompt(charter, out_dir, notify_script=notify_script)
    cmd = ["claude", "--bg", "--model", model, "--effort", effort, prompt]
    result = runner(
        cmd, cwd=str(out_dir), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise LaunchError(
            f"claude --bg exited {result.returncode}: {result.stderr or result.stdout}"
        )

    manifest = Manifest(
        run_id=make_run_id(charter.question, now),
        bg_session_id=parse_session_id(result.stdout or ""),
        engine="local",
        model=model,
        effort=effort,
        charter=str(charter_path),
        out_dir=str(out_dir),
        started_at=now.isoformat(),
        status="running",
    )
    write_manifest(out_dir, manifest)
    return manifest

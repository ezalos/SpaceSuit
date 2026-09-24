# ABOUTME: The claude-web run record: run.json read and write, run ids, and discovery under a runs root.
# ABOUTME: Writes are atomic so the watcher never reads a half-written record.
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

RUN_FILE = "run.json"
ENGINE = "claude-web"
SLUG_MAX = 40
FALLBACK_SLUG = "untitled"


class RunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STALE = "stale"
    NEEDS_REPLY = "needs-reply"
    HALTED = "halted"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    question: str
    status: str
    org_uuid: str
    conversation_uuid: str
    chat_url: str
    model: str
    charter: str
    out_dir: str
    started_at: str
    engine: str = ENGINE
    task_id: str | None = None
    project_uuid: str | None = None
    collected_at: str | None = None
    notified_at: str | None = None
    poll_failures: int = 0
    reason: str | None = None
    account: str | None = None
    name: str | None = None  # explicit archive name chosen at launch; None falls back to a slug of the question


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:SLUG_MAX].rstrip("-") or FALLBACK_SLUG


def make_run_id(question: str, now: datetime) -> str:
    return f"{now:%Y-%m-%d-%H%M%S}-{slugify(question)}"


def chat_url(conversation_uuid: str) -> str:
    return f"https://claude.ai/chat/{conversation_uuid}"


def write_run(out_dir: Path, rec: RunRecord) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / RUN_FILE
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(rec), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_run(out_dir: Path) -> RunRecord:
    data = json.loads((out_dir / RUN_FILE).read_text(encoding="utf-8"))
    return RunRecord(**data)


def find_runs(root: Path) -> list[RunRecord]:
    runs: list[RunRecord] = []
    for path in sorted(Path(root).rglob(RUN_FILE)):
        try:
            rec = read_run(path.parent)
        # ValueError covers bad JSON; TypeError a v1 manifest whose keys do not fit.
        # One foreign or corrupt file must never take down discovery.
        except (ValueError, TypeError, OSError):
            continue
        if rec.engine == ENGINE:
            runs.append(rec)
    return runs


class AmbiguousRunId(Exception):
    """A prefix matched more than one run. Never guess which one Louis meant."""

    def __init__(self, prefix: str, candidates: list[str]):
        self.prefix = prefix
        self.candidates = candidates
        super().__init__(f"run id {prefix!r} matches {len(candidates)}: " + ", ".join(candidates))


def find_run(root: Path, run_id: str) -> RunRecord | None:
    """The run with this id, or the only one it is a prefix of.

    Run ids are ~58 characters, so a prefix is how they actually get typed. Exact wins over a
    prefix match so a run whose id is a prefix of another is still reachable by its full id.
    """
    runs = find_runs(root)
    exact = next((r for r in runs if r.run_id == run_id), None)
    if exact is not None:
        return exact
    matches = [r for r in runs if r.run_id.startswith(run_id)]
    if len(matches) > 1:
        raise AmbiguousRunId(run_id, sorted(r.run_id for r in matches))
    return matches[0] if matches else None


def list_all(root: Path) -> list[dict]:
    """Every run.json under root, from any engine, as {run_id, engine, status, link}.

    Tolerant of the claude-web RunRecord shape (link is chat_url) and the older v1
    manifest shape (bg_session_id present, link is bg_session_id). A file this repo
    cannot make sense of is skipped, same discipline as find_runs: one bad run.json
    must never take down `list`.
    """
    out: list[dict] = []
    for path in sorted(Path(root).rglob(RUN_FILE)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or "run_id" not in data or "status" not in data:
            continue
        link = data["bg_session_id"] if "bg_session_id" in data else data.get("chat_url")
        out.append({
            "run_id": data["run_id"], "engine": data.get("engine"),
            "status": data["status"], "link": link,
        })
    return out


def running_runs(root: Path) -> list[RunRecord]:
    return [r for r in find_runs(root) if r.status == RunStatus.RUNNING.value]


def update_run(out_dir: Path, **changes) -> RunRecord:
    rec = replace(read_run(out_dir), **changes)
    write_run(out_dir, rec)
    return rec

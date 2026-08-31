# ABOUTME: The run manifest: run ids, run.json read and write, and discovery of past runs.
# ABOUTME: Writes are atomic so a reader never sees a half-written manifest.
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

MANIFEST_NAME = "run.json"
SLUG_MAX = 40
FALLBACK_SLUG = "untitled"


@dataclass(frozen=True)
class Manifest:
    run_id: str
    bg_session_id: str
    engine: str
    model: str
    effort: str
    charter: str
    out_dir: str
    started_at: str
    status: str


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-")
    # A question of pure punctuation would otherwise yield an empty slug, making a run id
    # that ends in a bare dash and collides with every other such run in the same second.
    return slug or FALLBACK_SLUG


def make_run_id(question: str, now: datetime) -> str:
    return f"{now:%Y-%m-%d-%H%M%S}-{slugify(question)}"


def write_manifest(out_dir: Path, m: Manifest) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / MANIFEST_NAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(m), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_manifest(out_dir: Path) -> Manifest:
    data = json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    return Manifest(**data)


def find_runs(root: Path) -> list[Manifest]:
    runs: list[Manifest] = []
    for path in sorted(root.rglob(MANIFEST_NAME)):
        try:
            runs.append(read_manifest(path.parent))
        # ValueError covers both json.JSONDecodeError and UnicodeDecodeError (an
        # undecodable run.json); TypeError covers a JSON object whose keys do not match
        # the Manifest fields. One corrupt file must never take down discovery, because
        # launch() calls find_runs for its concurrency cap.
        except (ValueError, TypeError, OSError):
            continue
    return runs


def active_runs(runs: Iterable[Manifest]) -> list[Manifest]:
    return [m for m in runs if m.status == "running"]

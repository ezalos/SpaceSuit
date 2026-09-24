# ABOUTME: Archives a collected run under an explicit name into a library directory: report files, sanitised metadata, a README written once, an index.
# ABOUTME: Pure filesystem, no git, no network; account identifiers and absolute paths never leave the runs root.
from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from .report import REPORT_NAME, RESULT_NAME, SOURCES_NAME
from .runs import RunRecord

ARCHIVE_FILE = "archive.json"
INDEX_NAME = "README.md"
README_NAME = "README.md"
CHARTER_NAME = "charter.md"
VERIFICATION_JSON = "verification.json"
VERIFICATION_MD = "verification.md"
PAPERS_LINK = "papers"
NAME_MAX = 60
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
COPIED = (CHARTER_NAME, REPORT_NAME, SOURCES_NAME, RESULT_NAME, VERIFICATION_JSON, VERIFICATION_MD)
GRADE_ORDER = ("quoted", "live", "misquoted", "dead", "unverifiable")


class ArchiveError(Exception):
    pass


def valid_name(name: str | None) -> bool:
    return bool(name) and len(name) <= NAME_MAX and bool(NAME_RE.match(name))


def default_name(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    return slug[:NAME_MAX].rstrip("-") or "untitled"


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def launch_day(rec: RunRecord) -> str:
    """The day Louis launched the run, as the run id stamps it in local time; started_at is UTC and can already be tomorrow."""
    m = _DAY_RE.match(rec.run_id)
    return m.group(0) if m else rec.started_at[:10]


def archive_dirname(rec: RunRecord, name: str | None = None) -> str:
    return f"{launch_day(rec)}-{name or rec.name or default_name(rec.question)}"


def _write_atomic(target: Path, text: str) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _copy_atomic(src: Path, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, target)


def _read_meta(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def grade_tally(run_dir: Path) -> dict[str, int]:
    result = _read_meta(run_dir / RESULT_NAME) or {}
    counts = Counter(str(s.get("grade", "")) for s in result.get("sources", []) if isinstance(s, dict))
    return {g: counts[g] for g in GRADE_ORDER if counts[g]}


def format_grades(grades: dict[str, int]) -> str:
    return ", ".join(f"{n} {g}" for g, n in grades.items()) or "none"


def _find_existing(archive_root: Path, run_id: str) -> Path | None:
    for meta_path in sorted(archive_root.glob(f"*/{ARCHIVE_FILE}")):
        meta = _read_meta(meta_path)
        if meta and meta.get("run_id") == run_id:
            return meta_path.parent
    return None


def _readme(rec: RunRecord, meta: dict) -> str:
    return (
        f"# {rec.question}\n\n"
        f"- Run `{rec.run_id}` ({meta['engine']}, {meta['model']}, account {meta['account'] or 'unknown'})\n"
        f"- Started {meta['started_at']}; collected {meta['collected_at'] or 'not yet'}\n"
        f"- Chat: {meta['chat_url']}\n"
        f"- Files: `{CHARTER_NAME}` (the question as launched), `{REPORT_NAME}` (the original report, frozen), "
        f"`{SOURCES_NAME}` and `{RESULT_NAME}` (the engine's graded citations), "
        f"`{VERIFICATION_JSON}` / `{VERIFICATION_MD}` when an independent claims check was run\n"
        f"- Grades at archive time: {format_grades(meta['grades'])}\n"
        f"- Papers: none linked. To link a Drive folder: a relative symlink `{PAPERS_LINK}` to it, and its mirror-relative path here.\n\n"
        "## Used in\n\n(nothing recorded yet)\n\n"
        "## Corrections\n\n(none recorded; a claim in the report is REPORTED, not reproduced)\n"
    )


def archive_run(
    rec: RunRecord, archive_root: Path, name: str | None = None, today: Callable[[], date] = date.today,
) -> Path:
    """Copy the run's report files under <started date>-<name>, write archive.json, keep an existing README, refresh the index."""
    if name is not None and not valid_name(name):
        raise ArchiveError(f"name {name!r}: lowercase kebab-case, at most {NAME_MAX} characters")
    chosen = name or rec.name or default_name(rec.question)
    if not valid_name(chosen):
        raise ArchiveError(f"name {chosen!r}: lowercase kebab-case, at most {NAME_MAX} characters")
    archive_root = Path(archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    dest = archive_root / archive_dirname(rec, chosen)
    existing = _find_existing(archive_root, rec.run_id)
    if existing is not None and existing != dest:
        if dest.exists():
            raise ArchiveError(f"{dest.name} already exists; cannot rename {existing.name} onto it")
        existing.rename(dest)
    elif dest.exists():
        owner = (_read_meta(dest / ARCHIVE_FILE) or {}).get("run_id")
        if owner != rec.run_id:
            raise ArchiveError(f"{dest.name} belongs to run {owner or '(unknown)'}, not {rec.run_id}")
    dest.mkdir(exist_ok=True)
    src = Path(rec.out_dir)
    for fn in COPIED:
        if (src / fn).exists():
            _copy_atomic(src / fn, dest / fn)
    meta = {
        "run_id": rec.run_id, "name": chosen, "question": rec.question, "engine": rec.engine,
        "model": rec.model, "account": rec.account, "started_at": rec.started_at,
        "collected_at": rec.collected_at, "chat_url": rec.chat_url, "grades": grade_tally(dest),
        "checked": (dest / VERIFICATION_JSON).exists(), "archived_at": today().isoformat(),
    }
    _write_atomic(dest / ARCHIVE_FILE, json.dumps(meta, indent=2) + "\n")
    if not (dest / README_NAME).exists():
        _write_atomic(dest / README_NAME, _readme(rec, meta))
    write_index(archive_root)
    return dest


def papers_of(entry: Path) -> str:
    link = entry / PAPERS_LINK
    if link.is_symlink():
        return Path(os.readlink(link)).name
    if link.is_dir():
        return ", ".join(Path(os.readlink(p)).name if p.is_symlink() else p.name for p in sorted(link.iterdir()))
    return ""


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def write_index(archive_root: Path) -> Path:
    """Regenerate <archive_root>/README.md from every <dir>/archive.json, newest directory first."""
    archive_root = Path(archive_root)
    rows = []
    for meta_path in sorted(archive_root.glob(f"*/{ARCHIVE_FILE}"), reverse=True):
        meta = _read_meta(meta_path)
        if not meta or "run_id" not in meta:
            continue
        entry = meta_path.parent
        rows.append(
            f"| {entry.name[:10]} | [{_cell(str(meta.get('name', entry.name)))}]({entry.name}/) | {_cell(str(meta.get('question', '')))} "
            f"| {format_grades(meta.get('grades') or {})} | {'yes' if meta.get('checked') else 'no'} | {_cell(papers_of(entry))} |"
        )
    text = (
        "# Deep research\n\n"
        "One directory per run, `<start date>-<name>/`: the charter, the original report (frozen), the engine's graded "
        "sources, `verification.json` when an independent claims check was run, and `papers` linking the Drive folder that "
        "holds the papers. Every number in a report is REPORTED by that report, not reproduced. This table is regenerated by "
        "`deep-research-web` on every archive; write in a run's own `README.md`, not here.\n\n"
        "| Date | Name | Question | Citation grades | Checked | Papers |\n|---|---|---|---|---|---|\n"
        + "\n".join(rows) + ("\n" if rows else "")
    )
    target = archive_root / INDEX_NAME
    _write_atomic(target, text)
    return target

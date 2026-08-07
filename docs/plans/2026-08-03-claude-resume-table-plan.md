# Claude Resume Table Implementation Plan

> **Status: implemented 2026-08-03** (commits 8e02177, ecd14d1). 76 tests pass.
> Kept for the reasoning, the measured evidence, and the rejected alternatives.
> Two changes from what is written below:
>
> - **A Task 0 was needed and is not in this plan.** `tests/test_tmux_save_restore.py`'s
>   fixture overrode `TMUX_SAVE_DIR` but not `TMUX_SAVE_HISTORY_DIR`, so every save
>   test wrote junk into the real `~/.tmux-save-history` and ran the retention prune
>   over it. Running this plan's Task 1 as written would have polluted it four more
>   times. Fixed first, in 243096c.
> - **`assign_most_recent` gained a third tier** (lineage before strangers) after
>   testing against live data. Task 6 below was updated and matches what shipped.
>   Task 9 was not: the shipped `__main__.py` also has `pane_is_busy` / `--force`,
>   which skip panes already running Claude. Read the source for that part.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `trestore`'s 26 sequential per-pane Claude prompts with one difference-focused table where columns are candidate resume policies and every candidate shows its human-readable title.

**Architecture:** A new stdlib-only Python package `claude_resume/` owns the data logic (snapshot reading, candidate construction, the assignment invariant, column selection, rendering) and the interactive loop. `tmux-restore.sh` drops its inline prompt loop and calls `python3 -m claude_resume`. `tmux-save.sh` gains an `origin` marker so a manually-taken save can become a column.

**Tech Stack:** Python 3 (stdlib only), bash, pytest. Design doc: `docs/plans/2026-08-03-claude-resume-table-design.md`.

## Deviation from the design doc

The design named the deliverable `scripts/claude-resume.sh`. This plan implements it as a Python package instead, for one decisive reason: **the assignment algorithm needs associative arrays, and macOS ships bash 3.2, which has none.** `tmux-save.sh` already carries a comment pinning its retention logic to "pure sort/awk, portable to macOS bash 3.2, no bash-4 assoc arrays" for exactly this reason. Building a pane-keyed candidate map, an mtime sort, a greedy assignment with reservation, and a multi-column diff render in bash 3.2 would be both painful and fragile.

Supporting reasons: the repo's Python subsystems (`agents_dashboard/`, `netwatch/`, `src_dotfiles/`, `nat_manager/`) are top-level packages tested directly by pytest, which is a far better fit for pure logic than shelling out to bash; and `tmux-save.sh` already hard-depends on `python3` (it parses Claude session JSON with `python3 -c` at line 86).

Invocation is bare `python3 -m claude_resume`, not `uv run`, deviating from the CLAUDE.md rule. Rationale: this runs on the restore path, potentially right after a reboot in a degraded environment. The package is stdlib-only so it needs no venv, and `tmux-save.sh` set the precedent for bare `python3` in this subsystem for the same robustness reason. Tests still run under `uv run pytest`.

## Global Constraints

- **Stdlib only.** `claude_resume/` imports nothing outside the Python standard library. No new entries in `pyproject.toml`.
- **No cross-package imports.** `claude_resume/` must not import from `agents_dashboard/`. The two subsystems stay independent; a cross-check test guards the one duplicated primitive.
- **macOS and Linux.** Any shell touched must stay bash 3.2 compatible: no associative arrays, no `mapfile`, no `readarray`. Watch `stat`, `sed`, `date` flag differences.
- **Never touch the default tmux socket.** Every test that starts a server uses the `tmux_env` fixture pattern from `tests/test_tmux_save_restore.py` (`TMUX_TMPDIR` at a short temp dir, killed on teardown).
- **`tmux-save.sh` is cron-critical.** Its default path must stay behavior-compatible: a save with no flags and no tty still produces a complete snapshot, with the marker as the only addition.
- **Every new file starts with a 2-line `ABOUTME:` comment.**
- **Never `rm`.** Use `rip`.
- Run tests with `uv run pytest`.

## File Structure

| File | Responsibility |
| --- | --- |
| `claude_resume/__init__.py` | Empty package marker |
| `claude_resume/models.py` | `PaneKey`, `Candidate`, `SnapshotRef`, `Column` dataclasses |
| `claude_resume/claude_paths.py` | cwd to project-dir slug, transcript lookup with glob fallback |
| `claude_resume/titles.py` | Title resolution chain plus per-id cache |
| `claude_resume/snapshots.py` | Read `state.tsv` / `saved_at` / `origin`; enumerate snapshots newest-first |
| `claude_resume/candidates.py` | Candidate sets and the assignment invariant |
| `claude_resume/columns.py` | Most-recent, change-driven and manual column construction |
| `claude_resume/render.py` | Difference-focused table string |
| `claude_resume/__main__.py` | CLI, key loop, `tmux send-keys` execution |
| `tests/test_claude_resume.py` | All package tests (new) |
| `tests/test_tmux_save_restore.py` | Extended with origin-marker tests |
| `scripts/tmux-save.sh` | `--origin` flag, writes `origin` file |
| `scripts/tmux-snapshots.sh` | Origin column in `--list` |
| `scripts/tmux-restore.sh` | Drop lines 272-378, call the package |
| `scripts/tmux-save-on-shutdown.service` | `--origin shutdown` |
| `dotfiles/.zshrc` | `cresume` alias |

---

### Task 1: Origin marker in tmux-save.sh

**Files:**
- Modify: `scripts/tmux-save.sh:29-35` (arg loop), and after line 108 (`saved_at` write)
- Test: `tests/test_tmux_save_restore.py`

**Interfaces:**
- Consumes: nothing
- Produces: an `origin` file inside every snapshot dir containing exactly one of `manual`, `cron`, `shutdown`, followed by a newline.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tmux_save_restore.py`:

```python
def test_save_records_origin_cron_when_not_a_tty(tmux_env, tmp_path):
    """No flag and no tty (cron, systemd) must record origin=cron."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0

    assert run_save(tmux_env).returncode == 0

    origin = Path(tmux_env["TMUX_SAVE_DIR"]) / "origin"
    assert origin.read_text().strip() == "cron"


def test_save_records_explicit_origin(tmux_env, tmp_path):
    """--origin wins over the tty heuristic."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0

    result = subprocess.run(
        [str(SAVE), "--origin", "shutdown"], env=tmux_env, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=60,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    origin = Path(tmux_env["TMUX_SAVE_DIR"]) / "origin"
    assert origin.read_text().strip() == "shutdown"


def test_save_rejects_unknown_origin(tmux_env, tmp_path):
    """A typo must fail loudly rather than silently record garbage."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0

    result = subprocess.run(
        [str(SAVE), "--origin", "bogus"], env=tmux_env, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=60,
    )

    assert result.returncode != 0
    assert "bogus" in (result.stdout + result.stderr)


def test_origin_propagates_into_history(tmux_env, tmp_path):
    """History snapshots are cp -a copies, so the marker must come along."""
    history = tmp_path / "history"
    env = dict(tmux_env)
    env["TMUX_SAVE_HISTORY_DIR"] = str(history)
    assert tmux(env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0

    result = subprocess.run(
        [str(SAVE), "--origin", "manual"], env=env, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=60,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    copies = sorted(history.glob("*/origin"))
    assert len(copies) == 1
    assert copies[0].read_text().strip() == "manual"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tmux_save_restore.py -k origin -v`
Expected: 4 FAILED. The first three fail on `FileNotFoundError` for the `origin` path; `test_save_rejects_unknown_origin` fails because the current catch-all `*) shift` swallows unknown args and exits 0.

- [ ] **Step 3: Add the flag and validation**

In `scripts/tmux-save.sh`, replace the arg loop at lines 29-35:

```bash
TMUX_SOCKET="${TMUX_SOCKET:-}"
ORIGIN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -L) TMUX_SOCKET="${2:-}"; shift 2 || true ;;
    --origin) ORIGIN="${2:-}"; shift 2 || true ;;
    *) shift ;;
  esac
done

# Provenance of this snapshot, so the resume table can offer "the checkpoint you
# took by hand" as a column. Explicit flag wins; otherwise a tty means a human
# typed `tsave`, and no tty means cron or the shutdown unit.
if [[ -z "$ORIGIN" ]]; then
  if [[ -t 0 ]]; then ORIGIN="manual"; else ORIGIN="cron"; fi
fi
case "$ORIGIN" in
  manual|cron|shutdown) ;;
  *) echo "Unknown --origin '$ORIGIN' (expected manual, cron or shutdown)" >&2; exit 2 ;;
esac

tmux() { command tmux ${TMUX_SOCKET:+-L "$TMUX_SOCKET"} "$@"; }
```

- [ ] **Step 4: Write the marker into the staging dir**

In `scripts/tmux-save.sh`, immediately after the `saved_at` line (currently line 108):

```bash
date '+%Y-%m-%d %H:%M:%S' > "$STAGING_DIR/saved_at"
printf '%s\n' "$ORIGIN" > "$STAGING_DIR/origin"
```

Writing into `STAGING_DIR` before the swap is what makes the marker land atomically with the rest of the snapshot, and the later `cp -a "$SAVE_DIR" "$HISTORY_DIR/..."` carries it into history with no further change.

- [ ] **Step 5: Run the whole save/restore suite**

Run: `uv run pytest tests/test_tmux_save_restore.py -v`
Expected: 10 passed (6 pre-existing plus 4 new). The pre-existing tests must still pass, which is the cron-compatibility check.

- [ ] **Step 6: Commit**

```bash
git add scripts/tmux-save.sh tests/test_tmux_save_restore.py
git commit -m "feat(tsave): record snapshot origin (manual|cron|shutdown)"
```

---

### Task 2: Origin column in tsnaps --list

**Files:**
- Modify: `scripts/tmux-snapshots.sh:46-55` (`row_label`), `:57-82` (`render_preview`)
- Test: `tests/test_claude_resume.py` (create)

**Interfaces:**
- Consumes: the `origin` file from Task 1
- Produces: nothing consumed by later tasks. Standalone improvement.

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_resume.py`:

```python
# ABOUTME: Tests the claude_resume package (candidates, columns, rendering) and tsnaps origin output.
# ABOUTME: Pure-logic tests run against synthetic snapshot and transcript fixtures, no tmux server.

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPS = REPO / "scripts" / "tmux-snapshots.sh"


def write_snapshot(root: str, name: str, saved_at: str, rows, origin=None):
    """Build a snapshot dir. rows: list of (session, win, win_name, layout, pane,
    cwd, is_claude, win_active, claude_id)."""
    d = Path(root) / name
    (d / "pane_contents").mkdir(parents=True)
    (d / "state.tsv").write_text(
        "".join("\t".join(str(c) for c in r) + "\n" for r in rows)
    )
    (d / "saved_at").write_text(saved_at + "\n")
    if origin is not None:
        (d / "origin").write_text(origin + "\n")
    return d


def test_tsnaps_list_shows_origin(tmp_path):
    save_dir = tmp_path / "save"
    history = tmp_path / "history"
    history.mkdir()
    write_snapshot(
        str(tmp_path), "save", "2026-08-03 16:00:00",
        [("alpha", 0, "win", "layout", 0, "/tmp", 1, 1, "aaaa1111")],
        origin="manual",
    )
    write_snapshot(
        str(history), "2026-08-03_12-00-00", "2026-08-03 12:00:00",
        [("alpha", 0, "win", "layout", 0, "/tmp", 1, 1, "aaaa1111")],
        origin="cron",
    )
    # A pre-marker snapshot, as all 101 existing ones are.
    write_snapshot(
        str(history), "2026-08-02_12-00-00", "2026-08-02 12:00:00",
        [("alpha", 0, "win", "layout", 0, "/tmp", 1, 1, "aaaa1111")],
    )

    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TMUX_SAVE_DIR": str(save_dir), "TMUX_SAVE_HISTORY_DIR": str(history)}
    result = subprocess.run([str(SNAPS), "--list"], env=env,
                            capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 3
    assert "manual" in lines[0]
    assert "cron" in lines[1]
    assert "?" in lines[2], "unmarked snapshots must render as unknown, not blank"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_claude_resume.py -k tsnaps -v`
Expected: FAIL. `row_label` emits no origin, so `"manual" in lines[0]` is False.

- [ ] **Step 3: Add origin to row_label**

In `scripts/tmux-snapshots.sh`, add a reader and widen the row. Replace `row_label` (lines 46-55):

```bash
# Provenance of a snapshot: manual (typed by hand), cron, shutdown, or '?' for
# snapshots taken before the marker existed.
snap_origin() {
  local o="$1/origin"
  [[ -f "$o" ]] && head -1 "$o" || echo "?"
}

# One pretty line for a snapshot dir.
row_label() {
  local dir="$1" tag="${2:-}" meta
  meta=$(snap_meta "$dir")
  local saved; saved=$(cat "$dir/saved_at" 2>/dev/null || echo "?")
  local origin; origin=$(snap_origin "$dir")
  # shellcheck disable=SC2086
  set -- $meta
  printf '%-19s  %-8s  %2s sess  %3s panes  %2s claude  %s%s' \
    "$saved" "$origin" "$1" "$2" "$3" "$(basename "$dir")" "$tag"
}
```

- [ ] **Step 4: Add origin to the preview header**

In `render_preview`, after the `saved_at` line:

```bash
  echo "saved_at : $(cat "$dir/saved_at" 2>/dev/null)"
  echo "origin   : $(snap_origin "$dir")"
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_claude_resume.py -k tsnaps -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/tmux-snapshots.sh tests/test_claude_resume.py
git commit -m "feat(tsnaps): show snapshot origin in --list and --preview"
```

---

### Task 3: Package skeleton, models and Claude path resolution

**Files:**
- Create: `claude_resume/__init__.py`, `claude_resume/models.py`, `claude_resume/claude_paths.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `PaneKey(session: str, window: int, pane: int, cwd: str)` frozen dataclass, `sort_key` property returning `(session, window, pane)`
  - `Candidate(session_id: str, mtime: float, exists: bool, lineage: bool)` dataclass. `lineage` is True when this pane actually held the conversation; False for a "stranger" merely found in the cwd.
  - `SnapshotRef(path: Path, saved_at: str, origin: str)` dataclass
  - `Column(key: str, label: str, assignment: dict[PaneKey, str])` dataclass
  - `project_slug(cwd: str) -> str`
  - `transcript_path(cwd: str, session_id: str, projects_dir: Path) -> Path | None`
  - `list_transcripts(cwd: str, projects_dir: Path) -> list[tuple[str, float]]` returning `(session_id, mtime)` pairs

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
from claude_resume.claude_paths import list_transcripts, project_slug, transcript_path
from claude_resume.models import Candidate, Column, PaneKey, SnapshotRef


def test_project_slug_replaces_non_alphanumerics():
    assert project_slug("/home/ezalos/Setup") == "-home-ezalos-Setup"
    assert project_slug("/home/ezalos/42/monorepo_quater") == "-home-ezalos-42-monorepo-quater"
    assert project_slug("/home/ezalos/Work/web_wm_onnx") == "-home-ezalos-Work-web-wm-onnx"


def test_project_slug_agrees_with_agents_dashboard_on_real_paths():
    """The dashboard has its own copy of this rule. They must not drift on any
    path shape that actually occurs. Imported inside the test so the package
    itself keeps no dependency on agents_dashboard."""
    from agents_dashboard.claude_sessions import project_slug as other
    for p in ["/home/user/Setup", "/home/user/42/monorepo_quater",
              "/home/user/Work/web_wm_onnx", "/home/user/42/V-Jaygent",
              "/home/user/nested/dir-with-dashes"]:
        assert project_slug(p) == other(p), p


def test_transcript_path_finds_direct_hit(tmp_path):
    d = tmp_path / "-tmp-proj"
    d.mkdir()
    (d / "abc123.jsonl").write_text("{}\n")
    assert transcript_path("/tmp/proj", "abc123", tmp_path) == d / "abc123.jsonl"


def test_transcript_path_falls_back_to_glob(tmp_path):
    """If the slug rule is wrong for an exotic path, find the file by id anyway."""
    d = tmp_path / "some-other-encoding"
    d.mkdir()
    (d / "abc123.jsonl").write_text("{}\n")
    assert transcript_path("/tmp/proj", "abc123", tmp_path) == d / "abc123.jsonl"


def test_transcript_path_returns_none_when_absent(tmp_path):
    assert transcript_path("/tmp/proj", "gone999", tmp_path) is None


def test_list_transcripts_returns_ids_and_mtimes(tmp_path):
    d = tmp_path / "-tmp-proj"
    d.mkdir()
    (d / "aaa.jsonl").write_text("{}\n")
    (d / "bbb.jsonl").write_text("{}\n")
    import os
    os.utime(d / "aaa.jsonl", (1000, 1000))
    os.utime(d / "bbb.jsonl", (2000, 2000))

    got = dict(list_transcripts("/tmp/proj", tmp_path))

    assert got == {"aaa": 1000.0, "bbb": 2000.0}


def test_list_transcripts_on_missing_dir_is_empty(tmp_path):
    assert list_transcripts("/nope", tmp_path) == []


def test_panekey_sort_key_orders_by_session_then_window_then_pane():
    a = PaneKey("setup", 2, 0, "/x")
    b = PaneKey("setup", 10, 0, "/x")
    assert a.sort_key < b.sort_key, "window must sort numerically, not as a string"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'claude_resume'`.

- [ ] **Step 3: Create the package marker**

Create `claude_resume/__init__.py`:

```python
# ABOUTME: Package for the trestore Claude-conversation resume table.
# ABOUTME: Stdlib only; no imports from other repo packages (see the plan's global constraints).
```

- [ ] **Step 4: Create the models**

Create `claude_resume/models.py`:

```python
# ABOUTME: Dataclasses shared across the claude_resume package.
# ABOUTME: PaneKey is the identity that joins a pane to itself across snapshots.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PaneKey:
    """Identity of a Claude pane across snapshots.

    cwd is part of the key because it is what maps the pane to a Claude project
    dir. A pane that changed directory is, for resume purposes, a different pane.
    """

    session: str
    window: int
    pane: int
    cwd: str

    @property
    def sort_key(self) -> tuple:
        return (self.session, self.window, self.pane)

    @property
    def label(self) -> str:
        return f"{self.session}  w{self.window}.{self.pane}"


@dataclass
class Candidate:
    """One conversation a pane could resume.

    lineage distinguishes a conversation this pane actually held from a
    "stranger" merely sitting in the same project dir. Project dirs collect
    conversations no pane ever ran: /security-review sessions and orphans from
    closed panes. Ranking by mtime alone let those hijack live panes, so
    strangers are a last resort only (see assign_most_recent).
    """

    session_id: str
    mtime: float
    exists: bool
    lineage: bool


@dataclass
class SnapshotRef:
    """A tsave snapshot on disk."""

    path: Path
    saved_at: str
    origin: str


@dataclass
class Column:
    """One resume policy: what every pane would get if you picked this column."""

    key: str
    label: str
    assignment: dict = field(default_factory=dict)
```

- [ ] **Step 5: Create the path resolver**

Create `claude_resume/claude_paths.py`:

```python
# ABOUTME: Maps a pane cwd to its Claude project dir and locates transcript files.
# ABOUTME: Slug rule mirrors tmux-restore.sh; a glob fallback covers any path it gets wrong.
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")


def project_slug(cwd: str) -> str:
    """Encode a working directory the way Claude Code names its project dirs.

    Every non-alphanumeric character becomes '-'. Handling only '/' misses
    underscore dirs (monorepo_quater), which once made their saved session IDs
    look absent and sent trestore to the picker.
    """
    return _NON_ALNUM.sub("-", cwd)


def transcript_path(cwd: str, session_id: str, projects_dir: Path) -> Path | None:
    """Locate one conversation's .jsonl, or None if it is gone.

    Tries the slugged dir first, then falls back to a glob across every project
    dir. The fallback means an imperfect slug rule degrades to a slower lookup
    instead of a wrong 'conversation missing' verdict.
    """
    direct = Path(projects_dir) / project_slug(cwd) / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    for found in Path(projects_dir).glob(f"*/{session_id}.jsonl"):
        return found
    return None


def list_transcripts(cwd: str, projects_dir: Path) -> list:
    """Every conversation in this cwd's project dir, as (session_id, mtime)."""
    d = Path(projects_dir) / project_slug(cwd)
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("*.jsonl"):
        try:
            out.append((f.stem, f.stat().st_mtime))
        except OSError:
            continue
    return sorted(out)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add claude_resume/ tests/test_claude_resume.py
git commit -m "feat(claude-resume): package skeleton, models, project path resolution"
```

---

### Task 4: Title resolution

**Files:**
- Create: `claude_resume/titles.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: `claude_paths.transcript_path`
- Produces: `TitleResolver(projects_dir: Path)` with method `title_for(cwd: str, session_id: str) -> str`, memoised per session id.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
import json

from claude_resume.titles import TitleResolver


def write_transcript(projects_dir: Path, cwd: str, session_id: str, records):
    from claude_resume.claude_paths import project_slug
    d = projects_dir / project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session_id}.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in records))
    return f


def test_title_prefers_ai_title(tmp_path):
    write_transcript(tmp_path, "/p", "s1", [
        {"type": "user", "message": {"content": "hello there"}},
        {"type": "ai-title", "aiTitle": "Investigate network losses"},
        {"type": "last-prompt", "lastPrompt": "and then what"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s1") == "Investigate network losses"


def test_title_uses_last_ai_title_when_several(tmp_path):
    """Claude rewrites the title as a session evolves; the newest one wins."""
    write_transcript(tmp_path, "/p", "s1", [
        {"type": "ai-title", "aiTitle": "First guess"},
        {"type": "ai-title", "aiTitle": "Better title"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s1") == "Better title"


def test_title_falls_back_to_last_prompt(tmp_path):
    write_transcript(tmp_path, "/p", "s2", [
        {"type": "user", "message": {"content": "first message"}},
        {"type": "last-prompt", "lastPrompt": "what about the deploy"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s2") == "what about the deploy"


def test_title_falls_back_to_first_user_message(tmp_path):
    write_transcript(tmp_path, "/p", "s3", [
        {"type": "assistant", "message": {"content": "hi"}},
        {"type": "user", "message": {"content": "please fix the flaky test"}},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s3") == "please fix the flaky test"


def test_title_untitled_when_nothing_usable(tmp_path):
    write_transcript(tmp_path, "/p", "s4", [{"type": "system", "content": "boot"}])
    assert TitleResolver(tmp_path).title_for("/p", "s4") == "(untitled)"


def test_title_untitled_when_file_missing(tmp_path):
    assert TitleResolver(tmp_path).title_for("/p", "nope") == "(untitled)"


def test_title_survives_corrupt_lines(tmp_path):
    """A truncated final line is normal for a live session being written to."""
    from claude_resume.claude_paths import project_slug
    d = tmp_path / project_slug("/p")
    d.mkdir(parents=True)
    (d / "s5.jsonl").write_text(
        json.dumps({"type": "ai-title", "aiTitle": "Good title"}) + "\n"
        + '{"type": "assist'
    )
    assert TitleResolver(tmp_path).title_for("/p", "s5") == "Good title"


def test_title_is_cached(tmp_path):
    f = write_transcript(tmp_path, "/p", "s6", [
        {"type": "ai-title", "aiTitle": "Original"},
    ])
    r = TitleResolver(tmp_path)
    assert r.title_for("/p", "s6") == "Original"
    f.write_text(json.dumps({"type": "ai-title", "aiTitle": "Changed"}) + "\n")
    assert r.title_for("/p", "s6") == "Original", "second call must not re-read"


def test_title_is_collapsed_to_one_line(tmp_path):
    write_transcript(tmp_path, "/p", "s7", [
        {"type": "last-prompt", "lastPrompt": "line one\nline two\n\nline three"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s7") == "line one line two line three"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -k title -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'claude_resume.titles'`.

- [ ] **Step 3: Implement the resolver**

Create `claude_resume/titles.py`:

```python
# ABOUTME: Resolves a human-readable title for a Claude conversation from its .jsonl.
# ABOUTME: Chain is ai-title -> last-prompt -> first user message -> "(untitled)", memoised.
from __future__ import annotations

import json
from pathlib import Path

from .claude_paths import transcript_path

UNTITLED = "(untitled)"

# Reading a whole transcript is not an option: they reach 84MB. Titles live in
# small records, and ai-title/last-prompt are rewritten as the session grows, so
# the newest one is at the end. Read a tail window and scan it backwards.
TAIL_BYTES = 256 * 1024


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _tail_lines(path: Path) -> list:
    with path.open("rb") as fh:
        try:
            fh.seek(-TAIL_BYTES, 2)
            chunk = fh.read()
            # A partial first line is likely after seeking into the middle.
            chunk = chunk.split(b"\n", 1)[1] if b"\n" in chunk else b""
        except OSError:
            fh.seek(0)
            chunk = fh.read()
    return chunk.decode("utf-8", "replace").splitlines()


def _scan(lines, wanted_type: str, field: str):
    """Newest matching record's field, scanning from the end."""
    for line in reversed(lines):
        line = line.strip()
        if not line or wanted_type not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") == wanted_type and rec.get(field):
            return str(rec[field])
    return ""


def _first_user_message(path: Path) -> str:
    """Read forwards from the top; only needed when both title records are absent."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or '"user"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "user":
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        return str(block["text"])
    return ""


class TitleResolver:
    """Titles for conversations, cached by session id for the life of one run."""

    def __init__(self, projects_dir: Path):
        self.projects_dir = Path(projects_dir)
        self._cache = {}

    def title_for(self, cwd: str, session_id: str) -> str:
        if session_id in self._cache:
            return self._cache[session_id]
        title = self._resolve(cwd, session_id)
        self._cache[session_id] = title
        return title

    def _resolve(self, cwd: str, session_id: str) -> str:
        path = transcript_path(cwd, session_id, self.projects_dir)
        if path is None:
            return UNTITLED
        try:
            lines = _tail_lines(path)
        except OSError:
            return UNTITLED
        for wanted_type, field in (("ai-title", "aiTitle"), ("last-prompt", "lastPrompt")):
            found = _scan(lines, wanted_type, field)
            if found:
                return _collapse(found)
        try:
            found = _first_user_message(path)
        except OSError:
            return UNTITLED
        return _collapse(found) if found else UNTITLED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -k title -v`
Expected: all PASS.

- [ ] **Step 5: Verify against real data**

Run:

```bash
uv run python -c "
from pathlib import Path
from claude_resume.titles import TitleResolver
r = TitleResolver(Path.home() / '.claude' / 'projects')
print(r.title_for('/home/ezalos/Setup', '415970f0-f928-41f9-843f-4c07bd092b65'))
"
```

Expected: `Trestore session recovery interface design`. This is the acceptance check that the tail-window approach finds real `ai-title` records in a live multi-hundred-KB transcript.

- [ ] **Step 6: Commit**

```bash
git add claude_resume/titles.py tests/test_claude_resume.py
git commit -m "feat(claude-resume): resolve conversation titles with fallback chain"
```

---

### Task 5: Snapshot reading

**Files:**
- Create: `claude_resume/snapshots.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: `models.PaneKey`, `models.SnapshotRef`
- Produces:
  - `read_panes(snapshot_dir: Path) -> dict[PaneKey, str]` mapping every Claude pane to its recorded conversation id (`""` when none was captured). Non-Claude panes are excluded.
  - `list_snapshots(save_dir: Path, history_dir: Path) -> list[SnapshotRef]` newest first, live save first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
from claude_resume.snapshots import list_snapshots, read_panes

CLAUDE_ROW = ("setup", 1, "claude", "layout-a", 0, "/home/e/Setup", 1, 1, "aaaa1111")
SHELL_ROW = ("setup", 2, "zsh", "layout-b", 0, "/home/e/Setup", 0, 0, "")


def test_read_panes_keeps_only_claude_panes(tmp_path):
    d = write_snapshot(str(tmp_path), "s", "2026-08-03 16:00:00", [CLAUDE_ROW, SHELL_ROW])

    panes = read_panes(d)

    assert list(panes) == [PaneKey("setup", 1, 0, "/home/e/Setup")]
    assert panes[PaneKey("setup", 1, 0, "/home/e/Setup")] == "aaaa1111"


def test_read_panes_handles_missing_session_id(tmp_path):
    row = ("setup", 1, "claude", "layout-a", 0, "/home/e/Setup", 1, 1, "")
    d = write_snapshot(str(tmp_path), "s", "2026-08-03 16:00:00", [row])

    assert read_panes(d)[PaneKey("setup", 1, 0, "/home/e/Setup")] == ""


def test_read_panes_on_missing_file_is_empty(tmp_path):
    assert read_panes(tmp_path / "nope") == {}


def test_read_panes_ignores_malformed_rows(tmp_path):
    d = tmp_path / "s"
    (d / "pane_contents").mkdir(parents=True)
    (d / "state.tsv").write_text("setup\tnot-a-number\tclaude\tl\t0\t/x\t1\t1\tid\n")
    (d / "saved_at").write_text("2026-08-03 16:00:00\n")

    assert read_panes(d) == {}


def test_list_snapshots_puts_live_save_first_then_history_newest_first(tmp_path):
    save_dir = tmp_path / "save"
    history = tmp_path / "history"
    history.mkdir()
    write_snapshot(str(tmp_path), "save", "2026-08-03 16:00:00", [CLAUDE_ROW], origin="manual")
    write_snapshot(str(history), "2026-08-01_12-00-00", "2026-08-01 12:00:00", [CLAUDE_ROW], origin="cron")
    write_snapshot(str(history), "2026-08-03_12-00-00", "2026-08-03 12:00:00", [CLAUDE_ROW], origin="cron")

    snaps = list_snapshots(save_dir, history)

    assert [s.path.name for s in snaps] == ["save", "2026-08-03_12-00-00", "2026-08-01_12-00-00"]
    assert snaps[0].origin == "manual"
    assert snaps[0].saved_at == "2026-08-03 16:00:00"


def test_list_snapshots_marks_unmarked_origin_unknown(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    write_snapshot(str(history), "2026-08-01_12-00-00", "2026-08-01 12:00:00", [CLAUDE_ROW])

    assert list_snapshots(tmp_path / "nosave", history)[0].origin == "unknown"


def test_list_snapshots_skips_incomplete_dirs(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "half-written").mkdir()
    write_snapshot(str(history), "2026-08-01_12-00-00", "2026-08-01 12:00:00", [CLAUDE_ROW])

    assert [s.path.name for s in list_snapshots(tmp_path / "nosave", history)] == ["2026-08-01_12-00-00"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -k "read_panes or list_snapshots" -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'claude_resume.snapshots'`.

- [ ] **Step 3: Implement**

Create `claude_resume/snapshots.py`:

```python
# ABOUTME: Reads tsave snapshots: which Claude conversation each pane held, and when.
# ABOUTME: state.tsv columns are session, win, win_name, layout, pane, cwd, is_claude, win_active, claude_id.
from __future__ import annotations

from pathlib import Path

from .models import PaneKey, SnapshotRef

IS_CLAUDE_COL = 6
EXPECTED_COLS = 9


def read_panes(snapshot_dir: Path) -> dict:
    """Every Claude pane in this snapshot, mapped to its recorded conversation id.

    Rows that do not parse are skipped rather than raising: a snapshot can be
    written while tmux is going down, and one bad row must not cost the restore.
    """
    state = Path(snapshot_dir) / "state.tsv"
    out = {}
    try:
        text = state.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < EXPECTED_COLS:
            continue
        if cols[IS_CLAUDE_COL] != "1":
            continue
        try:
            key = PaneKey(cols[0], int(cols[1]), int(cols[4]), cols[5])
        except ValueError:
            continue
        out[key] = cols[8].strip()
    return out


def _read_line(path: Path, default: str) -> str:
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return first[0].strip() if first else default
    except OSError:
        return default


def _as_ref(d: Path) -> SnapshotRef | None:
    if not (d / "state.tsv").is_file():
        return None
    return SnapshotRef(
        path=d,
        saved_at=_read_line(d / "saved_at", "?"),
        origin=_read_line(d / "origin", "unknown"),
    )


def list_snapshots(save_dir: Path, history_dir: Path) -> list:
    """Live save first, then history newest-first.

    History dir names are YYYY-MM-DD_HH-MM-SS, so a reverse lexical sort is
    chronological. Dirs without a state.tsv are half-written and skipped.
    """
    refs = []
    live = _as_ref(Path(save_dir))
    if live is not None:
        refs.append(live)
    history = Path(history_dir)
    if history.is_dir():
        for d in sorted(history.iterdir(), key=lambda p: p.name, reverse=True):
            if not d.is_dir():
                continue
            ref = _as_ref(d)
            if ref is not None:
                refs.append(ref)
    return refs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -k "read_panes or list_snapshots" -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add claude_resume/snapshots.py tests/test_claude_resume.py
git commit -m "feat(claude-resume): read panes and enumerate snapshots"
```

---

### Task 6: Candidates and the assignment invariant

This is the task that fixes the swap bug. Read the "The 'latest' option is usually wrong" section of the design doc before starting.

**Files:**
- Create: `claude_resume/candidates.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: `models.Candidate`, `models.PaneKey`, `claude_paths.list_transcripts`, `snapshots.read_panes`
- Produces:
  - `build_candidates(source_ids: dict, snapshots: list, projects_dir: Path) -> dict[PaneKey, list[Candidate]]` sorted newest-first per pane
  - `assign_most_recent(source_ids: dict, candidates: dict) -> dict[PaneKey, str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
from claude_resume.candidates import assign_most_recent, build_candidates

P0 = PaneKey("alfred", 0, 0, "/home/e/Alfred")
P1 = PaneKey("alfred", 1, 0, "/home/e/Alfred")


def make_transcripts(projects_dir: Path, cwd: str, ids_and_mtimes):
    import os
    from claude_resume.claude_paths import project_slug
    d = projects_dir / project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    for sid, mtime in ids_and_mtimes:
        f = d / f"{sid}.jsonl"
        f.write_text("{}\n")
        os.utime(f, (mtime, mtime))


def test_two_panes_in_one_cwd_do_not_swap(tmp_path):
    """The regression this whole feature exists to fix.

    Both panes live in /home/e/Alfred. cc is the newer file, and it belongs to
    P1. The old greedy walk gave cc to P0 (first in file order) and pushed P1
    onto c1, swapping the two conversations.
    """
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("cc", 2000)])
    source = {P0: "c1", P1: "cc"}
    cands = build_candidates(source, [], tmp_path)

    got = assign_most_recent(source, cands)

    assert got == {P0: "c1", P1: "cc"}


def test_live_pane_keeps_its_own_conversation_over_a_newer_stranger(tmp_path):
    """A newer conversation nobody ever held must not displace a live pane.

    Project dirs fill up with /security-review runs and orphans from closed
    panes. Prototyping the mtime-only rule against the live save displaced five
    real panes with 27-to-38-line tool sessions.
    """
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("brand-new", 5000)])
    source = {P0: "c1"}
    cands = build_candidates(source, [], tmp_path)

    assert assign_most_recent(source, cands) == {P0: "c1"}


def test_stranger_is_used_when_the_panes_lineage_is_gone(tmp_path):
    """Last resort: better than dropping the pane at a bare shell."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("stranger", 5000)])
    source = {P0: "deleted"}
    cands = build_candidates(source, [], tmp_path)

    assert assign_most_recent(source, cands) == {P0: "stranger"}


def test_own_history_beats_a_newer_stranger(tmp_path):
    """Tier 2 outranks tier 3: a conversation this pane held wins over one it never did."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("mine-old", 1000), ("stranger", 5000)])
    older = write_snapshot(
        str(tmp_path), "hist2", "2026-08-02 10:00:00",
        [("alfred", 0, "claude", "l", 0, "/home/e/Alfred", 1, 1, "mine-old")],
    )
    source = {P0: "deleted"}
    refs = [SnapshotRef(older, "2026-08-02 10:00:00", "cron")]
    cands = build_candidates(source, refs, tmp_path)

    assert assign_most_recent(source, cands) == {P0: "mine-old"}


def test_unclaimed_conversation_cannot_displace_another_panes_conversation(tmp_path):
    """P1's conversation is reserved for P1 even though it is newest overall."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("cc", 9000)])
    source = {P0: "c1", P1: "cc"}
    cands = build_candidates(source, [], tmp_path)

    got = assign_most_recent(source, cands)

    assert got[P0] == "c1"
    assert got[P1] == "cc"


def test_pane_whose_conversation_is_gone_falls_back(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("survivor", 1000)])
    source = {P0: "deleted-id"}
    cands = build_candidates(source, [], tmp_path)

    assert assign_most_recent(source, cands) == {P0: "survivor"}


def test_pane_with_no_candidates_at_all_gets_empty(tmp_path):
    source = {P0: "deleted-id"}
    cands = build_candidates(source, [], tmp_path)

    assert assign_most_recent(source, cands) == {P0: ""}


def test_candidates_include_ids_from_older_snapshots(tmp_path):
    """A conversation this pane held yesterday is a candidate even though it is
    no longer the newest file in the dir."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("old", 500), ("now", 3000)])
    older = write_snapshot(
        str(tmp_path), "hist", "2026-08-02 10:00:00",
        [("alfred", 0, "claude", "l", 0, "/home/e/Alfred", 1, 1, "old")],
    )
    source = {P0: "now"}

    cands = build_candidates(source, [SnapshotRef(older, "2026-08-02 10:00:00", "cron")], tmp_path)

    assert [c.session_id for c in cands[P0]] == ["now", "old"]


def test_candidates_are_sorted_newest_first(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("a", 100), ("b", 300), ("c", 200)])
    cands = build_candidates({P0: "a"}, [], tmp_path)

    assert [c.session_id for c in cands[P0]] == ["b", "c", "a"]


def test_missing_candidate_files_are_marked_not_existing(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("real", 100)])
    cands = build_candidates({P0: "ghost"}, [], tmp_path)

    by_id = {c.session_id: c for c in cands[P0]}
    assert by_id["ghost"].exists is False
    assert by_id["real"].exists is True


def test_candidates_flag_lineage_vs_stranger(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("mine", 100), ("stranger", 200)])
    cands = build_candidates({P0: "mine"}, [], tmp_path)

    by_id = {c.session_id: c for c in cands[P0]}
    assert by_id["mine"].lineage is True
    assert by_id["stranger"].lineage is False


def test_assignment_is_deterministic_regardless_of_dict_order(tmp_path):
    """Same inputs in a different insertion order must give the same answer."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("cc", 2000)])
    forward = {P0: "c1", P1: "cc"}
    backward = {P1: "cc", P0: "c1"}

    a = assign_most_recent(forward, build_candidates(forward, [], tmp_path))
    b = assign_most_recent(backward, build_candidates(backward, [], tmp_path))

    assert a == b
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -k "swap or candidate or assign or stranger or lineage or pane_w" -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'claude_resume.candidates'`.

- [ ] **Step 3: Implement**

Create `claude_resume/candidates.py`:

```python
# ABOUTME: Builds each pane's candidate conversations and assigns one per pane.
# ABOUTME: Reservation rule makes the historical pane-swap bug structurally impossible.
from __future__ import annotations

from pathlib import Path

from .claude_paths import list_transcripts, transcript_path
from .models import Candidate, PaneKey
from .snapshots import read_panes


def build_candidates(source_ids: dict, snapshots: list, projects_dir: Path) -> dict:
    """Candidate conversations per pane, newest first.

    Three sources, per the design doc: the id the pane holds in the snapshot
    being restored, ids the same pane held in older snapshots, and conversations
    in the pane's cwd that no pane holds.
    """
    projects_dir = Path(projects_dir)

    # Conversations any pane holds in the restored snapshot. Reserved: only their
    # own pane may take them.
    reserved = {sid: pane for pane, sid in source_ids.items() if sid}

    # Historical ids, keyed by pane. Read each snapshot once.
    historical = {}
    for ref in snapshots:
        for pane, sid in read_panes(ref.path).items():
            if sid:
                historical.setdefault(pane, []).append(sid)

    # mtimes per cwd, read once per directory rather than once per pane.
    by_cwd = {}
    for pane in source_ids:
        if pane.cwd not in by_cwd:
            by_cwd[pane.cwd] = dict(list_transcripts(pane.cwd, projects_dir))

    out = {}
    for pane, own in source_ids.items():
        mtimes = by_cwd.get(pane.cwd, {})
        ids = []
        if own:
            ids.append(own)
        ids.extend(historical.get(pane, []))
        for sid in mtimes:
            owner = reserved.get(sid)
            if owner is None or owner == pane:
                ids.append(sid)

        # Everything this pane actually held, as opposed to strangers that merely
        # live in the same project dir.
        lineage_ids = set()
        if own:
            lineage_ids.add(own)
        lineage_ids.update(historical.get(pane, []))

        seen, cands = set(), []
        for sid in ids:
            if sid in seen:
                continue
            seen.add(sid)
            is_lineage = sid in lineage_ids
            mtime = mtimes.get(sid)
            if mtime is None:
                found = transcript_path(pane.cwd, sid, projects_dir)
                if found is None:
                    cands.append(Candidate(sid, 0.0, False, is_lineage))
                    continue
                try:
                    mtime = found.stat().st_mtime
                except OSError:
                    cands.append(Candidate(sid, 0.0, False, is_lineage))
                    continue
            cands.append(Candidate(sid, mtime, True, is_lineage))

        cands.sort(key=lambda c: (-c.mtime, c.session_id))
        out[pane] = cands
    return out


def assign_most_recent(source_ids: dict, candidates: dict) -> dict:
    """One conversation per pane: the newest that pane may legitimately have.

    Reservation is the invariant: a conversation another pane held in the source
    snapshot is never offered here, at any tier. That is what stops two panes in
    one cwd from trading conversations, which is what the old mtime-ordered
    greedy walk did.

    Tiers exist because ranking on mtime alone is not safe. A project dir also
    holds conversations no pane ever ran: /security-review sessions and orphans
    from closed panes. Prototyped against the live save, mtime-only displaced
    five real panes with 27-to-38-line tool sessions.
    """
    reserved = {sid: pane for pane, sid in source_ids.items() if sid}
    out, taken = {}, set()

    def eligible(pane: PaneKey, cand: Candidate) -> bool:
        if not cand.exists or cand.session_id in taken:
            return False
        owner = reserved.get(cand.session_id)
        return owner is None or owner == pane

    panes = sorted(source_ids, key=lambda p: p.sort_key)

    # Tier 1, identity: a pane keeps the conversation it was actually running,
    # whenever that transcript still exists. This is the answer for every pane
    # on current data.
    for pane in panes:
        own = source_ids.get(pane)
        if not own or own in taken:
            continue
        c = next((c for c in candidates.get(pane, [])
                  if c.session_id == own and c.exists), None)
        if c is not None:
            out[pane] = own
            taken.add(own)

    # Tier 2, this pane's own past: the newest conversation the pane itself held.
    for pane in panes:
        if pane in out:
            continue
        c = next((c for c in candidates.get(pane, [])
                  if c.lineage and eligible(pane, c)), None)
        if c is not None:
            out[pane] = c.session_id
            taken.add(c.session_id)

    # Tier 3, last resort: a stranger from the cwd. Only reached once a pane's
    # entire lineage is gone from disk, so a tool-spawned session can never
    # displace a live pane.
    for pane in panes:
        if pane in out:
            continue
        c = next((c for c in candidates.get(pane, []) if eligible(pane, c)), None)
        out[pane] = c.session_id if c else ""
        if c is not None:
            taken.add(c.session_id)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -v`
Expected: all PASS.

- [ ] **Step 5: Verify against real data**

Run:

```bash
uv run python -c "
from pathlib import Path
from claude_resume.candidates import assign_most_recent, build_candidates
from claude_resume.snapshots import read_panes
save = Path.home() / '.tmux-save'
src = read_panes(save)
got = assign_most_recent(src, build_candidates(src, [], Path.home() / '.claude' / 'projects'))
swapped = [p.label for p in src if got[p] != src[p]]
print('panes:', len(src), 'changed from saved:', len(swapped))
for s in swapped: print('  ', s)
"
```

Expected: `changed from saved: 0`. Every saved conversation currently exists on disk, so tier 1 must claim all of them and the default must equal the saved assignment exactly. A non-zero count means a tier rule has regressed; this exact check caught the mtime-only version displacing five panes with `/security-review` sessions. Do not move on until it reads 0. The pane count itself is not asserted, since it tracks whatever is open at the time.

- [ ] **Step 6: Commit**

```bash
git add claude_resume/candidates.py tests/test_claude_resume.py
git commit -m "feat(claude-resume): candidate sets and non-swapping assignment"
```

---

### Task 7: Column construction

**Files:**
- Create: `claude_resume/columns.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: `models.Column`, `snapshots.read_panes`, `candidates.assign_most_recent`
- Produces: `build_columns(source_ids, candidates, snapshots, max_history=1) -> list[Column]`. Column `"1"` is always present and always first. Note it takes no `projects_dir`: every mtime it could need is already baked into `candidates`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
from claude_resume.columns import build_columns


def snap(tmp_path, name, saved_at, pane_ids, origin="cron"):
    rows = [(p.session, p.window, "claude", "l", p.pane, p.cwd, 1, 1, sid)
            for p, sid in pane_ids.items()]
    d = write_snapshot(str(tmp_path), name, saved_at, rows, origin=origin)
    return SnapshotRef(d, saved_at, origin)


def test_column_one_is_always_present_and_labelled_most_recent(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000)])
    source = {P0: "c1"}
    cols = build_columns(source, build_candidates(source, [], tmp_path), [])

    assert cols[0].key == "1"
    assert cols[0].label == "most recent"
    assert cols[0].assignment == {P0: "c1"}


def test_change_driven_column_is_the_newest_snapshot_that_differs(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now"}
    # Same as now -> must be skipped. Older one differs -> becomes column 2.
    same = snap(tmp_path, "h1", "2026-08-03 12:00:00", {P0: "now"})
    differs = snap(tmp_path, "h2", "2026-08-02 12:00:00", {P0: "old"})

    cols = build_columns(source, build_candidates(source, [same, differs], tmp_path),
                         [same, differs])

    assert [c.key for c in cols] == ["1", "2"]
    assert cols[1].assignment == {P0: "old"}
    assert "2026-08-02" in cols[1].label


def test_no_change_driven_column_when_history_all_agrees(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000)])
    source = {P0: "now"}
    same = snap(tmp_path, "h1", "2026-08-03 12:00:00", {P0: "now"})

    cols = build_columns(source, build_candidates(source, [same], tmp_path), [same])

    assert [c.key for c in cols] == ["1"], "an all-= column must not be rendered"


def test_manual_column_appears_when_a_manual_snapshot_differs(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("checkpoint", 1000)])
    source = {P0: "now"}
    manual = snap(tmp_path, "h1", "2026-08-02 18:45:00", {P0: "checkpoint"}, origin="manual")

    cols = build_columns(source, build_candidates(source, [manual], tmp_path), [manual])

    labels = [c.label for c in cols]
    assert any("manual" in l for l in labels), labels


def test_no_manual_column_when_no_marked_snapshot_exists(tmp_path):
    """All 101 existing snapshots predate the marker, so this is the common case."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now"}
    unknown = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "old"}, origin="unknown")

    cols = build_columns(source, build_candidates(source, [unknown], tmp_path), [unknown])

    assert not any("manual" in c.label for c in cols)


def test_duplicate_columns_are_not_added_twice(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now"}
    a = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "old"})
    b = snap(tmp_path, "h2", "2026-08-01 12:00:00", {P0: "old"})

    cols = build_columns(source, build_candidates(source, [a, b], tmp_path), [a, b],
                         max_history=5)

    assignments = [c.assignment for c in cols]
    assert len(assignments) == len(set(map(str, assignments))), assignments


def test_max_history_adds_more_columns(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("a", 2000), ("b", 1000)])
    source = {P0: "now"}
    s1 = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "a"})
    s2 = snap(tmp_path, "h2", "2026-08-01 12:00:00", {P0: "b"})

    one = build_columns(source, build_candidates(source, [s1, s2], tmp_path), [s1, s2])
    two = build_columns(source, build_candidates(source, [s1, s2], tmp_path), [s1, s2],
                        max_history=2)

    assert [c.key for c in one] == ["1", "2"]
    assert [c.key for c in two] == ["1", "2", "3"]


def test_pane_absent_from_a_snapshot_maps_to_empty(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now", P1: "old"}
    partial = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "old"})

    cols = build_columns(source, build_candidates(source, [partial], tmp_path),
                         [partial])

    assert cols[1].assignment[P1] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -k column -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'claude_resume.columns'`.

- [ ] **Step 3: Implement**

Create `claude_resume/columns.py`:

```python
# ABOUTME: Builds the resume table's columns: most recent, change-driven history, manual checkpoint.
# ABOUTME: A column whose cells would all equal column 1 is never produced.
from __future__ import annotations

from .candidates import assign_most_recent
from .models import Column
from .snapshots import read_panes


def _short_when(saved_at: str) -> str:
    """'2026-08-03 13:00:00' -> '08-03 13:00'. Falls back to the raw string."""
    try:
        date, time = saved_at.split(" ")
        return f"{date[5:]} {time[:5]}"
    except (ValueError, IndexError):
        return saved_at


def _assignment_from(ref, panes) -> dict:
    """What every pane would resume if this snapshot's record were used."""
    recorded = read_panes(ref.path)
    return {pane: recorded.get(pane, "") for pane in panes}


def build_columns(source_ids: dict, candidates: dict, snapshots: list,
                  max_history: int = 1) -> list:
    """Column 1 plus up to max_history change-driven columns plus a manual column.

    Change-driven means: walking snapshots newest to oldest, take the first whose
    assignment differs from every column already chosen. Columns at fixed time
    offsets were rejected because most panes hold one conversation for weeks, so
    those columns render as all-= and waste the width.
    """
    panes = list(source_ids)
    base = Column("1", "most recent", assign_most_recent(source_ids, candidates))
    columns = [base]
    seen = [base.assignment]

    def is_new(assignment: dict) -> bool:
        if not any(assignment.values()):
            return False
        return all(assignment != prior for prior in seen)

    for ref in snapshots:
        if len(columns) > max_history:
            break
        assignment = _assignment_from(ref, panes)
        if not is_new(assignment):
            continue
        columns.append(Column(str(len(columns) + 1), _short_when(ref.saved_at), assignment))
        seen.append(assignment)

    for ref in snapshots:
        if ref.origin != "manual":
            continue
        assignment = _assignment_from(ref, panes)
        if not is_new(assignment):
            break
        columns.append(
            Column(str(len(columns) + 1), f"manual {_short_when(ref.saved_at)}", assignment)
        )
        break

    return columns
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add claude_resume/columns.py tests/test_claude_resume.py
git commit -m "feat(claude-resume): most-recent, change-driven and manual columns"
```

---

### Task 8: Difference-focused rendering

**Files:**
- Create: `claude_resume/render.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: `models.Column`, `titles.TitleResolver`
- Produces: `render(columns: list, titles, width: int = 100, expand: bool = False) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
from claude_resume.render import render


class FakeTitles:
    def __init__(self, mapping):
        self.mapping = mapping

    def title_for(self, cwd, session_id):
        return self.mapping.get(session_id, "(untitled)")


def test_render_collapses_agreeing_panes(tmp_path):
    cols = [
        Column("1", "most recent", {P0: "a", P1: "b"}),
        Column("2", "08-02 12:00", {P0: "a", P1: "b"}),
    ]
    out = render(cols, FakeTitles({"a": "Alpha work", "b": "Beta work"}))

    assert "2 panes agree" in out
    assert "Alpha work" not in out, "agreeing panes must be collapsed, not listed"


def test_render_expands_differing_panes_with_titles(tmp_path):
    cols = [
        Column("1", "most recent", {P0: "a", P1: "b"}),
        Column("2", "08-02 12:00", {P0: "a", P1: "older"}),
    ]
    out = render(cols, FakeTitles({"a": "Alpha", "b": "Beta", "older": "Yesterday work"}))

    assert "1 pane agrees" in out
    assert "Beta" in out
    assert "Yesterday work" in out
    assert "Alpha" not in out


def test_render_marks_identical_cells_with_equals():
    cols = [
        Column("1", "most recent", {P0: "a", P1: "b"}),
        Column("2", "08-02 12:00", {P0: "a", P1: "older"}),
    ]
    out = render(cols, FakeTitles({}))
    differing_block = out.split("differ")[-1]
    assert "=" in differing_block


def test_render_marks_absent_pane_with_dash():
    cols = [
        Column("1", "most recent", {P0: "a"}),
        Column("2", "08-02 12:00", {P0: ""}),
    ]
    out = render(cols, FakeTitles({"a": "Alpha"}))
    assert "-" in out.split("differ")[-1]


def test_render_expand_lists_the_agreeing_panes():
    cols = [Column("1", "most recent", {P0: "a", P1: "b"})]
    out = render(cols, FakeTitles({"a": "Alpha", "b": "Beta"}), expand=True)

    assert "Alpha" in out
    assert "Beta" in out


def test_render_shows_column_headers_and_keys():
    cols = [
        Column("1", "most recent", {P0: "a"}),
        Column("2", "08-02 12:00", {P0: "z"}),
    ]
    out = render(cols, FakeTitles({}))
    assert "[1]" in out and "[2]" in out
    assert "most recent" in out and "08-02 12:00" in out


def test_render_truncates_long_titles_to_width():
    cols = [
        Column("1", "most recent", {P0: "a"}),
        Column("2", "08-02 12:00", {P0: "z"}),
    ]
    out = render(cols, FakeTitles({"a": "x" * 400, "z": "y" * 400}), width=100)
    assert all(len(line) <= 100 for line in out.splitlines()), \
        [l for l in out.splitlines() if len(l) > 100]


def test_render_single_column_says_nothing_to_decide():
    cols = [Column("1", "most recent", {P0: "a"})]
    out = render(cols, FakeTitles({"a": "Alpha"}))
    assert "1 pane agrees" in out


def test_render_leaves_no_trailing_whitespace():
    """Absent and same cells have no title; the line must not end in spaces."""
    cols = [
        Column("1", "most recent", {P0: "a"}),
        Column("2", "08-02 12:00", {P0: ""}),
    ]
    out = render(cols, FakeTitles({"a": "Alpha"}))
    assert not any(line != line.rstrip() for line in out.splitlines())


def test_render_handles_zero_panes():
    out = render([Column("1", "most recent", {})], FakeTitles({}))
    assert "No Claude panes" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -k render -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'claude_resume.render'`.

- [ ] **Step 3: Implement**

Create `claude_resume/render.py`:

```python
# ABOUTME: Renders the resume table: agreeing panes collapse to a count, differing panes expand.
# ABOUTME: Chosen over a full grid because ~22 of 26 panes agree on a typical restore.
from __future__ import annotations

SHORT_ID = 8
SAME = "="
ABSENT = "-"


def _short(session_id: str) -> str:
    if not session_id:
        return ABSENT
    return session_id[:SHORT_ID]


def _differing(columns: list) -> list:
    """Panes whose cells are not identical across every column, in stable order."""
    if len(columns) < 2:
        return []
    base = columns[0].assignment
    out = []
    for pane in sorted(base, key=lambda p: p.sort_key):
        if any(col.assignment.get(pane, "") != base[pane] for col in columns[1:]):
            out.append(pane)
    return out


def _clip(text: str, room: int) -> str:
    if room <= 1:
        return ""
    return text if len(text) <= room else text[: room - 1] + "…"


def render(columns: list, titles, width: int = 100, expand: bool = False) -> str:
    base = columns[0].assignment
    panes = sorted(base, key=lambda p: p.sort_key)
    if not panes:
        return "No Claude panes in this snapshot; nothing to resume."

    differing = _differing(columns)
    agreeing = [p for p in panes if p not in set(differing)]

    lines = []
    sessions = len({p.session for p in panes})
    lines.append(f"Claude resume - {len(panes)} panes across {sessions} sessions")
    lines.append("")

    header = "  " + "   ".join(
        f"[{c.key}] {c.label}" + (" (default)" if c.key == "1" else "") for c in columns
    )
    lines.append(_clip(header, width))
    lines.append("")

    if agreeing:
        word = "pane agrees" if len(agreeing) == 1 else "panes agree"
        suffix = "" if expand else "    [v] list them"
        lines.append(f"  {len(agreeing)} {word} across all columns -> resume most recent{suffix}")
        if expand:
            for pane in agreeing:
                sid = base[pane]
                title = titles.title_for(pane.cwd, sid) if sid else ""
                lines.append(_clip(f"    {pane.label}  {_short(sid)}  {title}", width))
        lines.append("")

    if differing:
        word = "pane differs" if len(differing) == 1 else "panes differ"
        lines.append(f"  {len(differing)} {word}:")
        lines.append("")
        for pane in differing:
            lines.append(_clip(f"  {pane.label}   {pane.cwd}", width))
            for col in columns:
                sid = col.assignment.get(pane, "")
                if col.key != "1" and sid == base.get(pane, ""):
                    lines.append(f"    [{col.key}] {SAME:>8}")
                    continue
                title = titles.title_for(pane.cwd, sid) if sid else ""
                lines.append(_clip(f"    [{col.key}] {_short(sid):>8}  {title}", width))
            lines.append("")

    keys = "/".join(c.key for c in columns)
    lines.append(f"  {keys} take column   d pane-by-pane   h more history   Enter default   q none")
    # rstrip because an absent or same cell has no title, which would otherwise
    # leave trailing spaces on the line.
    return "\n".join(line.rstrip() for line in lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add claude_resume/render.py tests/test_claude_resume.py
git commit -m "feat(claude-resume): difference-focused table rendering"
```

---

### Task 9: CLI, key loop and tmux execution

**Files:**
- Create: `claude_resume/__main__.py`
- Test: `tests/test_claude_resume.py`

**Interfaces:**
- Consumes: every module above
- Produces: `python3 -m claude_resume [--snapshot DIR] [--batch] [--no-launch] [--socket NAME] [--width N]`. Exits 0 on success, 1 when there is no snapshot.
- `resume_commands(assignment, projects_dir) -> list[tuple[PaneKey, str]]` is exported for testing: the exact shell command each pane should receive.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_resume.py`:

```python
from claude_resume.__main__ import resume_commands


def test_resume_command_uses_the_chosen_id(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000)])
    cmds = dict(resume_commands({P0: "c1"}, tmp_path))
    assert cmds[P0] == "claude --resume 'c1'"


def test_resume_command_degrades_to_picker_when_file_is_gone(tmp_path):
    cmds = dict(resume_commands({P0: "ghost"}, tmp_path))
    assert cmds[P0] == "claude --resume"


def test_resume_command_skips_panes_with_no_choice(tmp_path):
    assert resume_commands({P0: ""}, tmp_path) == []


def test_batch_mode_prints_plan_and_exits_zero(tmp_path, monkeypatch):
    """--batch with --dry-run must not need a tmux server."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000)])
    save = write_snapshot(
        str(tmp_path), "save", "2026-08-03 16:00:00",
        [("alfred", 0, "claude", "l", 0, "/home/e/Alfred", 1, 1, "c1")],
    )
    result = subprocess.run(
        ["python3", "-m", "claude_resume", "--snapshot", str(save),
         "--batch", "--dry-run"],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "CLAUDE_PROJECTS_DIR": str(tmp_path), "PYTHONPATH": str(REPO)},
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "claude --resume 'c1'" in result.stdout


def test_missing_snapshot_exits_one(tmp_path):
    result = subprocess.run(
        ["python3", "-m", "claude_resume", "--snapshot", str(tmp_path / "nope"), "--batch"],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO)},
    )
    assert result.returncode == 1
    assert "no" in (result.stdout + result.stderr).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_resume.py -k "resume_command or batch or missing_snapshot" -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'claude_resume.__main__'`.

- [ ] **Step 3: Implement**

Create `claude_resume/__main__.py`:

```python
# ABOUTME: CLI for the Claude resume table: renders it, reads one key, sends resume commands.
# ABOUTME: Invoked by trestore and standalone as `cresume`.
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .candidates import assign_most_recent, build_candidates
from .claude_paths import transcript_path
from .columns import build_columns
from .render import render
from .snapshots import list_snapshots, read_panes
from .titles import TitleResolver


def resume_commands(assignment: dict, projects_dir: Path) -> list:
    """The exact command each pane should be sent, skipping panes with no choice.

    A chosen conversation whose file has since vanished degrades to Claude's own
    picker rather than failing: `claude --resume <gone-id>` would just error in
    the pane and leave nothing useful on screen.
    """
    out = []
    for pane in sorted(assignment, key=lambda p: p.sort_key):
        sid = assignment[pane]
        if not sid:
            continue
        if transcript_path(pane.cwd, sid, projects_dir) is None:
            out.append((pane, "claude --resume"))
        else:
            out.append((pane, f"claude --resume '{sid}'"))
    return out


def send(pane, command: str, socket: str, launch: bool) -> None:
    target = f"{pane.session}:{pane.window}.{pane.pane}"
    argv = ["tmux"]
    if socket:
        argv += ["-L", socket]
    argv += ["send-keys", "-t", target, command]
    if launch:
        argv.append("Enter")
    subprocess.run(argv, capture_output=True, text=True)


def parse_args(argv):
    p = argparse.ArgumentParser(prog="claude_resume",
                                description="Pick which Claude conversation each restored pane resumes.")
    p.add_argument("--snapshot", default=os.environ.get("TMUX_SAVE_DIR",
                                                       str(Path.home() / ".tmux-save")))
    p.add_argument("--history", default=os.environ.get("TMUX_SAVE_HISTORY_DIR",
                                                       str(Path.home() / ".tmux-save-history")))
    p.add_argument("--projects", default=os.environ.get("CLAUDE_PROJECTS_DIR",
                                                        str(Path.home() / ".claude" / "projects")))
    p.add_argument("--socket", default=os.environ.get("TMUX_SOCKET", ""))
    p.add_argument("--width", type=int, default=100)
    p.add_argument("--batch", action="store_true",
                   help="take column 1 with no prompt")
    p.add_argument("--no-launch", action="store_true",
                   help="pre-type the commands without pressing Enter")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be sent and exit; no tmux calls")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    snapshot, projects = Path(args.snapshot), Path(args.projects)

    source_ids = read_panes(snapshot)
    if not source_ids:
        print(f"No Claude panes found in {snapshot}; nothing to resume.")
        return 1 if not (snapshot / "state.tsv").is_file() else 0

    history = [ref for ref in list_snapshots(snapshot, Path(args.history))
               if ref.path != snapshot]
    titles = TitleResolver(projects)
    max_history = 1

    def compute(mh):
        cands = build_candidates(source_ids, history, projects)
        return build_columns(source_ids, cands, history, max_history=mh)

    columns = compute(max_history)

    if args.batch:
        chosen = columns[0].assignment
    else:
        expand = False
        while True:
            print(render(columns, titles, width=args.width, expand=expand))
            try:
                answer = input("choice> ").strip().lower()
            except EOFError:
                answer = "q"
            if answer in ("q",):
                print("Nothing resumed.")
                return 0
            if answer == "v":
                expand = not expand
                continue
            if answer == "h":
                max_history += 1
                columns = compute(max_history)
                continue
            if answer == "d":
                chosen = detail_loop(columns, titles)
                break
            if answer == "":
                chosen = columns[0].assignment
                break
            match = next((c for c in columns if c.key == answer), None)
            if match is not None:
                chosen = match.assignment
                break
            print(f"  unknown choice: {answer!r}")

    commands = resume_commands(chosen, projects)
    if args.dry_run:
        for pane, cmd in commands:
            print(f"{pane.label}  {cmd}")
        return 0

    for pane, cmd in commands:
        send(pane, cmd, args.socket, launch=not args.no_launch)
    verb = "Pre-typed into" if args.no_launch else "Resumed"
    print(f"{verb} {len(commands)} Claude pane(s).")
    return 0


def detail_loop(columns: list, titles) -> dict:
    """Per-pane walk, the escape hatch from the bulk columns.

    This is the old trestore behaviour, kept deliberately: it is the right tool
    when the columns genuinely disagree and you want to decide case by case. The
    difference is that every option now carries a title.
    """
    base = columns[0].assignment
    chosen = {}
    for pane in sorted(base, key=lambda p: p.sort_key):
        options = []
        for col in columns:
            sid = col.assignment.get(pane, "")
            if sid and sid not in [o[1] for o in options]:
                options.append((col.key, sid))
        if not options:
            chosen[pane] = ""
            continue
        if len(options) == 1:
            chosen[pane] = options[0][1]
            continue
        print(f"\n  {pane.label}   {pane.cwd}")
        for key, sid in options:
            print(f"    [{key}] {sid[:8]}  {titles.title_for(pane.cwd, sid)}")
        print("    [n] none")
        try:
            answer = input("  choice> ").strip().lower()
        except EOFError:
            answer = ""
        picked = next((sid for key, sid in options if key == answer), None)
        if answer == "n":
            chosen[pane] = ""
        else:
            chosen[pane] = picked if picked else base.get(pane, "")
    return chosen


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_resume.py -v`
Expected: all PASS.

- [ ] **Step 5: Look at the real table**

Run:

```bash
CLAUDE_PROJECTS_DIR="$HOME/.claude/projects" \
  uv run python -m claude_resume --snapshot "$HOME/.tmux-save" --batch --dry-run | head -40
```

Expected: 26 `claude --resume '<id>'` lines. Then run without `--batch` and press `q` to see the table against real data, confirming it fits the terminal and reads clearly. Nothing is sent to tmux in either case.

- [ ] **Step 6: Commit**

```bash
git add claude_resume/__main__.py tests/test_claude_resume.py
git commit -m "feat(claude-resume): CLI, key loop and tmux send-keys execution"
```

---

### Task 10: Wire into trestore, the shutdown unit and zsh

**Files:**
- Modify: `scripts/tmux-restore.sh` (delete lines 201-378, add the call), `scripts/tmux-save-on-shutdown.service`, `dotfiles/.zshrc:573-575`
- Test: `tests/test_tmux_save_restore.py`

**Interfaces:**
- Consumes: `python3 -m claude_resume` from Task 9
- Produces: `trestore` delegating all Claude resumption; `cresume` alias

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tmux_save_restore.py`:

```python
def test_restore_batch_delegates_to_claude_resume(tmux_env, tmp_path):
    """trestore -b must run the package rather than its old inline loop."""
    wd = str(tmp_path)
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", wd).returncode == 0
    assert run_save(tmux_env).returncode == 0
    tmux(tmux_env, "kill-server")

    result = run_restore(tmux_env, "-c", "0", "-b")

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert tmux(tmux_env, "has-session", "-t", "alpha").returncode == 0


def test_restore_no_longer_contains_the_inline_prompt_loop():
    """Guard against the old 107-line loop being reintroduced alongside the new one."""
    text = RESTORE.read_text()
    assert "Resume claude --resume" not in text
    assert "[s]aved / [l]atest / [p]icker" not in text
    assert "claude_resume" in text


def test_shutdown_unit_marks_its_origin():
    unit = SCRIPTS / "tmux-save-on-shutdown.service"
    assert "--origin shutdown" in unit.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tmux_save_restore.py -k "delegates or inline_prompt or shutdown_unit" -v`
Expected: 2 FAILED (`inline_prompt`, `shutdown_unit`). `test_restore_batch_delegates` may pass already since `-b` currently works; it is a regression guard for after the edit.

- [ ] **Step 3: Replace the inline loop in tmux-restore.sh**

Delete everything from line 201 (`# --- Claude session verification helpers ---`) to the end of the file, and replace with:

```bash
# --- Claude conversation resumption ---
#
# Delegated to the claude_resume package. The logic it replaced picked each pane's
# conversation by mtime in state.tsv order without checking which pane had held
# it, so two panes sharing a directory swapped conversations (15 of 26 panes on a
# real save). See docs/plans/2026-08-03-claude-resume-table-design.md.
#
# Bare python3, not uv: this runs on the restore path, possibly right after a
# reboot, and the package is stdlib-only so it needs no venv. tmux-save.sh takes
# the same approach for the same reason.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
resume_args=(--snapshot "$SAVE_DIR")
[[ -n "$TMUX_SOCKET" ]] && resume_args+=(--socket "$TMUX_SOCKET")
(( BATCH )) && resume_args+=(--batch)
[[ -n "${TMUX_RESTORE_NO_LAUNCH:-}" ]] && resume_args+=(--no-launch)

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m claude_resume "${resume_args[@]}" || {
    echo "  (claude_resume failed; panes left at a shell. Re-run with: cresume)"
  }
```

Also delete the now-unused `claude_resume_list` accumulation if nothing else reads it. Keep the `active_windows` handling and the restore summary above line 201 untouched.

- [ ] **Step 4: Mark the shutdown unit's origin**

In `scripts/tmux-save-on-shutdown.service`, change the `ExecStop` line:

```
ExecStop=%h/Setup/scripts/tmux-save.sh --origin shutdown
```

Then apply it: `systemctl --user daemon-reload`.

- [ ] **Step 5: Add the cresume alias**

In `dotfiles/.zshrc`, after line 575:

```bash
cresume()  { PYTHONPATH="$PATH_SETUP_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m claude_resume "$@"; }
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest tests/test_tmux_save_restore.py tests/test_claude_resume.py -v`
Expected: all PASS, including the 6 original save/restore tests.

- [ ] **Step 7: End-to-end check on an isolated socket**

Run:

```bash
export TMUX_TMPDIR=$(mktemp -d)
tmux -L e2e new-session -d -s probe -c "$HOME/Setup"
"$HOME/Setup/scripts/tmux-save.sh" -L e2e --origin manual
"$HOME/Setup/scripts/tmux-snapshots.sh" --list | head -3
tmux -L e2e kill-server
rip "$TMUX_TMPDIR"
```

Expected: the save reports panes captured, and `tsnaps --list` shows `manual` in the origin column for the newest row. This exercises save, marker, and listing together without touching the default server.

- [ ] **Step 8: Commit**

```bash
git add scripts/tmux-restore.sh scripts/tmux-save-on-shutdown.service dotfiles/.zshrc tests/test_tmux_save_restore.py
git commit -m "feat(trestore): delegate Claude resumption to the resume table"
```

- [ ] **Step 9: Manual crontab edit (Louis)**

The crontab is not tracked in the repo (`scripts/crontabs/` is empty), so this is a machine-local step:

```
*/15 * * * * "$HOME/Setup/scripts/tmux-save.sh" --origin cron >/dev/null 2>&1
```

Optional: the tty fallback already records `cron` correctly for an unmarked entry, so nothing breaks if this is skipped.

---

## Self-Review

**Spec coverage.** Every design section maps to a task: placement (Task 9, 10), pane identity (Task 3), candidate set (Task 6), assignment invariant (Task 6), columns (Task 7), screen (Task 8), keys (Task 9), execution (Task 9), trestore integration (Task 10), origin marker (Task 1), tsnaps origin (Task 2), `CLAUDE_PROJECTS_DIR` override (Task 9). Every test case the design listed appears: the swap regression, gone-on-disk fallback, absent-pane dash, all-`=` column suppression, `h` never duplicating, all four title fallback levels, and origin detection for the three call paths.

**Placeholders.** None. Every code step carries the actual code; every test step carries the actual assertions; every run step names the exact command and expected result.

**Type consistency.** `PaneKey`/`Candidate`/`SnapshotRef`/`Column` are defined in Task 3 and used unchanged after. `build_candidates(source_ids, snapshots, projects_dir)` and `assign_most_recent(source_ids, candidates)` keep their Task 6 signatures in Tasks 7 and 9. `build_columns(source_ids, candidates, snapshots, max_history)` keeps its Task 7 signature in Task 9. `TitleResolver.title_for(cwd, session_id)` is defined in Task 4 and is the only method `render` calls on it, which is why the test fake only implements that one.

**Dead parameter removed.** An earlier draft gave `build_columns` a `projects_dir` argument it never read. Dropped from the signature, the implementation, all nine Task 7 call sites, and the Task 9 caller. Every mtime a column could need is already resolved into `candidates` by Task 6.

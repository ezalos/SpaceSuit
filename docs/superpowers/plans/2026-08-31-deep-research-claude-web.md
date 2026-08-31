# deep-research-claude-web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a skill plus CLI that launches a deep research run in a detached Claude Code background session and collects a citation-grade Markdown report from local disk.

**Architecture:** A `deep_research` Python package at the repo root, split into pure logic modules (charter, manifest, status, runner-prompt) and one thin impure module that spawns `claude --bg` and parses its session id. The CLI is an `argparse` `__main__.py`. A `SKILL.md` drives the human-facing workflow. Retrieval is by polling for a `DONE` sentinel the detached agent writes last, because `claude logs` returns an unusable raw ANSI screen dump.

**Tech Stack:** Python 3.10-3.13, `uv run`, pytest, argparse, stdlib only (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-08-31-deep-research-claude-web-design.md`

## Global Constraints

- Python entry points run via `uv run`, never bare `python`.
- Every code file opens with a 2-line `ABOUTME:` comment.
- **This repo is public.** No file may name a machine, place, person's address, or private host. Write "the local machine", never a hostname.
- No mock-only tests. The integration test must spawn a real `claude --bg`.
- Never `rm`; use `rip` if something must be removed.
- Never hand-edit `dotfiles/dotfiles.json`. Registration goes through the `add-dotfile` skill (Task 8).
- Defaults are `--model fable --effort max`.
- Concurrency cap is 2 simultaneous running jobs; `--force` overrides.
- Keep commit messages plain ASCII with no em-dashes, apostrophes, or smart quotes, so `git commit -m` is safe.
- Run all tests with: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`

## File Structure

| File | Responsibility |
|---|---|
| `deep_research/__init__.py` | Package marker, exports nothing |
| `deep_research/charter.py` | Charter dataclass, parse and render Markdown briefs |
| `deep_research/manifest.py` | Run id generation, `run.json` read/write, run discovery, active-run filtering |
| `deep_research/status.py` | Pure state machine mapping on-disk facts to a run state |
| `deep_research/runner.py` | Builds the prompt handed to the detached agent, carrying the output contract |
| `deep_research/launcher.py` | Spawns `claude --bg`, parses the session id, enforces the concurrency cap |
| `deep_research/__main__.py` | argparse CLI: launch, status, collect, stop, list |
| `dotfiles/bin/deep-research` | Thin shell wrapper so the CLI is on PATH |
| `skills/deep-research-claude-web/SKILL.md` | The skill: charter interview, launch, collect, observability |
| `tests/test_deep_research.py` | Unit tests for all pure modules |
| `tests/test_deep_research_integration.py` | One real-subprocess end-to-end test |

Split rationale: everything that can be a pure function is one, so the only code needing a real subprocess is `launcher.launch` and the CLI. That keeps the fast test suite fast and confines slowness to a single marked test.

---

### Task 1: Charter module

**Files:**
- Create: `deep_research/__init__.py`
- Create: `deep_research/charter.py`
- Test: `tests/test_deep_research.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Charter` frozen dataclass with fields `question: str`, `decision: str`, `must_answer: tuple[str, ...]`, `source_tier: str`, `recency: str`, `deliverable: str`, `out_of_scope: tuple[str, ...]`; `parse_charter(text: str) -> Charter`; `render_charter(c: Charter) -> str`; `CharterError(ValueError)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_deep_research.py`:

```python
# ABOUTME: Tests the deep_research package pure logic: charter, manifest, status, runner prompt.
# ABOUTME: No subprocesses and no network; the real claude --bg launch is covered separately.

import pytest

from deep_research.charter import Charter, CharterError, parse_charter, render_charter

CHARTER_TEXT = """# Which vector database fits a 10M chunk corpus

## Decision this feeds
Picking the store before the ingestion pipeline is written.

## Must answer
- What are the p95 query latencies at 10M vectors
- What does each cost per month at that scale

## Source bar
tier: vendor benchmarks only when reproduced by a third party
recency: 2024-01-01 onwards

## Deliverable
A comparison table plus a one paragraph recommendation.

## Out of scope
- Self hosted Kubernetes operational burden
"""


def test_parse_charter_extracts_every_field():
    c = parse_charter(CHARTER_TEXT)
    assert c.question == "Which vector database fits a 10M chunk corpus"
    assert c.decision == "Picking the store before the ingestion pipeline is written."
    assert c.must_answer == (
        "What are the p95 query latencies at 10M vectors",
        "What does each cost per month at that scale",
    )
    assert c.source_tier == "vendor benchmarks only when reproduced by a third party"
    assert c.recency == "2024-01-01 onwards"
    assert c.deliverable == "A comparison table plus a one paragraph recommendation."
    assert c.out_of_scope == ("Self hosted Kubernetes operational burden",)


def test_charter_round_trips():
    c = parse_charter(CHARTER_TEXT)
    assert parse_charter(render_charter(c)) == c


def test_parse_charter_rejects_missing_question():
    with pytest.raises(CharterError, match="question"):
        parse_charter("## Decision this feeds\nnothing\n")


def test_parse_charter_rejects_empty_must_answer():
    text = CHARTER_TEXT.replace(
        "- What are the p95 query latencies at 10M vectors\n"
        "- What does each cost per month at that scale\n",
        "",
    )
    with pytest.raises(CharterError, match="must answer"):
        parse_charter(text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research'`

- [ ] **Step 3: Write minimal implementation**

Create `deep_research/__init__.py`:

```python
# ABOUTME: Package marker for the deep-research detached run tooling.
# ABOUTME: Submodules are imported directly; this file intentionally exports nothing.
```

Create `deep_research/charter.py`:

```python
# ABOUTME: The research charter: a written brief parsed from and rendered to Markdown.
# ABOUTME: Pure logic, so a run is reproducible from a file rather than from memory.
from __future__ import annotations

import re
from dataclasses import dataclass


class CharterError(ValueError):
    """Raised when a charter is missing a field a detached run cannot proceed without."""


@dataclass(frozen=True)
class Charter:
    question: str
    decision: str
    must_answer: tuple[str, ...]
    source_tier: str
    recency: str
    deliverable: str
    out_of_scope: tuple[str, ...]


_FENCE_RE = re.compile(r"^(```|~~~)[^\n]*\n.*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)


def _mask_fences(text: str) -> str:
    # A "## " line inside a fenced code block is sample text, not a section boundary.
    # Blank fenced regions to spaces, preserving length and newlines so the match
    # offsets still index the original string.
    return _FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _section(text: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, _mask_fences(text), re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    # Slice the ORIGINAL text at the masked match's offsets so fenced content survives.
    return text[match.start(1) : match.end(1)].strip()


def _bullets(block: str) -> tuple[str, ...]:
    # Strip indentation FIRST: "  - item".lstrip("-") is a no-op, because the leading
    # space blocks the strip, which would leak the dash into the parsed value.
    items = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("-"):
            value = stripped[1:].strip()
            if value:
                items.append(value)
    return tuple(items)


def _labelled(block: str, label: str) -> str:
    for line in block.splitlines():
        if line.strip().lower().startswith(f"{label}:"):
            return line.split(":", 1)[1].strip()
    return ""


def parse_charter(text: str) -> Charter:
    title = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if not title:
        raise CharterError("charter is missing its question: no level-1 heading found")

    must_answer = _bullets(_section(text, "Must answer"))
    if not must_answer:
        raise CharterError("charter lists nothing under must answer")

    bar = _section(text, "Source bar")
    return Charter(
        question=title.group(1).strip(),
        decision=_section(text, "Decision this feeds"),
        must_answer=must_answer,
        source_tier=_labelled(bar, "tier"),
        recency=_labelled(bar, "recency"),
        deliverable=_section(text, "Deliverable"),
        out_of_scope=_bullets(_section(text, "Out of scope")),
    )


def render_charter(c: Charter) -> str:
    must = "\n".join(f"- {q}" for q in c.must_answer)
    scope = "\n".join(f"- {s}" for s in c.out_of_scope)
    return (
        f"# {c.question}\n\n"
        f"## Decision this feeds\n{c.decision}\n\n"
        f"## Must answer\n{must}\n\n"
        f"## Source bar\ntier: {c.source_tier}\nrecency: {c.recency}\n\n"
        f"## Deliverable\n{c.deliverable}\n\n"
        f"## Out of scope\n{scope}\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd ~/42/SpaceSuit
git add deep_research/__init__.py deep_research/charter.py tests/test_deep_research.py
git commit -m "deep-research: charter parsing and rendering"
```

---

### Task 2: Manifest module

**Files:**
- Create: `deep_research/manifest.py`
- Modify: `tests/test_deep_research.py` (append)

**Interfaces:**
- Consumes: nothing
- Produces: `Manifest` frozen dataclass with fields `run_id, bg_session_id, engine, model, effort, charter, out_dir, started_at, status` (all `str`); `slugify(text: str) -> str`; `make_run_id(question: str, now: datetime) -> str`; `write_manifest(out_dir: Path, m: Manifest) -> Path`; `read_manifest(out_dir: Path) -> Manifest`; `find_runs(root: Path) -> list[Manifest]`; `active_runs(runs: Iterable[Manifest]) -> list[Manifest]`; constant `MANIFEST_NAME = "run.json"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deep_research.py`:

```python
from datetime import datetime
from pathlib import Path

from deep_research.manifest import (
    MANIFEST_NAME,
    Manifest,
    active_runs,
    find_runs,
    make_run_id,
    read_manifest,
    slugify,
    write_manifest,
)


def _manifest(**over) -> Manifest:
    base = dict(
        run_id="2026-08-31-143022-vector-db",
        bg_session_id="8c969912",
        engine="local",
        model="fable",
        effort="max",
        charter="/runs/x/charter.md",
        out_dir="/runs/x",
        started_at="2026-08-31T14:30:22+02:00",
        status="running",
    )
    base.update(over)
    return Manifest(**base)


def test_slugify_is_filesystem_safe_and_bounded():
    assert slugify("Which vector DB fits 10M chunks?!") == "which-vector-db-fits-10m-chunks"
    assert len(slugify("word " * 60)) <= 40
    assert not slugify("word " * 60).endswith("-")


def test_make_run_id_is_sortable_and_carries_the_slug():
    rid = make_run_id("Which vector DB", datetime(2026, 8, 31, 14, 30, 22))
    assert rid == "2026-08-31-143022-which-vector-db"


def test_manifest_round_trips_through_disk(tmp_path):
    m = _manifest()
    written = write_manifest(tmp_path, m)
    assert written.name == MANIFEST_NAME
    assert read_manifest(tmp_path) == m


def test_find_runs_discovers_nested_manifests_and_skips_junk(tmp_path):
    a = tmp_path / "run-a"
    b = tmp_path / "run-b"
    a.mkdir()
    b.mkdir()
    write_manifest(a, _manifest(run_id="a", out_dir=str(a)))
    write_manifest(b, _manifest(run_id="b", out_dir=str(b), status="done"))
    (tmp_path / "not-a-run").mkdir()
    (tmp_path / "not-a-run" / "run.json").write_text("{ broken", encoding="utf-8")

    found = {m.run_id for m in find_runs(tmp_path)}
    assert found == {"a", "b"}


def test_active_runs_counts_only_running():
    runs = [_manifest(run_id="a"), _manifest(run_id="b", status="done")]
    assert [m.run_id for m in active_runs(runs)] == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.manifest'`

- [ ] **Step 3: Write minimal implementation**

Create `deep_research/manifest.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
cd ~/42/SpaceSuit
git add deep_research/manifest.py tests/test_deep_research.py
git commit -m "deep-research: run manifest with atomic writes and run discovery"
```

---

### Task 3: Status state machine

**Files:**
- Create: `deep_research/status.py`
- Modify: `tests/test_deep_research.py` (append)

**Interfaces:**
- Consumes: `Manifest` from `deep_research.manifest`
- Produces: `RunState` str-Enum with members `RUNNING="running"`, `DONE="done"`, `INCOMPLETE="incomplete"`, `LOST="lost"`; `resolve_state(out_dir: Path, session_alive: bool) -> RunState`; constants `DONE_SENTINEL = "DONE"`, `REPORT_NAME = "report.md"`, `SOURCES_NAME = "sources.md"`, `RESULT_NAME = "run-result.json"`

`session_alive` is passed in rather than probed here, so the state machine stays pure and testable without a subprocess.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deep_research.py`:

```python
from deep_research.status import (
    DONE_SENTINEL,
    REPORT_NAME,
    RunState,
    resolve_state,
)


def test_done_sentinel_wins_even_if_the_session_is_gone(tmp_path):
    (tmp_path / DONE_SENTINEL).write_text("", encoding="utf-8")
    (tmp_path / REPORT_NAME).write_text("# report", encoding="utf-8")
    assert resolve_state(tmp_path, session_alive=False) is RunState.DONE


def test_no_sentinel_but_session_alive_is_running(tmp_path):
    assert resolve_state(tmp_path, session_alive=True) is RunState.RUNNING


def test_dead_session_with_a_partial_report_is_incomplete(tmp_path):
    (tmp_path / REPORT_NAME).write_text("# half a report", encoding="utf-8")
    assert resolve_state(tmp_path, session_alive=False) is RunState.INCOMPLETE


def test_dead_session_with_nothing_written_is_lost(tmp_path):
    assert resolve_state(tmp_path, session_alive=False) is RunState.LOST
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.status'`

- [ ] **Step 3: Write minimal implementation**

Create `deep_research/status.py`:

```python
# ABOUTME: Maps on-disk facts plus session liveness onto a run state.
# ABOUTME: Pure so every branch is testable without spawning a background session.
from __future__ import annotations

from enum import Enum
from pathlib import Path

DONE_SENTINEL = "DONE"
REPORT_NAME = "report.md"
SOURCES_NAME = "sources.md"
RESULT_NAME = "run-result.json"


class RunState(str, Enum):
    RUNNING = "running"
    DONE = "done"
    INCOMPLETE = "incomplete"
    LOST = "lost"


def resolve_state(out_dir: Path, session_alive: bool) -> RunState:
    # The sentinel is written last, so its presence is the only completion signal
    # that does not race a partially written report.
    if (out_dir / DONE_SENTINEL).exists():
        return RunState.DONE
    if session_alive:
        return RunState.RUNNING
    if (out_dir / REPORT_NAME).exists():
        return RunState.INCOMPLETE
    return RunState.LOST
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
cd ~/42/SpaceSuit
git add deep_research/status.py tests/test_deep_research.py
git commit -m "deep-research: run state machine driven by the DONE sentinel"
```

---

### Task 4: Runner prompt builder

**Files:**
- Create: `deep_research/runner.py`
- Modify: `tests/test_deep_research.py` (append)

**Interfaces:**
- Consumes: `Charter` from `deep_research.charter`; `DONE_SENTINEL, REPORT_NAME, SOURCES_NAME, RESULT_NAME` from `deep_research.status`
- Produces: `build_runner_prompt(charter: Charter, out_dir: Path, notify_script: str | None = None) -> str`

This is the contract handed to the detached agent. It is the highest-value file in the package: everything about report quality lives here.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deep_research.py`:

```python
from deep_research.runner import build_runner_prompt


def test_runner_prompt_carries_the_question_and_every_sub_question():
    c = parse_charter(CHARTER_TEXT)
    prompt = build_runner_prompt(c, Path("/runs/x"))
    assert c.question in prompt
    for q in c.must_answer:
        assert q in prompt


def test_runner_prompt_states_the_output_contract_with_absolute_paths():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    assert "/runs/x/report.md" in prompt
    assert "/runs/x/sources.md" in prompt
    assert "/runs/x/run-result.json" in prompt
    assert "/runs/x/DONE" in prompt


def test_runner_prompt_demands_fetched_and_quoted_citations():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x")).lower()
    assert "verbatim" in prompt
    assert "bare domain" in prompt
    assert "webfetch" in prompt


def test_runner_prompt_orders_the_sentinel_last():
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    assert prompt.index("report.md") < prompt.index("/runs/x/DONE")
    assert "last" in prompt.lower()


def test_runner_prompt_includes_notify_only_when_a_script_is_given():
    c = parse_charter(CHARTER_TEXT)
    assert "notify.sh" not in build_runner_prompt(c, Path("/runs/x"))
    with_notify = build_runner_prompt(c, Path("/runs/x"), notify_script="/n/notify.sh")
    assert "/n/notify.sh" in with_notify


def test_runner_prompt_makes_a_relative_out_dir_absolute():
    # The prompt promises absolute paths, and the detached agent's cwd need not match
    # the caller's, so a relative path would strand the files where nobody polls.
    # Pull the path the prompt actually emits and assert it is absolute; a plain
    # substring check cannot work, since the resolved path still ENDS with the
    # relative one.
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("relative/out"))
    emitted = re.search(r"^1\. (\S*report\.md)\s*$", prompt, re.MULTILINE)
    assert emitted, f"no report.md line in prompt:\n{prompt}"
    assert Path(emitted.group(1)).is_absolute()
    assert emitted.group(1) == str((Path.cwd() / "relative" / "out" / "report.md"))


def test_runner_prompt_forbids_citing_an_unverified_source():
    # Listing a source as unverified must not be a licence to cite it anyway: without
    # this rule an agent can satisfy every other line and still ship an uncited claim.
    prompt = build_runner_prompt(parse_charter(CHARTER_TEXT), Path("/runs/x"))
    assert "unverified source may NEVER back an [n] marker" in prompt
    assert "unanswered" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.runner'`

- [ ] **Step 3: Write minimal implementation**

Create `deep_research/runner.py`:

```python
# ABOUTME: Builds the prompt handed to the detached research agent.
# ABOUTME: Carries the output contract and the citation rules; report quality lives here.
from __future__ import annotations

from pathlib import Path

from .charter import Charter
from .status import DONE_SENTINEL, REPORT_NAME, RESULT_NAME, SOURCES_NAME

PREAMBLE = """You are running an unattended deep research task. Nobody is watching, so
finish the job and write the files: an unwritten answer is a failed run.

Research method:
- Use WebSearch to find candidate sources and WebFetch to read them.
- Read a page before you cite it. A source you have not fetched is not a source.
- Prefer primary sources over coverage of primary sources.
- When sources conflict, say so explicitly and prefer the most recent from the most
  reputable publisher, rather than silently picking one.
- If a must-answer question cannot be answered from available evidence, say that
  plainly in the report. A documented gap is worth more than a confident guess.

Citation rules, which are absolute:
- Every data claim carries an inline [n] marker keyed to sources.md.
- Every source is an exact, live, clickable URL to the specific page carrying the
  claim. Never a bare domain, never a section index, never a redirect.
- Every source entry quotes the page verbatim, proving it says what you cite it for.
- If a source cannot be fetched and quoted, list it under unverified in
  run-result.json and say so in the report. Never silently downgrade it.
- An unverified source may NEVER back an [n] marker. Listing a source as unverified is
  not permission to cite it anyway: drop the claim it would have supported, and send the
  must-answer question it belonged to to unanswered instead. Every [n] marker in the
  report must point to a source you fetched and quoted.
"""


def build_runner_prompt(
    charter: Charter,
    out_dir: Path,
    notify_script: str | None = None,
) -> str:
    # resolve(), not just Path(): the prompt promises absolute paths, and the detached
    # agent's working directory need not match the caller's, so a relative path would
    # land the output files somewhere the poller never looks.
    out = Path(out_dir).resolve()
    must = "\n".join(f"- {q}" for q in charter.must_answer)
    scope = "\n".join(f"- {s}" for s in charter.out_of_scope) or "- nothing excluded"

    notify = ""
    if notify_script:
        notify = (
            f"\nWhen the sentinel is written, announce completion by running:\n"
            f'  {notify_script} done "<one line summary of what you found>"\n'
        )

    return f"""{PREAMBLE}
Research question:
{charter.question}

This decision depends on the answer:
{charter.decision}

You must answer every one of these:
{must}

Source bar:
- tier: {charter.source_tier}
- recency: {charter.recency}

Deliverable shape:
{charter.deliverable}

Out of scope, do not spend effort here:
{scope}

Write exactly these files, using absolute paths:
1. {out / REPORT_NAME}
   The report, with inline [n] markers on every data claim.
2. {out / SOURCES_NAME}
   A numbered list matching those markers. Each entry: the exact URL as a clickable
   Markdown link, the publishing authority, the page title, the date you accessed it,
   and a verbatim quote from the page supporting the claim.
3. {out / RESULT_NAME}
   JSON with keys: status ("complete" or "partial"), sources_total, sources_verified,
   unanswered (list of must-answer questions you could not answer), and unverified
   (list of objects with url and reason).
4. {out / DONE_SENTINEL}
   An empty sentinel file. Write it LAST, after all three files above are complete.
   It is the only signal the caller polls, so writing it early reports a half-finished
   run as a finished one.
{notify}"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
cd ~/42/SpaceSuit
git add deep_research/runner.py tests/test_deep_research.py
git commit -m "deep-research: runner prompt carrying the output and citation contract"
```

---

### Task 5: Launcher

**Files:**
- Create: `deep_research/launcher.py`
- Modify: `tests/test_deep_research.py` (append)

**Interfaces:**
- Consumes: `Charter`; `Manifest, make_run_id, write_manifest, find_runs, active_runs`; `build_runner_prompt`
- Produces: `LaunchError(RuntimeError)`; `CONCURRENCY_CAP = 2`; `parse_session_id(stdout: str) -> str`; `session_alive(session_id: str, runner=subprocess.run) -> bool`; `launch(charter: Charter, out_dir: Path, runs_root: Path, model: str = "fable", effort: str = "max", now: datetime | None = None, notify_script: str | None = None, force: bool = False, runner=subprocess.run) -> Manifest`

`runner` is injected so the cap and parsing logic are tested without spawning anything. The real subprocess path is covered by the integration test in Task 7. This is dependency injection of the boundary, not a mock of the thing under test.

`claude --bg` prints, verbatim:

```
Starting background service...
backgrounded - 8c969912
  claude agents             list sessions
```

where the separator before the id is a middle dot. The parser tolerates either.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deep_research.py`:

```python
import json as _json_mod
from types import SimpleNamespace

from deep_research.launcher import (
    CONCURRENCY_CAP,
    LaunchError,
    launch,
    parse_session_id,
    session_alive,
)

BG_STDOUT = (
    "Starting background service\n"
    "backgrounded · 8c969912\n"
    "  claude agents             list sessions\n"
    "  claude attach 8c969912    open in this terminal\n"
)


def test_parse_session_id_reads_the_backgrounded_line():
    assert parse_session_id(BG_STDOUT) == "8c969912"


def test_parse_session_id_rejects_output_without_an_id():
    with pytest.raises(LaunchError, match="session id"):
        parse_session_id("error: could not start background service\n")


def test_launch_writes_a_manifest_and_returns_it(tmp_path):
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    out = tmp_path / "run"
    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=out,
        runs_root=tmp_path,
        now=datetime(2026, 8, 31, 14, 30, 22),
        runner=fake_runner,
    )

    assert m.bg_session_id == "8c969912"
    assert m.model == "fable" and m.effort == "max"
    assert m.status == "running"
    assert read_manifest(out) == m
    assert (out / "charter.md").exists()

    cmd = calls[0]
    assert cmd[:2] == ["claude", "--bg"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "fable"
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "max"


def test_launch_refuses_past_the_concurrency_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    def fake_runner(cmd, **kwargs):
        raise AssertionError("must not spawn past the cap")

    with pytest.raises(LaunchError, match="already running"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=tmp_path / "new",
            runs_root=tmp_path,
            runner=fake_runner,
        )


def test_force_overrides_the_cap(tmp_path):
    for i in range(CONCURRENCY_CAP):
        d = tmp_path / f"busy{i}"
        write_manifest(d, _manifest(run_id=f"busy{i}", out_dir=str(d)))

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=BG_STDOUT, stderr="")

    m = launch(
        parse_charter(CHARTER_TEXT),
        out_dir=tmp_path / "new",
        runs_root=tmp_path,
        force=True,
        runner=fake_runner,
    )
    assert m.status == "running"


def test_launch_raises_when_claude_exits_nonzero(tmp_path):
    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(LaunchError, match="boom"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=tmp_path / "run",
            runs_root=tmp_path,
            runner=fake_runner,
        )


def test_launch_refuses_to_overwrite_an_existing_run(tmp_path):
    out = tmp_path / "run"
    write_manifest(out, _manifest(out_dir=str(out)))

    def fake_runner(cmd, **kwargs):
        raise AssertionError("must not spawn over an existing run")

    with pytest.raises(LaunchError, match="already holds"):
        launch(
            parse_charter(CHARTER_TEXT),
            out_dir=out,
            runs_root=tmp_path / "elsewhere",
            runner=fake_runner,
        )


AGENTS_JSON = _json_mod.dumps(
    [
        {
            "pid": 1,
            "id": "4c00ef07",
            "kind": "background",
            "sessionId": "4c00ef07-0c5c-4c7c-bad5-478c1fb7f234",
            "name": "a finished run",
            "status": "idle",
            "state": "done",
        },
        {
            "pid": 2,
            "id": "8c969912",
            "kind": "background",
            "sessionId": "8c969912-1111-2222-3333-444444444444",
            "name": "a live run",
            "status": "busy",
            "state": "running",
        },
    ]
)


def _agents_runner(payload: str, returncode: int = 0):
    def run(cmd, **kwargs):
        assert cmd == ["claude", "agents", "--json"]
        return SimpleNamespace(returncode=returncode, stdout=payload, stderr="")

    return run


def test_session_alive_is_true_for_a_running_background_session():
    assert session_alive("8c969912", runner=_agents_runner(AGENTS_JSON)) is True


def test_session_alive_is_false_once_the_state_is_done():
    assert session_alive("4c00ef07", runner=_agents_runner(AGENTS_JSON)) is False


def test_session_alive_is_false_when_the_id_is_absent():
    assert session_alive("deadbeef", runner=_agents_runner(AGENTS_JSON)) is False


def test_session_alive_matches_on_the_session_id_prefix():
    payload = _json_mod.dumps(
        [{"sessionId": "abc12345-0000-0000-0000-000000000000", "state": "running"}]
    )
    assert session_alive("abc12345", runner=_agents_runner(payload)) is True


def test_session_alive_is_false_rather_than_raising_when_claude_is_missing():
    def missing(cmd, **kwargs):
        raise FileNotFoundError("claude")

    assert session_alive("8c969912", runner=missing) is False


def test_session_alive_is_false_on_unparseable_output():
    assert session_alive("8c969912", runner=_agents_runner("not json")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.launcher'`

- [ ] **Step 3: Write minimal implementation**

Create `deep_research/launcher.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: 31 passed

- [ ] **Step 5: Commit**

```bash
cd ~/42/SpaceSuit
git add deep_research/launcher.py tests/test_deep_research.py
git commit -m "deep-research: launcher with concurrency cap and session id parsing"
```

---

### Task 6: CLI

**Files:**
- Create: `deep_research/__main__.py`
- Create: `dotfiles/bin/deep-research`
- Modify: `tests/test_deep_research.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-5
- Produces: `main(argv: list[str] | None = None) -> int`; `cmd_launch`, `cmd_status`, `cmd_collect`, `cmd_stop`, `cmd_list` each taking `argparse.Namespace` and returning `int`; `DEFAULT_RUNS_ROOT = Path.home() / "research-runs"`

`collect` must print unverified sources loudly, per the spec's failure-mode table: a run with unverified sources is never reported as clean.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deep_research.py`:

```python
import json as _json

from deep_research.__main__ import main


def test_status_reports_done_for_a_finished_run(tmp_path, capsys):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")

    rc = main(["status", "--runs-root", str(tmp_path)])
    assert rc == 0
    assert "done" in capsys.readouterr().out


def test_collect_shouts_about_unverified_sources(tmp_path, capsys):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")
    (out / "report.md").write_text("# findings", encoding="utf-8")
    (out / "run-result.json").write_text(
        _json.dumps(
            {
                "status": "partial",
                "sources_total": 3,
                "sources_verified": 2,
                "unanswered": ["what does it cost"],
                "unverified": [{"url": "https://x.test/a", "reason": "404"}],
            }
        ),
        encoding="utf-8",
    )

    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "https://x.test/a" in printed
    assert "UNVERIFIED" in printed.upper()
    assert "what does it cost" in printed


def test_collect_is_clean_for_a_fully_verified_run(tmp_path, capsys):
    out = tmp_path / "run"
    write_manifest(out, _manifest(run_id="r1", out_dir=str(out)))
    (out / "DONE").write_text("", encoding="utf-8")
    (out / "report.md").write_text("# findings", encoding="utf-8")
    (out / "run-result.json").write_text(
        _json.dumps(
            {
                "status": "complete",
                "sources_total": 3,
                "sources_verified": 3,
                "unanswered": [],
                "unverified": [],
            }
        ),
        encoding="utf-8",
    )

    rc = main(["collect", "r1", "--runs-root", str(tmp_path)])
    assert rc == 0
    assert "report.md" in capsys.readouterr().out


def test_collect_errors_on_an_unknown_run(tmp_path, capsys):
    rc = main(["collect", "nope", "--runs-root", str(tmp_path)])
    assert rc == 2
    assert "nope" in capsys.readouterr().err


def test_list_prints_every_run(tmp_path, capsys):
    for rid in ("r1", "r2"):
        d = tmp_path / rid
        write_manifest(d, _manifest(run_id=rid, out_dir=str(d)))
    assert main(["list", "--runs-root", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "r1" in printed and "r2" in printed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deep_research.__main__'`

- [ ] **Step 3: Write minimal implementation**

Create `deep_research/__main__.py`:

```python
# ABOUTME: CLI for detached deep research runs: launch, status, collect, stop, list.
# ABOUTME: Invoked as `deep-research`; all research logic lives in the sibling modules.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .charter import CharterError, parse_charter
from .launcher import LaunchError, launch, session_alive
from .manifest import Manifest, find_runs, read_manifest
from .status import DONE_SENTINEL, REPORT_NAME, RESULT_NAME, RunState, resolve_state

DEFAULT_RUNS_ROOT = Path.home() / "research-runs"
DEFAULT_NOTIFY = Path.home() / ".claude" / "skills" / "notify-louis" / "notify.sh"


def _find(runs_root: Path, run_id: str) -> Manifest | None:
    for m in find_runs(runs_root):
        if m.run_id == run_id:
            return m
    return None


def _state(m: Manifest) -> RunState:
    # Short-circuit on the sentinel before probing liveness: a finished run needs no
    # subprocess, which also keeps the unit tests from spawning a real claude.
    out = Path(m.out_dir)
    if (out / DONE_SENTINEL).exists():
        return RunState.DONE
    alive = m.status == "running" and session_alive(m.bg_session_id)
    return resolve_state(out, session_alive=alive)


def cmd_launch(args: argparse.Namespace) -> int:
    try:
        charter = parse_charter(Path(args.charter).read_text(encoding="utf-8"))
    except (OSError, CharterError) as exc:
        print(f"cannot read charter: {exc}", file=sys.stderr)
        return 2

    notify = str(DEFAULT_NOTIFY) if DEFAULT_NOTIFY.exists() else None
    try:
        m = launch(
            charter,
            out_dir=Path(args.out),
            runs_root=Path(args.runs_root),
            model=args.model,
            effort=args.effort,
            notify_script=notify,
            force=args.force,
        )
    except LaunchError as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return 1

    print(f"launched {m.run_id}")
    print(f"  session {m.bg_session_id} ({m.model}, effort {m.effort})")
    print(f"  output  {m.out_dir}")
    print(f"  watch   deep-research status {m.run_id}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    runs = find_runs(Path(args.runs_root))
    if args.run_id:
        runs = [m for m in runs if m.run_id == args.run_id]
        if not runs:
            print(f"no run named {args.run_id}", file=sys.stderr)
            return 2
    if not runs:
        print("no runs found")
        return 0
    for m in runs:
        print(f"{m.run_id}  {_state(m).value}  {m.out_dir}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    m = _find(Path(args.runs_root), args.run_id)
    if m is None:
        print(f"no run named {args.run_id}", file=sys.stderr)
        return 2

    out = Path(m.out_dir)
    state = _state(m)
    print(f"{m.run_id}  {state.value}")

    report = out / REPORT_NAME
    if report.exists():
        print(f"  report: {report}")
    else:
        print("  report: not written")

    result_path = out / RESULT_NAME
    if not result_path.exists():
        print("  no run-result.json; the run did not report on its own sources")
        return 0 if state is RunState.DONE else 1

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  run-result.json is unreadable: {exc}", file=sys.stderr)
        return 1

    total = result.get("sources_total", 0)
    verified = result.get("sources_verified", 0)
    print(f"  sources: {verified}/{total} verified")

    problems = False
    unanswered = result.get("unanswered") or []
    if unanswered:
        problems = True
        print("  UNANSWERED questions:")
        for q in unanswered:
            print(f"    - {q}")

    unverified = result.get("unverified") or []
    if unverified:
        problems = True
        print("  UNVERIFIED sources, do not cite these without checking them:")
        for entry in unverified:
            print(f"    - {entry.get('url')}  ({entry.get('reason')})")

    if state is not RunState.DONE:
        problems = True

    return 1 if problems else 0


def cmd_stop(args: argparse.Namespace) -> int:
    m = _find(Path(args.runs_root), args.run_id)
    if m is None:
        print(f"no run named {args.run_id}", file=sys.stderr)
        return 2
    result = subprocess.run(
        ["claude", "stop", m.bg_session_id],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"stop failed: {result.stderr or result.stdout}", file=sys.stderr)
        return 1
    print(f"stopped {m.run_id} (session {m.bg_session_id})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    runs = find_runs(Path(args.runs_root))
    if not runs:
        print("no runs found")
        return 0
    for m in runs:
        print(f"{m.run_id}  {m.status}  {m.model}/{m.effort}  {m.out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deep-research",
        description="Launch and collect detached deep research runs.",
    )
    parser.add_argument(
        "--runs-root",
        default=str(DEFAULT_RUNS_ROOT),
        help=f"where runs are discovered (default: {DEFAULT_RUNS_ROOT})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("launch", help="start a detached research run")
    p.add_argument("--charter", required=True, help="path to the charter Markdown file")
    p.add_argument("--out", required=True, help="output directory for this run")
    p.add_argument("--model", default="fable")
    p.add_argument("--effort", default="max")
    p.add_argument("--force", action="store_true", help="ignore the concurrency cap")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("status", help="show run state")
    p.add_argument("run_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("collect", help="summarise a finished run and flag problems")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("stop", help="stop a running run")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("list", help="list every known run")
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `dotfiles/bin/deep-research`:

```bash
#!/usr/bin/env bash
# ABOUTME: Thin wrapper putting the deep_research CLI on PATH.
# ABOUTME: Runs through uv from the repo so the package resolves without installation.
set -euo pipefail
REPO="${SPACESUIT_REPO:-$HOME/42/SpaceSuit}"
exec uv run --project "$REPO" python -m deep_research "$@"
```

Then make it executable:

```bash
chmod +x ~/42/SpaceSuit/dotfiles/bin/deep-research
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py -v`
Expected: 36 passed

- [ ] **Step 5: Verify the CLI runs end to end without a launch**

Run: `cd ~/42/SpaceSuit && uv run python -m deep_research list --runs-root /tmp/no-such-runs`
Expected: prints `no runs found`, exit 0

- [ ] **Step 6: Commit**

```bash
cd ~/42/SpaceSuit
git add deep_research/__main__.py dotfiles/bin/deep-research tests/test_deep_research.py
git commit -m "deep-research: CLI for launch, status, collect, stop and list"
```

---

### Task 7: Real end-to-end integration test

**Files:**
- Create: `tests/test_deep_research_integration.py`

**Interfaces:**
- Consumes: `launch` from `deep_research.launcher`; `Charter` from `deep_research.charter`
- Produces: nothing consumed by later tasks

This is the test that matters. Everything up to here proves the logic; this proves a real detached process really produces real files. It spawns an actual `claude --bg` with a deliberately trivial charter, so it costs a few seconds of quota rather than a real research run.

It is marked `slow` and skips cleanly when `claude` is absent, so the fast suite stays fast and CI without a Claude install is not red.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deep_research_integration.py`:

```python
# ABOUTME: End to end test spawning a real claude --bg session and awaiting its files.
# ABOUTME: Deliberately trivial charter so the run costs seconds, not a real research run.

import shutil
import time
from pathlib import Path

import pytest

from deep_research.charter import Charter
from deep_research.launcher import launch
from deep_research.status import DONE_SENTINEL, REPORT_NAME, RunState, resolve_state

pytestmark = pytest.mark.slow

TIMEOUT_SECONDS = 300
POLL_SECONDS = 3

TRIVIAL = Charter(
    question="What is two plus two",
    decision="Proving the detached run pipeline delivers files.",
    must_answer=("What is two plus two",),
    source_tier="no sources needed, answer from arithmetic",
    recency="not applicable",
    deliverable=(
        "One short sentence. Do not search the web. Do not cite anything. "
        "Write an empty sources.md and a run-result.json with zero sources."
    ),
    out_of_scope=("anything requiring research",),
)


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_a_real_background_run_writes_the_output_contract(tmp_path):
    out = tmp_path / "run"
    manifest = launch(TRIVIAL, out_dir=out, runs_root=tmp_path)
    assert manifest.bg_session_id

    sentinel = out / DONE_SENTINEL
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline and not sentinel.exists():
        time.sleep(POLL_SECONDS)

    assert sentinel.exists(), (
        f"no DONE sentinel after {TIMEOUT_SECONDS}s; "
        f"directory holds: {sorted(p.name for p in out.iterdir())}"
    )
    assert (out / REPORT_NAME).read_text(encoding="utf-8").strip()
    assert resolve_state(out, session_alive=False) is RunState.DONE
```

- [ ] **Step 2: Register the marker so pytest does not warn**

Modify `pyproject.toml`, in the existing `[tool.pytest.ini_options]` block, adding the `markers` key beneath `testpaths`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: spawns real subprocesses or waits on real work",
]
```

- [ ] **Step 3: Run the fast suite and confirm the slow test is skipped**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research.py tests/test_deep_research_integration.py -v -m "not slow"`
Expected: 36 passed, 1 deselected

- [ ] **Step 4: Run the real integration test**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/test_deep_research_integration.py -v -m slow`
Expected: PASS within 5 minutes. If it fails on a missing sentinel, read the listed directory contents in the assertion message and check `claude agents` for a stuck session before changing any code.

- [ ] **Step 5: Commit**

```bash
cd ~/42/SpaceSuit
git add tests/test_deep_research_integration.py pyproject.toml
git commit -m "deep-research: end to end test against a real background session"
```

---

### Task 8: The skill, and registration

**Files:**
- Create: `skills/deep-research-claude-web/SKILL.md`
- Modify: `dotfiles/dotfiles.json` **via the add-dotfile skill only**

**Interfaces:**
- Consumes: the `deep-research` CLI from Task 6
- Produces: the user-facing `/deep-research-claude-web` skill

- [ ] **Step 1: Write the skill**

Create `skills/deep-research-claude-web/SKILL.md`:

````markdown
---
name: deep-research-claude-web
description: Use when Louis wants a deep research run that should NOT consume this session - e.g. "research X properly", "go deep on Y", "launch a research run on Z", "find out everything about W and report back". Launches a detached Claude Code background session with a written charter and collects a cited Markdown report. Use the in-session research skills instead when the answer must inform the current conversation.
allowed-tools: Read, Write, Bash, AskUserQuestion
---

## Observability

This skill follows the universal observability baseline.

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (Claude was about to be wrong), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during the run.** Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change - already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | `deep-research launch` fails | `deep-research: launch failed: <stderr-tail>` |
| CRITICAL | `claude` binary missing | `deep-research: claude CLI not on PATH; cannot launch` |
| WARNING | concurrency cap hit | `deep-research: cap reached (<ids>); offered force or wait` |
| WARNING | run collected as lost or incomplete | `deep-research: run <id> came back <state>; offered relaunch` |
| WARNING | any source came back unverified | `deep-research: run <id> has <n> unverified sources; surfaced to Louis` |
| WARNING | model or effort downgraded from the defaults | `deep-research: downgraded to <model>/<effort>; reason=<why>` |
| INFO | run launched | `deep-research: launched <id>; <model>/<effort>; <n> sub-questions` |
| INFO | run collected clean | `deep-research: collected <id>; <n> sources all verified; <duration>` |

Concrete invocation examples:

```
claude-log deep-research-claude-web INFO "deep-research: launched 2026-08-31-143022-vector-db; fable/max; 5 sub-questions"
claude-log deep-research-claude-web WARNING "deep-research: run 2026-08-31-143022-vector-db has 2 unverified sources; surfaced to Louis"
claude-log deep-research-claude-web CRITICAL "deep-research: launch failed: claude --bg exited 1"
```

# deep-research-claude-web

Run a deep research task in a detached background session, so it costs neither this
session's context window nor the terminal's attention, and come back with a cited report
on disk.

## When to use this instead of researching in-session

Use this when the research is long, the answer is a document rather than a conversational
reply, and the calling session has other work to do. Research in-session instead when the
answer must immediately inform what you are both doing right now.

## Phase 1: Build the charter

An expensive detached run must not start from a misunderstanding. Before launching:

1. Draft a charter with these sections: the question as a level-1 heading, then
   `## Decision this feeds`, `## Must answer` (3-8 bullets), `## Source bar` with
   `tier:` and `recency:` lines, `## Deliverable`, `## Out of scope`.
2. Fill what the request already tells you. Ask only about fields you genuinely cannot
   infer - use `AskUserQuestion`, and ask about the decision it feeds first, since that
   is what makes the sub-questions answerable.
3. Write it to `<out>/charter.md` and **show it to Louis before launching.**

If the request is vague about what it feeds, say so rather than inventing a decision.
A charter with a made-up purpose produces a report answering nobody's question.

## Phase 2: Launch

```bash
deep-research launch --charter <out>/charter.md --out <out>
```

Defaults are `--model fable --effort max`, which is what to use unless there is a reason
not to. Propose `--model opus --effort high` when the charter has more than 8
sub-questions or the cap is already reached, and say plainly what is being traded away.

There is no programmatic way to read remaining subscription quota. When quota is a live
concern, tell Louis to run `/usage` in any interactive session rather than guessing at
it.

At most 2 runs may be in flight. If the cap refuses a launch, report which runs are
holding it and offer either waiting or `--force` - never force silently.

## Phase 3: Collect

```bash
deep-research status            # all runs
deep-research collect <run-id>  # summary, exits non-zero when anything needs attention
```

`collect` exits 1 when sources went unverified, questions went unanswered, or the run did
not finish. **Report every unverified source by name.** Never present a run with
unverified sources as a clean result - silently downgrading a citation is the exact
failure this contract exists to prevent.

If a run comes back `lost` (the machine rebooted mid-run), the charter is still on disk:
offer to relaunch from it.

## What this does not do

- It does not run on claude.ai. v1 runs locally, detached. The cloud engine is specified
  but deferred; see the design doc for the exact upgrade path.
- It does not enforce any project's citation registry. The report is portable Markdown;
  run the project's own citation gate if the material graduates into a deliverable.
````

- [ ] **Step 2: Verify the skill file is well formed**

Run: `cd ~/42/SpaceSuit && head -5 skills/deep-research-claude-web/SKILL.md`
Expected: YAML frontmatter opening with `---` and a `name:` of `deep-research-claude-web`

- [ ] **Step 3: Confirm nothing private leaked into the public repo**

Run:
```bash
cd ~/42/SpaceSuit && grep -rniE 'thebeast|tinybutmighty|develle|192\.168|/home/[a-z]+/42' \
  deep_research/ skills/deep-research-claude-web/ dotfiles/bin/deep-research \
  && echo "LEAK FOUND, fix before continuing" || echo "clean"
```
Expected: `clean`

If this finds `$HOME/42/SpaceSuit` inside `dotfiles/bin/deep-research`, that is a generic
path with no machine or person named in it and is acceptable; any hostname is not.

- [ ] **Step 4: Register the skill and the binary**

Do NOT hand-edit `dotfiles/dotfiles.json`. Invoke the `add-dotfile` skill and ask it to
register two entries:
- `skills/deep-research-claude-web/` to `~/.claude/skills/deep-research-claude-web/`
- `dotfiles/bin/deep-research` to `~/.local/bin/deep-research` (executable)

- [ ] **Step 5: Deploy and verify the CLI is reachable**

Run: `cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles deploy`
Then: `deep-research list --runs-root /tmp/no-such-runs`
Expected: prints `no runs found`

- [ ] **Step 6: Run the full fast suite one more time**

Run: `cd ~/42/SpaceSuit && uv run pytest tests/ -q -m "not slow"`
Expected: every test passes EXCEPT one pre-existing, unrelated failure that was
already red before this work started:
`tests/test_secrets_cli.py::test_run_resolves_refs_into_child_env` (the secrets CLI
resolving a `pass://` ref, which needs the vault unlocked). Baseline measured at
commit 0004c0b: 439 passed, 1 failed.

Do NOT fix, skip, or delete that test. It is out of scope for this plan and belongs
to the repo owner. Confirm it is the ONLY failure; any other failure is yours.

- [ ] **Step 7: Commit and push**

```bash
cd ~/42/SpaceSuit
git add skills/deep-research-claude-web/SKILL.md dotfiles/dotfiles.json
git commit -m "deep-research: the skill and its dotfile registration"
git push
```

If the push is rejected as non fast forward, run `git pull --rebase` once and push again.
Several machines commit to this repo.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Charter, written brief | 1 |
| Launcher CLI, manifest | 2, 5, 6 |
| Runner prompt, citation rules | 4 |
| Output contract, DONE sentinel | 3, 4 |
| Defaults fable/max | 5, 6 |
| Concurrency cap of 2 | 5 |
| Cheaper profile proposal, manual /usage check | 8 (skill Phase 2) |
| Failure modes: reboot, unverified, partial, dir collision, launch failure | 3, 5, 6 |
| Security posture, no credentials | inherent; nothing reads a token |
| Observability table | 8 |
| Testing: unit pure, one real integration, no mock-only | 1-6, 7 |
| Cloud engine deferred | not implemented, by design |

**Placeholder scan:** none. Every step carries runnable code or an exact command.

**Type consistency:** `Manifest` field names are identical across Tasks 2, 5, 6.
`RunState` members are used by their enum identity in Tasks 3, 6, 7. `parse_charter` and
`render_charter` keep their Task 1 signatures throughout. `launch` keyword names
(`out_dir`, `runs_root`, `model`, `effort`, `now`, `notify_script`, `force`, `runner`)
match between Task 5's definition and its callers in Tasks 6 and 7.

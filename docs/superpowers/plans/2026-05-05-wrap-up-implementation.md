# wrap-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `wrap-up` skill end-to-end on Louis's machine, including the minimum foundation prerequisites it needs (the `claude-log` helper, the `~/Setup/dotfiles/bin/` directory, and the dotfile entry pattern Louis already established for `claude_skill_*` directories).

**Architecture:** Bundles the foundation pieces from spec [2026-04-21-skill-storage-observability](../../plans/2026-04-21-skill-storage-observability-design.md) (as amended 2026-05-05) that wrap-up directly depends on, then implements wrap-up itself per spec [2026-05-05-wrap-up-skill](../../plans/2026-05-05-wrap-up-skill-design.md). Skill body is a markdown SKILL.md file Claude follows; the only "code" code here is the `claude-log` shell script.

**Tech Stack:** POSIX shell (bash), Python 3 (existing `src_dotfiles` for deploy), pytest for shell-script tests, markdown for SKILL.md.

**Pattern note:** This plan adopts the `dotfiles/claude_skill_<snake_name>/` directory pattern Louis already established (visible in `claude_skill_open_local_port`, `claude_skill_link_develle_domain`, `claude_skill_share_file`). Snake-case in source dir + alias, kebab-case in deploy path. `only_devices` defaulted to current device only.

---

## Pre-flight: working directory

All work happens in `~/Setup/` on the `master` branch (per Louis's CLAUDE.md). No worktree.

Verify before starting:

```bash
cd ~/Setup
git status              # claude_skill_* WIP dirs may exist; that's intentional
git rev-parse --abbrev-ref HEAD   # should be 'master'
test -d src_dotfiles && test -f dotfiles/dotfiles.json   # foundation files exist
```

The unrelated WIP (`.zshrc`, `claude_md`, `claude_settings`, `config_nvim/*`, `nvim-cheatsheet.md`) was stashed before this plan started executing, so it doesn't leak into plan commits. The three `claude_skill_*` directories and their `dotfiles.json` entries are kept — they're the established pattern this plan adopts.

---

## File Structure

```
~/Setup/
├── dotfiles/
│   ├── bin/
│   │   └── claude-log                              (NEW — Task 2)
│   ├── claude_skill_wrap_up/                       (NEW DIR — Task 4)
│   │   └── SKILL.md                                (NEW — Tasks 4-9)
│   └── dotfiles.json                               (MODIFY — Tasks 3, 10)
├── tests/
│   └── test_claude_log.py                          (NEW — Task 2)
└── docs/
    └── superpowers/
        └── plans/
            └── 2026-05-05-wrap-up-implementation.md   (THIS FILE)
```

---

## Task 1: `~/Setup/dotfiles/bin/` directory

**Files:**
- Create: `~/Setup/dotfiles/bin/.keep` (placeholder so the empty dir lands in git)

- [ ] **Step 1: Create the directory.**

```bash
mkdir -p ~/Setup/dotfiles/bin
touch ~/Setup/dotfiles/bin/.keep
```

- [ ] **Step 2: Verify.**

```bash
ls -la ~/Setup/dotfiles/bin
```

Expected: directory listed, `.keep` file present.

- [ ] **Step 3: Commit.**

```bash
cd ~/Setup
git add dotfiles/bin/.keep
git commit -m "feat(dotfiles): add dotfiles/bin/ for deployable helper scripts

Co-locates supporting binaries with other dotfiles so the existing
deploy pattern (one entry per file in dotfiles.json) applies uniformly.
First user: claude-log (next commit)."
```

---

## Task 2: `claude-log` helper script (TDD)

**Files:**
- Create: `~/Setup/dotfiles/bin/claude-log`
- Create: `~/Setup/tests/test_claude_log.py`

- [ ] **Step 1: Write the failing test.**

`~/Setup/tests/test_claude_log.py`:

```python
"""Tests for the dotfiles/bin/claude-log helper script.

The script appends one structured line per call to a target log file.
Format: 'YYYY-MM-DD skill-name [LEVEL] : message'
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "dotfiles" / "bin" / "claude-log"
LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} (?P<skill>\S+) \[(?P<level>INFO|WARNING|CRITICAL)\] : (?P<msg>.+)$"
)


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path


def run(args, env_overrides=None, log_path=None):
    env = os.environ.copy()
    if log_path is not None:
        env["CLAUDE_LOG_FILE"] = str(log_path)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} not found"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_appends_one_line(log_dir):
    log = log_dir / "lessons.md"
    result = run(["my-skill", "INFO", "hello world"], log_path=log)
    assert result.returncode == 0, result.stderr
    assert log.exists()
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    m = LINE_RE.match(lines[0])
    assert m, f"line not matching format: {lines[0]!r}"
    assert m["skill"] == "my-skill"
    assert m["level"] == "INFO"
    assert m["msg"] == "hello world"


def test_creates_log_file_if_missing(log_dir):
    log = log_dir / "subdir" / "lessons.md"
    assert not log.exists()
    result = run(["my-skill", "INFO", "first"], log_path=log)
    assert result.returncode == 0, result.stderr
    assert log.exists()


def test_rejects_unknown_level(log_dir):
    log = log_dir / "lessons.md"
    result = run(["my-skill", "DEBUG", "should fail"], log_path=log)
    assert result.returncode == 2
    assert "level" in result.stderr.lower()
    assert not log.exists()


def test_rejects_missing_args(log_dir):
    log = log_dir / "lessons.md"
    result = run(["my-skill", "INFO"], log_path=log)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower() or "argument" in result.stderr.lower()


def test_two_calls_two_lines(log_dir):
    log = log_dir / "lessons.md"
    run(["my-skill", "INFO", "first"], log_path=log)
    run(["my-skill", "WARNING", "second"], log_path=log)
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert "[INFO]" in lines[0]
    assert "[WARNING]" in lines[1]


def test_concurrent_calls_no_interleave(log_dir):
    """Hammer the lock with N parallel invocations; verify all lines land cleanly."""
    log = log_dir / "lessons.md"
    procs = []
    N = 20
    for i in range(N):
        env = os.environ.copy()
        env["CLAUDE_LOG_FILE"] = str(log)
        p = subprocess.Popen(
            [str(SCRIPT), "my-skill", "INFO", f"msg-{i}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        procs.append(p)
    for p in procs:
        p.wait()
    lines = log.read_text().splitlines()
    assert len(lines) == N, f"expected {N} lines, got {len(lines)}: {lines!r}"
    for line in lines:
        assert LINE_RE.match(line), f"line not matching format: {line!r}"


def test_default_log_path_is_claude_lessons(tmp_path):
    """Without CLAUDE_LOG_FILE set, default is ~/.claude/lessons.md.

    We override HOME to redirect, so the test doesn't touch the real file.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("CLAUDE_LOG_FILE", None)
    result = subprocess.run(
        [str(SCRIPT), "my-skill", "INFO", "hello"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    expected = fake_home / ".claude" / "lessons.md"
    assert expected.exists()
```

- [ ] **Step 2: Run tests to verify they fail.**

```bash
cd ~/Setup && uv run pytest tests/test_claude_log.py -v
```

Expected: all tests fail (script doesn't exist yet).

- [ ] **Step 3: Write the script.**

`~/Setup/dotfiles/bin/claude-log`:

```bash
#!/usr/bin/env bash
# ABOUTME: Append a structured observability log line to ~/.claude/lessons.md.
# ABOUTME: Usage: claude-log <skill-name> <INFO|WARNING|CRITICAL> "<message>"

set -euo pipefail

usage() {
    echo "usage: claude-log <skill-name> <INFO|WARNING|CRITICAL> \"<message>\"" >&2
    echo "  Set CLAUDE_LOG_FILE to override target path (default: \$HOME/.claude/lessons.md)." >&2
    exit 2
}

[ "$#" -eq 3 ] || usage

skill="$1"
level="$2"
msg="$3"

case "$level" in
    INFO|WARNING|CRITICAL) ;;
    *) echo "claude-log: invalid level '$level' (must be INFO|WARNING|CRITICAL)" >&2; exit 2 ;;
esac

log_file="${CLAUDE_LOG_FILE:-$HOME/.claude/lessons.md}"
log_dir="$(dirname "$log_file")"
mkdir -p "$log_dir"

date_str="$(date +%Y-%m-%d)"
line="$date_str $skill [$level] : $msg"

# mkdir-based portable lock (works on macOS BSD + Linux GNU; no flock dependency)
lock_dir="$log_file.lock"
attempts=0
max_attempts=50    # 50 * 0.1s = 5s timeout
while ! mkdir "$lock_dir" 2>/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge "$max_attempts" ]; then
        echo "claude-log: lock timeout, best-effort append" >&2
        break
    fi
    sleep 0.1
done

trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT

printf '%s\n' "$line" >> "$log_file"
```

- [ ] **Step 4: Make executable.**

```bash
chmod +x ~/Setup/dotfiles/bin/claude-log
```

- [ ] **Step 5: Run tests to verify they pass.**

```bash
cd ~/Setup && uv run pytest tests/test_claude_log.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 6: Manual smoke test.**

```bash
TMPLOG=$(mktemp)
CLAUDE_LOG_FILE="$TMPLOG" ~/Setup/dotfiles/bin/claude-log smoke-test INFO "hello from plan task 2"
cat "$TMPLOG"
rm "$TMPLOG"
```

Expected: one line matching `YYYY-MM-DD smoke-test [INFO] : hello from plan task 2`.

- [ ] **Step 7: Commit.**

```bash
cd ~/Setup
git add dotfiles/bin/claude-log tests/test_claude_log.py
git commit -m "feat(bin): add claude-log helper for skill observability

Portable POSIX shell helper that appends structured log lines to
~/.claude/lessons.md (or CLAUDE_LOG_FILE override). Uses mkdir-based
lock for atomic concurrent appends — works on macOS BSD and Linux GNU
without requiring flock. Validates INFO/WARNING/CRITICAL levels.

Tests cover: format, file creation, level validation, missing args,
sequential appends, concurrent appends (lock), default path resolution."
```

---

## Task 3: Deploy `claude-log` via dotfiles

**Files:**
- Modify: `~/Setup/dotfiles/dotfiles.json`

- [ ] **Step 1: Determine current device identifier.**

```bash
cd ~/Setup && python -c "from src_dotfiles.config import config; print(config.identifier)"
```

Expected: a string like `TheBeast.ezalos`. Note this value as `<DEVICE>` for the JSON edit.

Verify `~/.local/bin` exists and is on PATH:

```bash
mkdir -p ~/.local/bin
echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin" && echo "PATH OK" || echo "PATH MISSING ~/.local/bin"
```

If "PATH MISSING", add `~/.local/bin` to PATH via `~/.zshrc` before continuing — `claude-log` won't be findable on PATH otherwise.

- [ ] **Step 2: Add `claude-log` entry to `dotfiles.json`.**

Edit `~/Setup/dotfiles/dotfiles.json`. Add a new top-level entry inside the `"dotfiles": { ... }` object, near the existing `claude_skill_*` entries:

```json
"claude_log": {
    "alias": "claude_log",
    "main": "dotfiles/bin/claude-log",
    "deploy": {
        "<DEVICE>": {
            "deploy_path": "/home/ezalos/.local/bin/claude-log",
            "backups": []
        }
    },
    "only_devices": null,
    "variants": null
}
```

Note: `only_devices: null` here (not restricted) because the helper is generally useful on any machine running Claude Code, not tied to local services.

Replace `<DEVICE>` with the value from Step 1. Replace `/home/ezalos` with the actual `$HOME` if different.

- [ ] **Step 3: Verify JSON parses.**

```bash
python -c "import json; json.load(open('/home/ezalos/Setup/dotfiles/dotfiles.json'))" && echo "JSON OK"
```

Expected: `JSON OK`. If it fails, fix the syntax error before proceeding.

- [ ] **Step 4: Deploy via src_dotfiles.**

```bash
cd ~/Setup && python -m src_dotfiles deploy claude_log
```

Expected: log lines indicating symlink creation. The deployer will symlink `/home/ezalos/.local/bin/claude-log` → `/home/ezalos/Setup/dotfiles/bin/claude-log`.

If `python -m src_dotfiles deploy` is not the right invocation, run `python -m src_dotfiles --help` to find the correct subcommand and update this step.

- [ ] **Step 5: Verify symlink + functionality.**

```bash
ls -la ~/.local/bin/claude-log     # should be symlink → ~/Setup/dotfiles/bin/claude-log
which claude-log                    # should resolve to ~/.local/bin/claude-log
claude-log smoke-deploy INFO "deployed via dotfiles"
tail -1 ~/.claude/lessons.md       # should show the line
```

Expected: symlink exists, `which` resolves, log line appended to real `~/.claude/lessons.md`.

- [ ] **Step 6: Commit.**

```bash
cd ~/Setup
git add dotfiles/dotfiles.json
git commit -m "feat(dotfiles): track and deploy claude-log helper

claude-log now deploys to ~/.local/bin via the existing src_dotfiles
symlink mechanism. only_devices: null since the helper is generally
useful wherever Claude Code runs (not tied to local services like the
claude_skill_* entries are)."
```

---

## Task 4: wrap-up SKILL.md scaffold (frontmatter + `## Observability`)

**Files:**
- Create: `~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md`

- [ ] **Step 1: Create the skill directory.**

```bash
mkdir -p ~/Setup/dotfiles/claude_skill_wrap_up
```

- [ ] **Step 2: Write the SKILL.md scaffold (frontmatter + Observability + section stubs).**

`~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md`:

````markdown
---
name: wrap-up
description: Use when Louis says "wrap up", "close session", "end session", "wrap things up", "close out this task", or invokes /wrap-up. Runs end-of-session checklist for shipping, memory, and self-improvement. Auto-applies routine actions, gates ambiguous memory placements for review, and produces one consolidated report.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
---

# Session Wrap-Up

Run four phases in order. Each is conversational and inline — no separate
documents. All phases auto-apply (with the confidence gate in Phase 2).
Present one consolidated report at the end.

## Observability

This skill follows the universal observability baseline (see
`docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:** CRITICAL on abort, WARNING on correction/fallback/retry/precondition-fail, INFO on edge path.

**Skill-specific triggers:**

| Level    | Trigger                                                    | Message template                                          |
|----------|------------------------------------------------------------|-----------------------------------------------------------|
| CRITICAL | Phase 1 commit/push fails                                  | `wrap-up: phase1 ship failed in <repo>: <reason>`         |
| CRITICAL | Phase 1 deploy command exits non-zero                      | `wrap-up: deploy failed in <repo>: <stderr-tail>`         |
| WARNING  | Low-confidence memory placement (Phase 2)                  | `wrap-up: ambiguous memory placement for <topic>; chose <tier>` |
| WARNING  | Phase 1 file rename collision                              | `wrap-up: file rename collision: <from> -> <to>`          |
| WARNING  | Task flagged orphaned (>2 sessions stale)                  | `wrap-up: orphaned task <id>: <subject>`                  |
| INFO     | No deploy marker in repo                                   | `wrap-up: no deploy marker in <repo>; skipped`            |
| INFO     | Nothing publishable                                        | `wrap-up: no publishable content this session`            |
| INFO     | Nothing to improve                                         | `wrap-up: no self-improvement findings`                   |

Log via: `claude-log wrap-up <LEVEL> "<message>"`

# triggers I might have missed: subagent failures during phase execution, partial-session crashes

## Phase 1: Ship It

(populated in Task 5)

## Phase 2: Remember It

(populated in Task 6)

## Phase 3: Review & Apply

(populated in Task 7)

## Phase 4: Publish It

(populated in Task 8)

## Final consolidated report

(populated in Task 9)
````

- [ ] **Step 3: Static check — frontmatter parses, Observability section present, claude-log call present, name match.**

```bash
SKILL=~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md
head -1 $SKILL | grep -q '^---$' && echo "frontmatter open OK"
grep -q '^## Observability' $SKILL && echo "section OK"
grep -qE 'claude-log wrap-up (INFO|WARNING|CRITICAL)' $SKILL && echo "log call OK"
grep -q '^name: wrap-up$' $SKILL && echo "name OK"
```

Expected: all four "OK" lines.

- [ ] **Step 4: Commit.**

```bash
cd ~/Setup
git add dotfiles/claude_skill_wrap_up/SKILL.md
git commit -m "feat(skill): scaffold wrap-up SKILL.md with observability contract

Frontmatter, ## Observability section with universal baseline + 8
skill-specific triggers, and the four phase headers as stubs to be
filled in subsequent commits. Static contract check passes."
```

---

## Task 5: Phase 1 — Ship It

**Files:**
- Modify: `~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md` — replace the `## Phase 1: Ship It` stub

- [ ] **Step 1: Replace the Phase 1 stub.**

Find:
```markdown
## Phase 1: Ship It

(populated in Task 5)
```

Replace with:

````markdown
## Phase 1: Ship It

### 1a. Commit

For each repo directory touched during this session:

1. Run `git status --porcelain` in that repo.
2. If output is empty: skip — repo is clean.
3. If non-empty:
   - Inspect the diff (`git diff` and `git diff --cached`) to draft a one-line commit subject summarizing the change.
   - Stage relevant files explicitly (avoid `git add -A` — never commit secrets or unrelated dirty work).
   - Commit on the default branch (`master` for Louis's own repos; check `git symbolic-ref --short HEAD` first).
   - **Do NOT use `--no-verify`.** If a hook fails, treat it as a precondition failure: log `WARNING wrap-up: phase1 hook failed in <repo>: <hook-name>`, leave the commit unmade, and report in the final summary.
4. **Push policy:** push only if (a) the user explicitly asked for pushes during this session, OR (b) the repo's CLAUDE.md frontmatter has `auto-push: true`. Otherwise leave un-pushed and report.

If a commit or push fails for non-hook reasons (network error, etc.), log:

```
claude-log wrap-up CRITICAL "wrap-up: phase1 ship failed in <repo>: <reason>"
```

### 1b. File placement check

For each file created or modified during this session:

1. **Naming.** If the project has a CLAUDE.md with naming conventions, check the file matches; otherwise infer from neighbor files (snake_case vs kebab-case). If a violation is found, rename via `git mv`.
2. **Location.** If the file is misplaced (e.g., a test file in `src/`, a doc in the project root), move it to the correct subfolder.
3. **Document files** (.md, .docx, .pdf, .xlsx, .pptx) created at the workspace root or in a code directory: move to `docs/` if a `docs/` folder exists.

On a rename collision (target name already exists), log:

```
claude-log wrap-up WARNING "wrap-up: file rename collision: <from> -> <to>"
```

…and leave the file in place; report in summary.

### 1c. Deploy

Detect a deploy step by checking, in order, the FIRST match:

1. `Makefile` containing a `deploy:` target → run `make deploy`
2. `scripts/deploy.sh` (executable) → run `scripts/deploy.sh`
3. `bin/deploy` (executable) → run `bin/deploy`
4. Project `CLAUDE.md` containing `## Deploy` followed by a fenced bash code block → run that block's first command

If a marker matched: run the command. Capture stdout/stderr.
- On exit 0: report `Deploy: ran <command>` in summary.
- On non-zero exit: log:

  ```
  claude-log wrap-up CRITICAL "wrap-up: deploy failed in <repo>: <stderr-tail>"
  ```

  Report in summary, but DO NOT abort wrap-up — proceed to subsequent phases.

If NO marker matched: log:

```
claude-log wrap-up INFO "wrap-up: no deploy marker in <repo>; skipped"
```

…and report `Deploy: skipped (no marker)` in summary. **Do NOT ask the user about manual deployment.**

### 1d. Task cleanup

1. Run TaskList. Read all tasks.
2. For tasks completed during this session but still `pending` or `in_progress`: TaskUpdate to `completed`.
3. For tasks `pending` for ≥2 sessions without progress: mark them as orphaned in the summary. Do NOT auto-delete. Log:

   ```
   claude-log wrap-up WARNING "wrap-up: orphaned task <id>: <subject>"
   ```
````

- [ ] **Step 2: Static-check the contract still holds.**

```bash
grep -qE 'claude-log wrap-up (INFO|WARNING|CRITICAL)' ~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md && echo OK
```

- [ ] **Step 3: Commit.**

```bash
cd ~/Setup
git add dotfiles/claude_skill_wrap_up/SKILL.md
git commit -m "feat(skill): wrap-up Phase 1 (Ship It) instructions

Commit + push policy, file placement check, marker-based deploy
detection (Makefile / scripts/deploy.sh / bin/deploy / CLAUDE.md ##
Deploy section), and task cleanup with orphan detection."
```

---

## Task 6: Phase 2 — Remember It

**Files:**
- Modify: `~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md`

- [ ] **Step 1: Replace the Phase 2 stub.**

Find:
```markdown
## Phase 2: Remember It

(populated in Task 6)
```

Replace with:

````markdown
## Phase 2: Remember It

Review what was learned this session. For each piece of knowledge, choose
a destination tier per the framework:

| Tier               | Path                                                | Use for                                                                |
|--------------------|-----------------------------------------------------|------------------------------------------------------------------------|
| Auto memory        | `~/.claude/projects/<project>/memory/`              | Patterns Claude discovered, project quirks, debugging insights         |
| Project CLAUDE.md  | `<repo>/CLAUDE.md`                                  | Permanent project rules, conventions, commands, architecture           |
| Project rules      | `<repo>/.claude/rules/<topic>.md` (with `paths:`)   | Topic-specific instructions scoped to file types                       |
| CLAUDE.local.md    | `<repo>/CLAUDE.local.md`                            | Personal WIP context, sandbox creds, current focus (not committed)     |
| `@import`          | reference in CLAUDE.md                              | Cross-reference rather than duplicate                                  |

### Confidence-gated auto-apply

For each knowledge item:

- **High confidence** (one tier clearly fits per the table): auto-apply,
  list under "Applied" in the summary.
- **Low confidence** (≥2 tiers plausibly fit, OR user intent didn't
  clearly indicate scope): auto-apply *the chosen tier* but list under
  "Review please" in the summary so Louis can quickly relocate.

Heuristics for "low confidence":
- Could be project-wide OR file-type-scoped (CLAUDE.md vs `.claude/rules/`)
- Could be permanent OR ephemeral (CLAUDE.md vs CLAUDE.local.md)
- Refers to something cross-cutting

When low confidence, log:

```
claude-log wrap-up WARNING "wrap-up: ambiguous memory placement for <topic>; chose <tier>"
```
````

- [ ] **Step 2: Commit.**

```bash
cd ~/Setup
git add dotfiles/claude_skill_wrap_up/SKILL.md
git commit -m "feat(skill): wrap-up Phase 2 (Remember It) instructions

Memory tier framework with confidence-gated auto-apply: clear cases ship
silently, ambiguous cases get applied + flagged for review."
```

---

## Task 7: Phase 3 — Review & Apply

**Files:**
- Modify: `~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md`

- [ ] **Step 1: Replace the Phase 3 stub.**

Find:
```markdown
## Phase 3: Review & Apply

(populated in Task 7)
```

Replace with:

````markdown
## Phase 3: Review & Apply

Analyze the conversation for self-improvement findings. **Auto-apply all
actionable findings immediately**; do not gate per-finding.

If the session was short or routine with nothing notable, output
"Nothing to improve" in the summary and log:

```
claude-log wrap-up INFO "wrap-up: no self-improvement findings"
```

…then proceed to Phase 4.

### Finding categories

- **Skill gap** — Claude struggled, got wrong, needed multiple attempts.
- **Friction** — Repeated manual steps, things Louis had to ask explicitly that should have been automatic.
- **Knowledge** — Facts Claude didn't know but should have.
- **Automation** — Repetitive patterns that could become skills, hooks, or scripts.

### Action types

- **CLAUDE.md** — edit relevant project or global CLAUDE.md.
- **Rules** — create or update `<repo>/.claude/rules/<topic>.md`.
- **Auto memory** — append insight to the project's auto-memory.
- **Skill / Hook spec** — write a spec to `~/Setup/docs/plans/YYYY-MM-DD-<name>-design.md`. Do NOT auto-build the skill.
- **CLAUDE.local.md** — create or update per-project local memory.

### Summary format (for the consolidated report)

```
Findings (applied):

1. ✅ Skill gap: <description>
   → [CLAUDE.md] <what was added>

2. ✅ Knowledge: <description>
   → [Rules] <file>

3. ✅ Automation: <description>
   → [Skill spec] <path-to-new-spec.md>

---
No action needed:

4. <description>
   <reason — already documented / out of scope / etc.>
```
````

- [ ] **Step 2: Commit.**

```bash
cd ~/Setup
git add dotfiles/claude_skill_wrap_up/SKILL.md
git commit -m "feat(skill): wrap-up Phase 3 (Review & Apply) instructions

Self-improvement findings auto-applied across CLAUDE.md / rules / auto
memory / spec / CLAUDE.local.md tiers, with concrete summary format."
```

---

## Task 8: Phase 4 — Publish It

**Files:**
- Modify: `~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md`

- [ ] **Step 1: Replace the Phase 4 stub.**

Find:
```markdown
## Phase 4: Publish It

(populated in Task 8)
```

Replace with:

````markdown
## Phase 4: Publish It

After all other phases complete, review the full conversation for
publishable material:

- Interesting technical solutions or debugging stories.
- Community-relevant announcements or updates.
- Educational content (how-tos, tips, lessons learned).
- Project milestones or feature launches.

### If publishable material exists

Create a per-post directory under `~/Drafts/`:

```bash
mkdir -p ~/Drafts/<post-slug>
```

Where `<post-slug>` is a kebab-case version of the working title.

Write tailored drafts for each platform:

- `~/Drafts/<post-slug>/Reddit.md` — tldr at top, then full-post structure
- `~/Drafts/<post-slug>/Blog.md` — long-form with section headings

Platforms supported in v1: **Reddit, Blog**. (To add more — HN,
Mastodon, X, etc. — extend this list.)

In the consolidated report, present:

```
Potential content to publish:

1. "<Title of post>" — 1-2 sentence description.
   Drafts: ~/Drafts/<post-slug>/Reddit.md, Blog.md

(Drafts written. No posting happens automatically — paste manually
when ready.)
```

If multiple publishable items: write all drafts. Note the most
time-sensitive one in the summary; do NOT post automatically.

### If nothing publishable

Output `Nothing worth publishing from this session.` in the summary and log:

```
claude-log wrap-up INFO "wrap-up: no publishable content this session"
```
````

- [ ] **Step 2: Commit.**

```bash
cd ~/Setup
git add dotfiles/claude_skill_wrap_up/SKILL.md
git commit -m "feat(skill): wrap-up Phase 4 (Publish It) instructions

Per-platform drafts to ~/Drafts/<post-slug>/. Reddit + Blog targets in
v1. No auto-posting; drafts persist for manual publication."
```

---

## Task 9: Final consolidated report

**Files:**
- Modify: `~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md`

- [ ] **Step 1: Replace the final-report stub.**

Find:
```markdown
## Final consolidated report

(populated in Task 9)
```

Replace with:

````markdown
## Final consolidated report

After all four phases complete, present this single report as the final
output of the skill:

````
# Wrap-up — YYYY-MM-DD HH:MM

## Phase 1 — Ship It
- Committed: <repos and short subjects>
- Pushed: <repos>  (or "none")
- File placement: <fixes>  (or "no changes needed")
- Deploy: <ran X / failed: ... / skipped (no marker)>
- Tasks: <N completed, M flagged orphaned>

## Phase 2 — Remember It
Applied (high-confidence):
- [<tier>] <summary>

Review please (low-confidence — applied to <tier>, may want relocation):
- <summary>  (or "none")

## Phase 3 — Review & Apply
Applied:
1. <category>: <description> → [<tier>] <action>

No action needed:
2. <description> — <reason>

## Phase 4 — Publish It
- <Title>: drafted at ~/Drafts/<slug>/  (or "Nothing worth publishing")

## Self-observability
<count> entries written to ~/.claude/lessons.md this run.
````

The `<count>` is the number of `claude-log wrap-up` lines that landed
in `~/.claude/lessons.md` during this run — verify by:

```bash
# Replace YYYY-MM-DD with today's date
grep -c "^$(date +%Y-%m-%d) wrap-up " ~/.claude/lessons.md
```

(But subtract any that were already there before this run started.)
````

- [ ] **Step 2: Final contract check.**

```bash
SKILL=~/Setup/dotfiles/claude_skill_wrap_up/SKILL.md
grep -q '^name: wrap-up$' $SKILL
grep -q '^## Observability' $SKILL
grep -cE 'claude-log wrap-up (INFO|WARNING|CRITICAL)' $SKILL   # ≥ 1
echo "contract check complete"
```

- [ ] **Step 3: Commit.**

```bash
cd ~/Setup
git add dotfiles/claude_skill_wrap_up/SKILL.md
git commit -m "feat(skill): wrap-up final consolidated report format

Single report at end of all four phases with explicit sections per
phase, low-confidence flag, self-observability count. Skill body
complete; contract check passes."
```

---

## Task 10: Deploy wrap-up via dotfiles + smoke test

**Files:**
- Modify: `~/Setup/dotfiles/dotfiles.json`

- [ ] **Step 1: Add the wrap-up skill entry to dotfiles.json.**

Use the same `<DEVICE>` value from Task 3 Step 1.

Add to `dotfiles.json`, near the existing `claude_skill_*` entries:

```json
"claude_skill_wrap_up": {
    "alias": "claude_skill_wrap_up",
    "main": "dotfiles/claude_skill_wrap_up",
    "deploy": {
        "<DEVICE>": {
            "deploy_path": "/home/ezalos/.claude/skills/wrap-up",
            "backups": []
        }
    },
    "only_devices": ["<DEVICE>"],
    "variants": null
}
```

- [ ] **Step 2: Verify JSON parses.**

```bash
python -c "import json; json.load(open('/home/ezalos/Setup/dotfiles/dotfiles.json'))" && echo "JSON OK"
```

- [ ] **Step 3: Deploy via src_dotfiles.**

```bash
cd ~/Setup && python -m src_dotfiles deploy claude_skill_wrap_up
```

Expected: log lines indicating symlink creation. After this, `~/.claude/skills/wrap-up` is a symlink → `~/Setup/dotfiles/claude_skill_wrap_up`.

- [ ] **Step 4: Verify deploy.**

```bash
ls -la ~/.claude/skills/wrap-up      # should be symlink
test -f ~/.claude/skills/wrap-up/SKILL.md && echo "SKILL.md visible"
head -10 ~/.claude/skills/wrap-up/SKILL.md
```

Expected: symlink, "SKILL.md visible", frontmatter visible.

- [ ] **Step 5: Smoke test the skill (in a fresh chat session).**

Open a new Claude Code conversation. Type:

> wrap up

Expected: Claude finds the new wrap-up skill (the description triggers on "wrap up"), invokes it, runs through the four phases, produces the consolidated report. Verify:

- At least one new line appears in `~/.claude/lessons.md` matching `<today> wrap-up [INFO|WARNING|CRITICAL] : ...`
- The consolidated report has the four phase sections.
- For phases that did nothing meaningful (e.g., no publishable content), the report says so and `lessons.md` has the corresponding INFO line.

If the smoke test reveals a real issue, fix and re-test before declaring done.

- [ ] **Step 6: Commit.**

```bash
cd ~/Setup
git add dotfiles/dotfiles.json
git commit -m "feat(dotfiles): track and deploy wrap-up skill

wrap-up SKILL.md now deploys to ~/.claude/skills/wrap-up via the
existing src_dotfiles symlink mechanism. First user-authored skill
shipped via the foundation pattern from spec
2026-04-21-skill-storage-observability (as amended 2026-05-05 to adopt
the pre-existing claude_skill_* convention)."
```

---

## Self-review checklist (run after writing the plan, before executing)

- [x] **Spec coverage:** every section of the wrap-up spec maps to a task.
- [x] **Placeholder scan:** no "TBD", "implement later", "similar to above" — every step has the actual code/command.
- [x] **Type/name consistency:** skill name `wrap-up` is consistent across SKILL.md frontmatter, every `claude-log wrap-up …` invocation, and the dotfiles entry `claude_skill_wrap_up`. Helper script alias `claude_log` is consistent everywhere it appears.
- [x] **Contract verification:** Tasks 4 and 9 explicitly run the three contract checks (Observability section, claude-log call, name match).
- [x] **Smoke test exists:** Task 10 Step 5 invokes the skill end-to-end.

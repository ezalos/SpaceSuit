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

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (would have produced wrong result), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during skill execution.** No judgment about "noteworthy" — log every distinct user message that conveys preference, redirection, refinement, or commentary. Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change — already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level    | Trigger                                                    | Message template                                          |
|----------|------------------------------------------------------------|-----------------------------------------------------------|
| CRITICAL | Phase 1 commit/push fails                                  | `wrap-up: phase1 ship failed in <repo>: <reason>`         |
| CRITICAL | Phase 1 deploy command exits non-zero                      | `wrap-up: deploy failed in <repo>: <stderr-tail>`         |
| WARNING  | Low-confidence memory placement (Phase 2)                  | `wrap-up: ambiguous memory placement for <topic>; chose <tier>` |
| WARNING  | Phase 1 file rename collision                              | `wrap-up: file rename collision: <from> -> <to>`          |
| WARNING  | Task flagged orphaned (>2 sessions stale)                  | `wrap-up: orphaned task <id>: <subject>`                  |
| INFO     | **Any user feedback during the wrap-up run** (per universal baseline) | `wrap-up: feedback: '<paraphrase>'; phase=<phase>; changed <what>` |
| INFO     | No deploy marker in repo                                   | `wrap-up: no deploy marker in <repo>; skipped`            |
| INFO     | Docs synced or no drift (Phase 1a)                        | `wrap-up: docs synced: <files>` (or `no doc drift`)       |
| WARNING  | Phase 3 tool/infra problem, slowdown, or friction          | `wrap-up: friction: <what>; slowed=<how>`                 |
| WARNING  | Phase 3 re-orientation (Louis redirected a wrong default)  | `wrap-up: reorientation: <what>; root=<missing-doc\|stale-doc\|skill-default\|tool-default>` |
| WARNING  | Phase 3 doc-debt (a doc confidently asserts something false/stale) | `wrap-up: doc-debt: <doc> asserts <falsehood>; found-by=<wrong-conclusion\|footgun\|dead-path>` |
| INFO     | Nothing notable to report (Phase 3)                        | `wrap-up: no friction/reorientation this session`         |
| INFO     | Nothing to improve                                         | `wrap-up: no self-improvement findings`                   |

Log via the `claude-log` helper script — concrete invocations look like:

```
claude-log wrap-up INFO "wrap-up: started"
claude-log wrap-up WARNING "wrap-up: ambiguous memory placement for <topic>; chose <tier>"
claude-log wrap-up CRITICAL "wrap-up: deploy failed in <repo>: <stderr-tail>"
```

# triggers I might have missed: subagent failures during phase execution, partial-session crashes

## Phase 1: Ship It

### 1a. Documentation sync

Runs BEFORE the commit so any doc updates ship in the same commit. Scope is
**documentation this session's own changes made stale**, not a repo-wide audit.

Look at what changed this session (new or renamed scripts, new commands/flags,
changed config keys, moved files, new behavior) and update the docs that
describe it:

1. **README / usage docs** — a command, script, flag, or setup step was added or
   changed → update the matching `README.md` / usage section.
2. **Project `CLAUDE.md`** — a convention, command, or path contributors rely on
   changed → reflect it (durable facts only, not this-session state).
3. **`.env.example` / `.envrc.example`** — new env vars were introduced → add
   them (names only, empty values) so the template stays complete.
4. **Design / runbook docs** the session implemented (e.g. a `plans/*` or
   `docs/plans/*` doc) → update their Status / final-state so the committed doc
   isn't misleading.
5. **`ABOUTME:` headers / docstrings** for files whose behavior changed.

Auto-apply the edits (the commit step stages them). Touch only docs clearly
stale relative to *this session's* changes; leave the rest. If you spot a doc
that is stale but out of scope to fix here, note it in the summary. Log:

```
claude-log wrap-up INFO "wrap-up: docs synced: <files>"      # or: no doc drift from this session's changes
```

### 1b. Commit

Treat this phase as **session-boundary cleanup**, not strict
this-turn scope. If a repo has uncommitted work that's COHERENT
(same area of code as recent commits, would write cleanly as one
commit, looks like an interrupted save), commit it even if it
predates the current turn. The point of automated wrap-up is to
leave the repo shippable across session boundaries. Skip clearly
unrelated clutter — paths dirty for weeks, big binaries, anything
that has no narrative tie to recent commits. When in doubt about
a path, exclude it.

For each repo directory touched during this session:

1. Run `git status --porcelain` in that repo.
2. If output is empty: skip — repo is clean.
3. If non-empty:
   - Inspect the diff (`git diff` and `git diff --cached`) to draft a one-line commit subject summarizing the change.
   - Stage relevant files explicitly (avoid `git add -A` — never commit secrets or unrelated dirty work).
   - Commit on the default branch (`master` for Louis's own repos; check `git symbolic-ref --short HEAD` first).
   - **Commit-message handling:** when the message contains em-dashes / apostrophes / smart-quotes, write to a temp file via the `Write` tool first, then `git commit -F /tmp/msg.txt`. Bash heredoc parsing mangles those characters and has produced wrong commit subjects across multiple sessions.
   - **Do NOT use `--no-verify`.** If a hook fails, treat it as a precondition failure: log `WARNING wrap-up: phase1 hook failed in <repo>: <hook-name>`, leave the commit unmade, and report in the final summary.
4. **Push policy:** push only if (a) the user explicitly asked for pushes during this session, OR (b) the repo's CLAUDE.md frontmatter has `auto-push: true`. Otherwise leave un-pushed and report.
5. **Non-fast-forward handling:** if `git push` fails with `non-fast-forward` / `fetch first` / `Updates were rejected`, the remote has commits the local branch doesn't (e.g. an automated push from another machine landed during the session). Run `git pull --rebase origin <branch>` and retry the push once. If the rebase produces conflicts, abort it (`git rebase --abort`), log a CRITICAL, and surface in the summary — do NOT attempt to resolve conflicts during wrap-up.

If a commit or push fails for non-hook reasons (network error, etc.), log:

```
claude-log wrap-up CRITICAL "wrap-up: phase1 ship failed in <repo>: <reason>"
```

### 1c. File placement check

For each file created or modified during this session:

1. **Naming.** If the project has a CLAUDE.md with naming conventions, check the file matches; otherwise infer from neighbor files (snake_case vs kebab-case). If a violation is found, rename via `git mv`.
2. **Location.** If the file is misplaced (e.g., a test file in `src/`, a doc in the project root), move it to the correct subfolder.
3. **Document files** (.md, .docx, .pdf, .xlsx, .pptx) created at the workspace root or in a code directory: move to `docs/` if a `docs/` folder exists.

On a rename collision (target name already exists), log:

```
claude-log wrap-up WARNING "wrap-up: file rename collision: <from> -> <to>"
```

…and leave the file in place; report in summary.

### 1d. Deploy

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

### 1e. Task cleanup

1. Run TaskList. Read all tasks.
2. For tasks completed during this session but still `pending` or `in_progress`: TaskUpdate to `completed`.
3. For tasks `pending` for ≥2 sessions without progress: mark them as orphaned in the summary. Do NOT auto-delete. Log:

   ```
   claude-log wrap-up WARNING "wrap-up: orphaned task <id>: <subject>"
   ```

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

## Phase 3: Report (friction & re-orientations)

Before self-improvement, surface the raw signals from the session — this phase REPORTS them,
Phase 4 ACTS on them. Review the whole session for anything meaningful worth landing in
`lessons.md`:

- **Tool / infra problems** — a tool errored, behaved unexpectedly, had the wrong default
  mechanism or usage form, hit a permission denial, or an infra step broke (wrong serving
  port, failed deploy, etc.).
- **Slowdowns** — anything that cost extra round-trips: repeated manual steps, a wrong first
  attempt, a dead-end approach, waiting on something avoidable.
- **Re-orientations** — every time Louis had to correct or redirect because the first result
  was not what he meant. Each one is a signal; tag the likely root cause:
  - `missing-doc` — the fact/context existed but was not written down anywhere.
  - `stale-doc` — a doc, skill, memory, or CLAUDE.md **confidently asserted something false
    or outdated** and the session (or a subagent) believed it. The doc exists and lies.
  - `skill-default` — a skill oriented in the wrong direction by default.
  - `tool-default` — a tool had the wrong default mechanism or usage form.
  - `other`.
- **Doc-debt sweep** — beyond re-orientations, scan the session for every point where you
  reached a wrong usage conclusion, hit a footgun, or found instructions that no longer match
  the machine (dangling paths, dead aliases, invocations that fail in agent shells, rules that
  contradict their own tooling). Each is a `stale-doc`/`missing-doc` signal even when Louis
  never had to intervene. Lesson from 2026-07-09: the expensive failures were not missing
  docs but confidently wrong ones — a sweep that only asks "what is undocumented?" catches
  none of them.

For each meaningful item, write one concise `lessons.md` line via `claude-log`:

```
claude-log wrap-up WARNING "wrap-up: friction: <what happened>; slowed=<how>"
claude-log wrap-up WARNING "wrap-up: reorientation: <what Louis redirected>; root=<missing-doc|stale-doc|skill-default|tool-default>"
claude-log wrap-up WARNING "wrap-up: doc-debt: <doc> asserts <falsehood>; found-by=<wrong-conclusion|footgun|dead-path>"
```

Be concrete (name the tool / file / skill). Log anything that recurred or cost a round-trip;
skip one-off typos. If the session was clean, log:

```
claude-log wrap-up INFO "wrap-up: no friction/reorientation this session"
```

These logged items are the **input to Phase 4**: every `skill-default` / `tool-default` /
`missing-doc` re-orientation should map to a concrete self-improvement action there, and every
`stale-doc` / doc-debt item must end with the lying doc **patched** (or, if it lives outside
reach — another repo, another owner — explicitly flagged in the report with its path). Never
just log a stale doc and move on: the log line records the debt, the Phase 4 edit pays it.
List them in the consolidated report under "Phase 3 — Report".

## Phase 4: Review & Apply

Analyze the conversation for self-improvement findings. **Auto-apply all
actionable findings immediately**; do not gate per-finding.

If the session was short or routine with nothing notable, output
"Nothing to improve" in the summary and log:

```
claude-log wrap-up INFO "wrap-up: no self-improvement findings"
```

…then proceed to the final report.

### Finding categories

- **Skill gap** — Claude struggled, got wrong, needed multiple attempts.
- **Friction** — Repeated manual steps, things Louis had to ask explicitly that should have been automatic.
- **Knowledge** — Facts Claude didn't know but should have.
- **Automation** — Repetitive patterns that could become skills, hooks, or scripts.

### Action types

- **CLAUDE.md** — edit relevant project or global CLAUDE.md.
- **Rules** — create or update `<repo>/.claude/rules/<topic>.md`.
- **Auto memory** — append insight to the project's auto-memory.
- **Skill self-improvement** — edit the SKILL.md of any skill that ran in this session if user feedback or caveats during its execution would have led to better behavior. Examples: a missed trigger phrase in the description; an action the skill should have taken automatically but Louis had to ask for; a step that fired in the wrong order; a guard that should have prevented something. Treat user corrections (the universal-baseline WARNING events for the relevant skill) as the primary signal. Edit the SKILL.md inline; commit per the regular Phase 1 flow.
- **New skill / Hook spec** — write a spec to `~/Setup/docs/plans/YYYY-MM-DD-<name>-design.md`. Do NOT auto-build the new skill.
- **CLAUDE.local.md** — create or update per-project local memory.

**Capture the trigger source.** When applying a skill self-improvement, note in the summary what user input prompted it ("Louis said X mid-execution → updated skill Y to do Z automatically"). This makes the audit trail readable and gives Louis a chance to push back if the change misread his feedback.

### Summary format (for the consolidated report)

```
Findings (applied):

1. ✅ Skill gap: <description>
   → [CLAUDE.md] <what was added>

2. ✅ Knowledge: <description>
   → [Rules] <file>

3. ✅ Automation: <description>
   → [Skill spec] <path-to-new-spec.md>

4. ✅ Skill self-improvement: <skill-name>: <trigger from user input>
   → [SKILL.md] <what was edited>

---
No action needed:

5. <description>
   <reason — already documented / out of scope / etc.>
```

## Final consolidated report

After all four phases complete, present this single report as the final
output of the skill:

````
# Wrap-up — YYYY-MM-DD HH:MM

## Phase 1 — Ship It
- Docs: <files updated / no drift>
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

## Phase 3 — Report
Logged to lessons.md (or "nothing notable"):
- friction: <what>; slowed=<how>
- reorientation: <what Louis redirected>; root=<missing-doc|stale-doc|skill-default|tool-default>
- doc-debt: <doc> asserts <falsehood> → patched in Phase 4 (or flagged: <path>)

## Phase 4 — Review & Apply
Applied:
1. <category>: <description> → [<tier>] <action>

No action needed:
2. <description> — <reason>

## Self-observability
<count> entries written to ~/.claude/lessons.md this run.

## Session résumé
<1-3 plain-language lines: what this session was about and where it ended
up. This is ALWAYS the very last thing written in the wrap-up output —
nothing may follow it.>
````

The résumé is for Louis skimming old sessions later: name the subject and
the outcome, not the process (good: "Designed and shipped the secrets
methodology; vault trims still on Louis." bad: "Ran 4 phases and committed
files.").

The `<count>` is the number of `claude-log wrap-up` lines that landed
in `~/.claude/lessons.md` during this run — verify by:

```bash
# Replace YYYY-MM-DD with today's date
grep -c "^$(date +%Y-%m-%d) wrap-up " ~/.claude/lessons.md
```

(Subtract any that were already there before this run started.)

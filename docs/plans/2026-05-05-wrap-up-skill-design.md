# Spec — wrap-up Skill

**Date:** 2026-05-05
**Status:** Approved — ready for implementation plan
**Depends on:** [2026-04-21 skill storage & observability pattern](2026-04-21-skill-storage-observability-design.md)
**Scope:** A user-invocable skill that runs an end-of-session checklist across four phases — ship, remember, review-and-apply, publish — auto-applying actions where confidence is high and presenting one consolidated report at the end. Minimizes Louis's attention time at session close.

## Motivation

Session endings have predictable bookkeeping: commit + push, deploy if applicable, write down what was learned, capture self-improvement findings, and surface anything publishable. Doing this manually is what slips first when Louis is tired or context-switched. The skill formalizes the checklist so nothing gets dropped between sessions.

Louis provided the full functional spec text in the original conversation; this document refines and grounds it in the foundation (observability contract, skill storage, etc.) and pins down the decisions left open by the original draft.

## Non-goals

- **Replacing per-step explicit commands.** `/commit`, `/commit-push-pr`, etc. still exist as targeted tools. Wrap-up is the *every-thing-at-once* catch-all, not a substitute for surgical operations.
- **Cross-machine deploy orchestration.** Deploy step looks at the local repo only.
- **Auto-publishing posts to external platforms.** Drafts are written to disk; posting is always a separate explicit user action.
- **Ad-hoc memory mutations.** The Remember phase only catalogs what the *current session* surfaced. It doesn't scan the project for things to memorize that have nothing to do with the work just done.

## Design

### 1. Trigger surface

User-invocable skill: `/wrap-up`.

Description (frontmatter): `Use when Louis says "wrap up", "close session", "end session", "wrap things up", "close out this task", or invokes /wrap-up. Runs end-of-session checklist for shipping, memory, and self-improvement.`

### 2. Phase structure

Four phases run in order. Each phase is conversational and inline — no separate documents. All phases auto-apply (subject to per-phase rules below); a single consolidated report is presented at the end.

```
Phase 1: Ship It            → commit, file placement, deploy, task cleanup
Phase 2: Remember It        → memory placement (auto-apply with confidence gate)
Phase 3: Review & Apply     → self-improvement findings → applied actions
Phase 4: Publish It         → draft publishable content to ~/Drafts/
```

### 3. Phase 1 — Ship It

**Commit:**
1. For each repo directory touched during the session: run `git status`.
2. If uncommitted changes exist: auto-commit to default branch with a descriptive message based on the diff. Do not skip hooks. (Per Louis's CLAUDE.md.)
3. Push to remote *only* if the user has previously asked for pushes in this session, OR the repo's CLAUDE.md / convention indicates push-on-commit. Otherwise, leave un-pushed and report.

**File placement check:**
4. If any files were created or saved during this session:
   - Verify they follow naming conventions (project-local conventions if a CLAUDE.md exists; otherwise generic snake_case-or-kebab-case based on neighbor files).
   - Auto-fix naming violations by renaming.
   - Verify they're in the correct subfolder per the project's structure.
   - Auto-move misplaced files.
5. If any document-type files (.md, .docx, .pdf, .xlsx, .pptx) were created at the workspace root or in code directories, move them to the project's `docs/` folder if it exists.

**Deploy (per Q1 / A — marker-based detection):**
6. Detect a deploy step by checking, in order:
   - `Makefile` containing a `deploy:` target
   - `scripts/deploy.sh` executable file
   - `bin/deploy` executable file
   - Project-level `CLAUDE.md` containing a `## Deploy` section with a runnable command block
7. First match wins. Run that command. Report stdout/stderr in the summary.
8. If no marker matched: skip silently. **Do not ask about manual deployment.** (Per the original spec.)

**Task cleanup:**
9. Read the current TaskList.
10. For tasks that were completed in this session but still marked `pending` / `in_progress`: mark `completed`.
11. For tasks that have been `pending` for >2 sessions without progress: flag in the summary as orphaned, do not auto-delete.

### 4. Phase 2 — Remember It (per Q3 / B)

Review what was learned during this session. For each piece of knowledge, choose a destination per the framework:

| Tier | Use for | Example |
|---|---|---|
| **Auto memory** (`~/.claude/projects/<repo>/memory/`) | Debugging insights, patterns Claude discovered, project quirks | "this build needs `--no-verify-ssl` for the corp proxy" |
| **CLAUDE.md** (project) | Permanent project rules, conventions, commands, architecture | "tests live in `tests/`, run via `make test`" |
| **`.claude/rules/`** (modular project rules) | Topic-specific instructions scoped to file types via `paths:` frontmatter | "type all functions in `src/api/**/*.ts`" |
| **CLAUDE.local.md** (private per-project) | Personal WIP context, sandbox creds, current focus | "currently debugging the X bug, ignore Y for now" |
| **`@import`** | Cross-reference rather than duplicate | CLAUDE.md `@import .claude/rules/testing.md` |

**Confidence-gated auto-apply:**

For each piece of session knowledge:
- **High confidence** placement (one tier clearly fits per the framework): auto-apply, list in summary as applied.
- **Low confidence** placement (≥2 tiers plausibly fit OR the user's intent in conversation didn't clearly indicate scope): auto-apply *the chosen tier* but flag it in the summary's "review-please" section so Louis can quickly relocate if needed.

Heuristics for "low confidence":
- Could be project-wide OR file-type-scoped (CLAUDE.md vs. rules with paths)
- Could be permanent OR ephemeral (CLAUDE.md vs. CLAUDE.local.md)
- Refers to something cross-cutting (cite in CLAUDE.md? then duplicate in rules?)

### 5. Phase 3 — Review & Apply (auto-apply, with summary)

Analyze the conversation for self-improvement findings. If the session was short or routine with nothing notable, output "Nothing to improve" and proceed to Phase 4.

**Auto-apply all actionable findings immediately**, then present a summary. Do not gate per-finding.

**Finding categories:**
- **Skill gap** — Things Claude struggled with, got wrong, or needed multiple attempts.
- **Friction** — Repeated manual steps, things Louis had to ask for explicitly that should have been automatic.
- **Knowledge** — Facts about projects, preferences, or setup that Claude didn't know but should have.
- **Automation** — Repetitive patterns that could become skills, hooks, or scripts.

**Action types:**
- **CLAUDE.md** — edit relevant project or global CLAUDE.md.
- **Rules** — create or update a `.claude/rules/` file.
- **Auto memory** — save an insight for future sessions.
- **Skill / Hook** — write a spec to `~/Setup/docs/plans/YYYY-MM-DD-<name>-design.md` for later implementation. Do not auto-build the skill itself.
- **CLAUDE.local.md** — create or update per-project local memory.

**Summary format:**

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

### 6. Phase 4 — Publish It (per Q2 / A)

After all other phases complete, review the full conversation for publishable material:

- Interesting technical solutions or debugging stories.
- Community-relevant announcements or updates.
- Educational content (how-tos, tips, lessons learned).
- Project milestones or feature launches.

**If publishable material exists:**

Draft per platform to `~/Drafts/<post-slug>/<platform>.md`. Platforms supported in v1:
- `Reddit.md`
- `Blog.md` (generic; Louis adapts to his actual blog format)

Each platform's draft is tailored: Reddit gets a tldr + full-post structure; Blog gets a long-form treatment with section headings.

Present alongside the consolidated report:

```
All wrap-up steps complete. I also found potential content to publish:

1. "Title of Post" — 1-2 sentence description.
   Platforms drafted: Reddit, Blog
   Drafts: ~/Drafts/title-of-post/

Wait for the user to respond. If they approve a platform, no posting happens
automatically — the user copies the draft and posts manually. (External
platform posting is out of scope for v1.)
```

**Scheduling considerations:**
- If the session produced multiple publishable items, drafts are still all written. The skill notes which is most time-sensitive in the summary.
- Drafts persist; nothing is auto-deleted.

**If nothing publishable:** "Nothing worth publishing from this session." End.

### 7. Consolidated final report

After all four phases, present one report that is the final output:

```
# Wrap-up — YYYY-MM-DD HH:MM

## Phase 1 — Ship It
- Committed: <repos and short subjects>
- Pushed: <repos>
- File placement: <fixes>
- Deploy: <ran X / skipped (no marker)>
- Tasks: <N completed, M flagged orphaned>

## Phase 2 — Remember It
Applied (high-confidence):
- [Auto memory] <summary>
- [CLAUDE.md] <summary>

Review please (low-confidence — applied to <tier>, may want relocation):
- <summary>

## Phase 3 — Review & Apply
Applied:
1. <category>: <description> → [<tier>] <action>
2. ...

No action needed:
3. <description> — <reason>

## Phase 4 — Publish It
- <Title>: drafted at ~/Drafts/<slug>/

## Self-observability log
<count> entries written to ~/.claude/lessons.md this run.
```

### 8. Observability

This skill is observability-enabled (per the contract).

```markdown
## Observability

This skill follows the universal observability baseline (see
docs/plans/2026-04-21-skill-storage-observability-design.md).

**Universal baseline:** CRITICAL on abort, WARNING on correction/fallback/retry/precondition-fail, INFO on edge path.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | Phase 1 commit/push fails (hook failure, network, etc.) | `wrap-up: phase1 ship failed in <repo>: <reason>` |
| CRITICAL | Phase 1 deploy command exits non-zero | `wrap-up: deploy failed in <repo>: <stderr-tail>` |
| WARNING | Low-confidence memory placement (Phase 2) | `wrap-up: ambiguous memory placement for <topic>; chose <tier>` |
| WARNING | Phase 1 file rename collision (target name already exists) | `wrap-up: file rename collision: <from> -> <to>` |
| WARNING | Task flagged orphaned (>2 sessions stale) | `wrap-up: orphaned task <id>: <subject>` |
| INFO | No deploy marker found in repo | `wrap-up: no deploy marker in <repo>; skipped` |
| INFO | Nothing publishable found in session | `wrap-up: no publishable content this session` |
| INFO | Nothing to improve (short/routine session) | `wrap-up: no self-improvement findings` |

Log via: `claude-log wrap-up <LEVEL> "<message>"`

# triggers I might have missed: subagent failures during phase execution, partial-session crashes
```

### 9. Side-effects checklist

After a successful run:

- [ ] All touched repos either committed or explicitly clean
- [ ] Deploy command ran (if marker present) or was reported as skipped
- [ ] Memory tiers updated per the framework
- [ ] CLAUDE.md / rules / specs files updated for self-improvement findings
- [ ] `~/Drafts/<post>/` directory created for any publishable content
- [ ] Final consolidated report printed in conversation
- [ ] Log lines in `~/.claude/lessons.md` for any phase events that match the triggers in §8

## Components to build

- [ ] `~/Setup/skills/wrap-up/SKILL.md` — the skill body following sections 1–8
- [ ] `dotfiles.json` entry for `wrap-up` skill
- [ ] Tests:
  - Phase 1: clean repo (no-op commit, no push, no deploy → INFO log)
  - Phase 1: dirty repo with deploy marker → commits + runs deploy
  - Phase 1: dirty repo without deploy marker → commits, skips deploy silently, INFO log
  - Phase 2: high-confidence memory placement → applied, listed under "Applied"
  - Phase 2: low-confidence placement → applied, listed under "Review please" + WARNING log
  - Phase 3: short routine session → "Nothing to improve" + INFO log
  - Phase 4: no publishable content → INFO log; with publishable content → drafts written
  - Observability self-check: at least one `claude-log wrap-up …` invocation per phase that produced an event

## Open questions (defer to implementation)

- **Scope of "touched repos":** Phase 1 needs to detect which repos were touched. Heuristic: any directory the session ran tools in, OR any directory whose `git status` shows changes that match files edited this session. Verify during implementation that we don't accidentally try to commit unrelated dirty trees.
- **Deploy marker priority on multi-match:** if a repo has both `Makefile` and `scripts/deploy.sh`, the spec says first-match-wins (Makefile). Confirm this is the right priority.
- **Push policy:** "previously asked for pushes" is fuzzy. v1 default: do NOT auto-push unless the user explicitly said so this session OR the repo has `auto-push: true` in its CLAUDE.md frontmatter. Reconsider if this is too conservative.
- **Platform list expansion:** v1 ships Reddit + Blog. Add platforms (HN, Mastodon, X, etc.) when Louis actually posts there. Don't speculate.
- **Long sessions and Phase 3 noise:** very long sessions might produce many findings. v1 lists all of them; if it gets noisy, add a confidence/severity threshold for what makes the summary.

## References

- Foundation: [docs/plans/2026-04-21-skill-storage-observability-design.md](2026-04-21-skill-storage-observability-design.md)
- Audit meta-skill: [docs/plans/2026-05-05-observability-audit-design.md](2026-05-05-observability-audit-design.md)
- Add-dotfile sister skill: [docs/plans/2026-05-05-add-dotfile-skill-design.md](2026-05-05-add-dotfile-skill-design.md)
- Original wrap-up spec text: provided by Louis in the brainstorm conversation 2026-04-21

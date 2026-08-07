---
name: audit-observability
description: Use when Louis says "audit observability", "check observability", "audit my skills", "are my skills observable", "find skills missing observability", or invokes /audit-observability. Scans Louis-authored skills and agents (default user + project, plugins excluded), classifies each against the observability contract, writes one consolidated proposals file with tailored Observability sections for skills missing the contract, and applies approved decisions in batch.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
---

# Observability Audit

Scan, propose, apply. Two phases: autonomous scan + proposal generation; single-attention review of one consolidated proposals file; batch apply.

## Observability

This skill follows the universal observability baseline (see `docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (Claude was about to be wrong), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during the run.** Every distinct user message that conveys preference, redirection, refinement, or commentary MUST be logged. Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change — already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | Cannot read a target SKILL.md / agent file | `audit-observability: <path> unreadable: <reason>` |
| CRITICAL | `--apply` fails to write to a skill file | `audit-observability: apply failed for <skill>: <reason>` |
| WARNING | Skill frontmatter unparseable; treated as needs-attention | `audit-observability: frontmatter parse failed for <path>` |
| WARNING | After apply, skill still doesn't satisfy contract | `audit-observability: apply incomplete for <skill>: <reason>` |
| INFO | Frontmatter `observability: opt-out` or `deferred` set | `audit-observability: <skill> marked as <state>` |
| INFO | Decision other than accept/edit on apply | `audit-observability: <skill> decision=<value>` |
| INFO | Self-improvement edit applied (Phase 5) | `audit-observability: self-improvement: '<paraphrase>'; edited <what>` |

Concrete invocation examples:

```
claude-log audit-observability INFO "audit-observability: starting scan; scope=user+project"
claude-log audit-observability WARNING "audit-observability: frontmatter parse failed for /home/ezalos/.claude/skills/X/SKILL.md"
claude-log audit-observability CRITICAL "audit-observability: apply failed for wrap-up: write permission denied"
```

# triggers I might have missed: scan timeout on huge skill trees, network access in subagent files (none today)

## Invocation

`/audit-observability [flags]`

Flags:
- `--user-only` — restrict scan to `~/.claude/skills/` + `~/.claude/agents/`
- `--project-only` — restrict scan to `<cwd>/.claude/skills/` + `<cwd>/.claude/agents/`
- `--installed` — also scan installed third-party skills in user-space (report-only; no proposals)
- `--plugins` — also scan plugin skills (report-only; no proposals)
- `--verify-only` — skip proposal generation; print report and exit
- `--apply <path>` — read a previously-generated proposals file and apply approved decisions

Default scope (no flags): user + project skills + agents that are **Louis-authored** (symlinks into `~/Setup/dotfiles/`). Installed third-party skills (real directories in `~/.claude/skills/` populated by marketplaces) are excluded by default. Plugins always excluded by default.

If invoked with no flags AND no proposals file argument: run Phase 1 + Phase 2.
If invoked with `--apply <path>`: run Phase 3.
If invoked with `--verify-only`: run Phase 1 only, print report.

Log the chosen scope at start:

```
claude-log audit-observability INFO "audit-observability: starting scan; scope=<flags-resolved>"
```

## Phase 1 — autonomous scan

No user input required during this phase.

### 1a. Enumerate targets

Resolve the scope flags to a list of files. The mapping:

| Scope | Glob | Editability |
|---|---|---|
| user skills (authored) | `~/.claude/skills/*/SKILL.md` where the skill dir is a **symlink** to `~/Setup/dotfiles/...` | editable |
| user skills (installed) | `~/.claude/skills/*/SKILL.md` where the skill dir is a **real directory** | upstream-owned |
| user agents | `~/.claude/agents/*.md` | editable (assume Louis-authored) |
| project skills | `<cwd>/.claude/skills/*/SKILL.md` | editable |
| project agents | `<cwd>/.claude/agents/*.md` | editable |
| plugin skills (only with `--plugins`) | `~/.claude/plugins/*/skills/*/SKILL.md` and `~/.claude/plugins/cache/*/*/skills/*/SKILL.md` | upstream-owned |

**Distinguishing authored from installed in user-space:**

```bash
# A user-space skill is "authored" iff its directory is a symlink:
[ -L ~/.claude/skills/<name> ] && echo authored || echo installed
```

Louis's deployment pattern symlinks every authored skill from `~/Setup/skills/<name>/` to `~/.claude/skills/<name>/` via the single `skills` fan-out entry. Real directories in `~/.claude/skills/` are necessarily third-party-installed (marketplaces, manual copies, etc.; recorded in `skills/EXTERNAL.md`).

Default behavior: scan authored only. Add `--installed` to include installed skills (report-only, no proposals).

Deduplicate symlinks: if two scope-paths resolve to the same target, count once.

If a target is unreadable: log CRITICAL and skip it (don't abort the audit).

### 1b. Classify each target

Read frontmatter + body. Apply these checks in order:

1. **Editability check first.** If the target is upstream-owned (installed third-party in user-space, plugin skill): status = `installed-third-party` (or `plugin`). Run the contract checks below for reporting purposes, but never propose edits — these get listed in a separate report-only section.
2. **Has `observability: opt-out` in frontmatter** → status = `opted-out`. Note the `observability_set` date if present.
3. **Has `observability: deferred` in frontmatter** → status = `deferred`. Note date if present.
4. **Contract checks (all three must pass for `passing`):**
   - Body contains `^## Observability` heading.
   - Body contains at least one `claude-log\s+\S+\s+(INFO|WARNING|CRITICAL)` invocation.
   - The first arg passed to `claude-log` matches the frontmatter `name:` value. If the skill has no `name:` field (some installed skills omit it), fall back to the directory's basename for the match check.
5. Otherwise (and only if editable) → status = `needs-attention`. Capture WHICH bullet(s) failed.

Frontmatter parse failure: log WARNING and treat as `needs-attention` (editable) or `installed-third-party` (upstream) per editability.

### 1c. Tailored proposal (only for `needs-attention`)

For each `needs-attention` skill, generate a proposed `## Observability` section by:

1. **Identifying likely failure points** in the skill body:
   - `Bash` calls to external tools (`curl`, `gh`, `ssh`, `cf`, `make`, `python -m ...`)
   - Retry loops, `until` blocks, fallback branches
   - User-prompt gates (AskUserQuestion calls)
   - Idempotency guards (`if not exists`, `if symlink`, etc.)
   - File writes / edits that could collide
2. **Generating skill-specific triggers** matched to those failure points, with message templates that reference the skill's own data shape.
3. **Always include the universal baseline** verbatim — including the systematic feedback INFO trigger.

Proposal template:

```markdown
## Observability

This skill follows the universal observability baseline (see
`docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction, fallback, retry, precondition-fail.
- INFO (systematic) on any user feedback, suggestion, or caveat during the run.
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| <LEVEL> | <trigger> | <template> |
...

Concrete invocation examples:

```
claude-log <skill-name> INFO "<skill-name>: <example-event>"
```

# triggers I might have missed: <none|list>
```

The `# triggers I might have missed:` line is part of the template — it invites Louis to add domain-specific events the audit didn't anticipate.

## Phase 2 — write the consolidated proposals file

Write `~/.claude/audit-proposals-YYYY-MM-DD.md`. Structure:

```markdown
# Observability Audit — YYYY-MM-DD

## Summary
- Authored: N passing, M needs attention, K opted out, J deferred
- Installed third-party (if --installed): P scanned; Q passing, R failing contract (report-only)
- Plugins (if --plugins): P scanned; Q passing, R failing contract (report-only)
- Lessons log: ~/.claude/lessons.md (current line count: <N>)

## ✅ Passing (authored)
- <skill-a> (<source>) — last reviewed <date if available>
- <skill-b> (<source>)

## ⏸ Opted out / Deferred
- <skill-c> (<source>) — opt-out, set <date>
- <skill-d> (<source>) — deferred, set <date>

## 📦 Installed third-party (report-only, --installed flag required to display)
- <skill-e> — passing
- <skill-f> — failing: <bullets>

## 📦 Plugins (report-only, --plugins flag)
- <skill-g> (plugin: <name>) — passing
- <skill-h> (plugin: <name>) — failing: <bullets>

## ❌ Needs attention — review each block, edit "decision:" line, save

### 1. <skill-name> (<source>)
**Path:** /full/path/to/SKILL.md
**Missing:** <list of contract bullets failing>
**Identified failure points:**
- <observation 1>
- <observation 2>

decision: accept    # one of: accept | edit | skip | opt-out | defer
proposed: |
  <full proposed ## Observability section here, indented one level
  so YAML treats it as a literal block scalar>

### 2. <skill-name> (<source>)
...
```

After writing, print to conversation:

> Audit complete. **N skills need attention.** Review proposals at `~/.claude/audit-proposals-YYYY-MM-DD.md`. When ready, run `/audit-observability --apply ~/.claude/audit-proposals-YYYY-MM-DD.md` (or just say "apply").

Then end the turn — do not proceed to apply automatically. Louis reviews the file, edits decisions, then triggers Phase 3.

## Phase 3 — apply

Triggered by `/audit-observability --apply <path>` or by Louis saying "apply" while the audit is still in conversation context.

### 3a. Parse the proposals file

For each block in the `## ❌ Needs attention` section, extract:
- Skill path (from `**Path:**` line)
- `decision:` value (must be one of: `accept`, `edit`, `skip`, `opt-out`, `defer`)
- `proposed:` block content (literal block scalar)

If `decision:` is missing or invalid: skip that block, record as error to print later.

### 3b. Apply each decision

| Decision | Action |
|---|---|
| `accept` | Insert the `proposed:` block into the skill's SKILL.md. Position: after frontmatter (after the second `---`), before the next top-level heading (`^# ` or first `^## `). Do not modify frontmatter. |
| `edit` | Functionally identical to `accept`. Use Louis's edited `proposed:` block. |
| `skip` | No-op. |
| `opt-out` | Add `observability: opt-out` and `observability_set: <today's date>` to frontmatter. Do not insert any Observability section. |
| `defer` | Add `observability: deferred` and `observability_set: <today's date>` to frontmatter. Do not insert any Observability section. |

For `accept` / `edit` / `opt-out` / `defer`: log INFO `audit-observability: <skill> decision=<value>`.

If a write fails: log CRITICAL and continue with remaining blocks. Don't abort on one failure.

### 3c. Verification pass

For each skill where `decision: accept` or `edit` was applied: re-run the contract check (Phase 1b step 3). If the skill still fails: log WARNING `audit-observability: apply incomplete for <skill>: <reason>`. List in summary as "applied but still failing".

### 3d. Final summary

Print:

```
# Audit apply — YYYY-MM-DD

Applied: <N> (accept/edit)
Opted-out: <N>
Deferred: <N>
Skipped: <N>
Errors: <N>
Applied but still failing: <N>  (re-run audit to see why)
```

## Phase 4 — verify-only mode

When invoked with `--verify-only`: do Phase 1 only. Print the report (Phase 2's structure, but without the `❌ Needs attention` proposals — just the missing-bullets list). Don't write a proposals file. Don't enter Phase 2 or 3.

## Phase 5 — self-improvement

After the final summary in Phase 3 (or after the report in Phase 4), review user feedback from this audit invocation against THIS skill's own SKILL.md. If user input would have produced a better audit (a missed scope, confusing prompt, heuristic that fired wrong, trigger phrase that should have caught a request that missed), edit `~/.claude/skills/audit-observability/SKILL.md` inline.

Treat WARNING-level corrections logged this run as the primary signal; INFO feedback as secondary signal. Capture *what user input prompted the change* in a one-line note in the final summary.

```
claude-log audit-observability INFO "audit-observability: self-improvement: '<paraphrase>'; edited <what>"
```

If no user input warrants a change: log INFO `audit-observability: no self-improvement findings this run`.

## Constraints

- **Don't edit plugin-installed skills** even if `--plugins` was passed. Plugin scope is report-only.
- **Don't auto-apply.** Phase 2 ends; Louis triggers Phase 3 explicitly.
- **Don't gate on each skill in Phase 2.** All proposals go into one file; Louis reviews once.
- **Don't pollute `~/.claude/lessons.md` with non-events.** The audit's own log lines (per the triggers above) are real events; status messages like "starting scan" are fine, but per-skill "checked X" noise is not.
- **Date handling**: use `date +%Y-%m-%d` for filenames and frontmatter dates. Never `date +%D` (BSD/GNU differ).
- **Keep proposals files dated and persistent**: don't auto-delete old proposal files. Louis can prune manually.

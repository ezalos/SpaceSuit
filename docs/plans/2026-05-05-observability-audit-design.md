# Spec — Observability Audit Meta-Skill

**Date:** 2026-05-05
**Status:** Approved — ready for implementation plan
**Depends on:** [2026-04-21 skill storage & observability pattern](2026-04-21-skill-storage-observability-design.md)
**Scope:** A user-invocable meta-skill that scans Louis's authored skills and agents, reports observability coverage, proposes tailored `## Observability` sections for any skill missing the contract, and applies approved edits in one batch — minimizing Louis's attention time.

## Motivation

Piece 1 defined the observability contract but nothing enforces it. Without an audit, skills will drift: new skills won't add observability, existing skills' `claude-log` calls will rot when renamed, the universal baseline will be implemented inconsistently. The audit is the *checking mechanism* that makes the contract real.

The interaction model is shaped by Louis's stated goal: **minimize attention time**. Inline per-skill prompting interrupts; batch upfront with a single review gate is better.

## Non-goals

- Auditing upstream-owned skills by default. Two flavors: plugin skills (in `~/.claude/plugins/`) and installed third-party skills (real directories in `~/.claude/skills/` populated by marketplaces). Both are upstream-owned; Louis can't usefully edit them. Distinguished from authored skills (which are symlinks into `~/Setup/dotfiles/`) at scan time. Opt-in via `--plugins` and `--installed` flags respectively, both report-only.
- Detecting observability in *inline* subagent prompts (where a skill's body contains the subagent task text directly, not a reference to a named agent file). Detection requires understanding skill execution flow; defer to v2.
- Verifying that logged events actually fire at runtime. The audit is static — contract presence, not runtime behavior. A separate "is the logging actually happening?" tool would be a different skill.
- Backporting opt-out tracking from prior runs. v1 starts fresh: opt-outs are written into skill frontmatter going forward, not migrated from any external state.

## Design

### 1. Invocation

User-invocable skill: `/audit-observability [flags]`

Flags:
- `--user-only` — restrict scan to `~/.claude/skills/` + `~/.claude/agents/`
- `--project-only` — restrict scan to `<cwd>/.claude/skills/` + `<cwd>/.claude/agents/`
- `--installed` — also scan installed third-party skills in `~/.claude/skills/` (real directories, not symlinks); report-only, no edits proposed
- `--plugins` — also scan `~/.claude/plugins/.../skills/` (report-only, no edits proposed)
- `--verify-only` — skip proposal generation; just print the report
- `--apply <path-to-proposals-file>` — read a previously-generated proposals file and apply approved decisions

Default scope (no flags): user + project skills + agents. Plugins excluded.

### 2. Two-phase flow

**Phase 1 — autonomous scan (no Louis input):**

1. Enumerate target SKILL.md / agent .md files per scope.
2. For each, parse frontmatter and body. Apply the contract from piece 1 §4:
   - Has `## Observability` section?
   - At least one `claude-log ` invocation in body?
   - Skill-name argument matches frontmatter `name`?
   - Has frontmatter `observability: opt-out` or `observability: deferred`? (Bypass — list as opted-out / deferred, do not propose.)
3. Classify each skill as: `passing`, `opted-out`, `deferred`, or `needs-attention`.
4. For each `needs-attention` skill: read body, identify likely failure points (Bash calls that may fail, external API touches, fallback branches, user-prompt steps), and generate a tailored `## Observability` proposal.
5. Self-observe: any parse failures, ambiguous frontmatter, or unreadable files get logged via `claude-log audit-observability ...` per the universal baseline.

**Phase 2 — single consolidated review:**

Audit writes one file: `~/.claude/audit-proposals-YYYY-MM-DD.md`. Structure:

```markdown
# Observability Audit — YYYY-MM-DD

## Summary
- N passing, M needs attention, K opted out, J deferred, P plugins (if --plugins)
- Lessons log: ~/.claude/lessons.md (line count: ...)

## ✅ Passing
- skill-a (user) — last reviewed YYYY-MM-DD (if opt-out/defer markers carry a date)
- skill-b (project)

## ⏸ Opted out / Deferred
- skill-c (user) — opt-out, set YYYY-MM-DD
- skill-d (user) — deferred, set YYYY-MM-DD

## ❌ Needs attention — review each block, edit "decision:" line, save

### 1. <skill-name> (<source>)
**Path:** /full/path/to/SKILL.md
**Missing:** <list of contract bullets failing>
**Identified failure points:**
- <observation 1>
- <observation 2>

decision: accept    # one of: accept | edit | skip | opt-out | defer
proposed: |
  ## Observability

  This skill follows the universal observability baseline (see
  docs/plans/2026-04-21-skill-storage-observability-design.md).

  **Universal baseline:** CRITICAL on abort; WARNING on correction/fallback/retry/precondition-fail; INFO (systematic) on any user feedback during the run; INFO on edge path. (See foundation §4.)

  **Skill-specific triggers:**

  | Level | Trigger | Message template |
  |---|---|---|
  | <LEVEL> | <trigger> | <template> |

  Log via: `claude-log <skill-name> <LEVEL> "<message>"`

### 2. ...
```

Then audit prints, in conversation:
> Audit complete. **N skills need attention.** Review proposals at `~/.claude/audit-proposals-YYYY-MM-DD.md`. When ready, run `/audit-observability --apply ~/.claude/audit-proposals-YYYY-MM-DD.md` (or just say "apply").

### 3. Decision semantics (per skill block)

The `decision:` line in each block is the only thing Louis must edit. He may also edit the `proposed:` block to refine the section before applying.

| Decision | Effect on apply |
|---|---|
| `accept` | Insert the `proposed:` block into the skill's SKILL.md (after the frontmatter, before the next top-level section). Add no frontmatter flag. Skill is now passing. |
| `edit` | Same as accept, but apply uses Louis's edited `proposed:` block (audit makes no assumption that he's marked it differently — `accept` and `edit` are functionally identical at apply time; the distinction is documentation of "I changed it"). |
| `skip` | Do nothing. Skill remains in `needs-attention` next run. |
| `opt-out` | Write `observability: opt-out` to skill frontmatter (with `observability_set: YYYY-MM-DD` for traceability). Skill listed under "Opted out" next run. No `## Observability` section added. |
| `defer` | Write `observability: deferred` to skill frontmatter (with date). Listed under "Deferred" next run; not re-proposed until flag removed. |

If `decision:` is missing or invalid: apply fails for that block, audit reports which blocks couldn't be applied and why. Other valid blocks still apply.

### 4. Tailored proposal generation

When generating each proposal, the audit reads the skill body and surfaces:
- **Failure points** the skill mentions or implies — calls to external APIs (`curl`, `gh`, `sfr`, `cloudflare`), retries, precondition checks, user-confirmation gates.
- **Skill-specific message templates** referencing the skill's own data shape (e.g. `add-dotfile` uses `<alias>` and `<device>`; `open-local-port` uses `<port>` and `<service>`).
- **Universal baseline** is always included verbatim, including the systematic-feedback INFO trigger (foundation §4).
- **Free-text "anything else?" hint** appended at the end of every proposal: a comment line `# triggers I might have missed: <none|list>` so Louis can add domain-specific events the audit didn't anticipate.

The proposal is a *suggestion*, not a contract — Louis's edits override.

### 5. Apply phase

When invoked with `--apply <file>` (or "apply" in conversation when audit is fresh):

1. Re-read the proposals file.
2. For each block, read the `decision:` line and execute per §3.
3. Verification pass: re-run the contract check on edited skills. Skills that don't now satisfy the contract get listed as "applied but still failing" with the specific reason — Louis sees this immediately.
4. Print final summary: applied count, opt-out count, defer count, skip count, errors.
5. Self-observe: CRITICAL on apply-edit failure, WARNING on "applied but still failing", INFO on opt-out/defer setting.

### 6. Self-observability

The audit skill itself is observability-enabled (eats own dog food).

**Self-improvement capture (Phase 5):** at the end of every audit run, before printing the final summary, the audit reviews any user feedback or caveats from the current invocation against its OWN SKILL.md. If user input during the audit run would have produced a better audit (a missed scope, a confusing prompt, a heuristic that fired wrong, a trigger phrase that should have caught something it missed), the audit edits its own SKILL.md inline and lists the change in the summary under a `Self-improvement` section. Treat user corrections logged as WARNING via the universal baseline as the primary signal. This mirrors Phase 3 of the wrap-up skill but scoped to this single skill's own behavior — keeps the audit's quality drifting *upward* rather than calcifying.

The audit's universal baseline already logs WARNING on user corrections; the self-improvement step turns that signal into action without waiting for a wrap-up run to do it.

| Level | Trigger | Message |
|---|---|---|
| CRITICAL | Cannot read a target SKILL.md / agent file | `<path> unreadable: <reason>` |
| CRITICAL | `--apply` fails to write to a skill file | `apply failed for <skill>: <reason>` |
| WARNING | Skill frontmatter unparseable; treated as needs-attention | `frontmatter parse failed for <path>` |
| WARNING | After apply, skill still doesn't satisfy contract | `apply incomplete for <skill>: <reason>` |
| INFO | Frontmatter `observability: opt-out` or `deferred` set | `<skill> marked as <state>` |
| INFO | Universal-baseline path: any decision other than accept/edit | `<skill> decision=<value>` |

### 7. Idempotency / re-run behavior

- Skills with `observability: opt-out` or `observability: deferred` in frontmatter (set by a previous run) are listed in their respective sections, never re-proposed.
- To re-prompt for an opted-out skill, Louis removes the frontmatter line manually.
- The proposals file is dated; multiple files can coexist. Apply takes a path argument so Louis can apply an old proposal file if needed.
- `--verify-only` produces the report without writing a proposals file. Useful as a quick sanity check.

### 8. Format and detection rules (concrete)

**Contract checks (all must pass for "passing"):**

```bash
# 1. Has Observability section
grep -q '^## Observability' "$SKILL_MD"

# 2. Has at least one claude-log invocation
grep -qE 'claude-log\s+\S+\s+(INFO|WARNING|CRITICAL)' "$SKILL_MD"

# 3. Skill-name argument matches frontmatter name
NAME=$(awk '/^---$/{f=!f;next} f && /^name:/{print $2; exit}' "$SKILL_MD")
grep -qE "claude-log\s+$NAME\s+" "$SKILL_MD"
```

**Frontmatter flags (recognized):**
- `observability: opt-out` (with optional `observability_set: YYYY-MM-DD`)
- `observability: deferred` (with optional `observability_set: YYYY-MM-DD`)

## Components to build

- [ ] `~/Setup/skills/audit-observability/SKILL.md` — the meta-skill itself
- [ ] dotfiles.json entry for `audit-observability` skill
- [ ] Proposals file template (embedded in SKILL.md or as a shipped reference file)
- [ ] Helper script (optional) `~/Setup/bin/audit-observability-scan` — does the static scan portion in pure bash, returns JSON the skill consumes. (Decide during implementation: bash-only might be too clunky for tailored proposal generation; the skill body in markdown may be enough.)
- [ ] Tests:
  - Skill with observability passes contract check
  - Skill missing section fails contract check
  - Skill with mismatched name in `claude-log` fails contract check
  - opt-out frontmatter excludes from proposals
  - Apply writes correct edits and re-verification passes
  - Self-observability: at least one `claude-log` call in this skill's own SKILL.md

## Open questions (defer to implementation)

- **Section insertion point:** where exactly in SKILL.md does the new `## Observability` section go? Last top-level section (after all existing content), or at a canonical position? Going with **last top-level section** by default unless a sentinel exists — least disruptive to existing skill structure.
- **Proposal file pruning:** old `audit-proposals-*.md` files accumulate. v1 leaves them; revisit if it becomes noise.
- **Pure-bash scan vs in-skill markdown analysis:** for v1, all scanning and proposal generation is done in-skill (the SKILL.md instructs Claude to read files, analyze, write proposals). A separate scanner script is an optimization for later if it proves useful.

## References

- Foundation: [docs/plans/2026-04-21-skill-storage-observability-design.md](2026-04-21-skill-storage-observability-design.md)
- Contract checks reference §4, §6 of the foundation spec
- Subagent rule (§5 of foundation): also applies if this skill ever dispatches subagents

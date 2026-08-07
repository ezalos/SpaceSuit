# Spec — Skill Storage & Observability Pattern

**Date:** 2026-04-21
**Status:** Approved — ready for implementation plan
**Scope:** Foundation for computer-wide authored skills. Defines *where* skills live, *how* they deploy, and the *observability contract* that enables the audit meta-skill and the wrap-up/add-dotfile/open-port skills.

## Motivation

Louis wants to author skills that are general to his computer (not repo-scoped), version them in `~/Setup`, and have them observable so long-running friction becomes visible. Today:

- `~/.claude/skills/` contains only third-party installed skills. Nothing Louis-authored is version-controlled.
- No shared convention exists for how a skill should report errors or friction. Each skill would reinvent it.
- The existing dotfile system (`src_dotfiles/`, `dotfiles.json`) and the existing declarative infrastructure (Cloudflare DNS, NAT manager) are good primitives — the new pieces should compose with them, not duplicate them.

This spec establishes the shared foundation that later specs (audit meta-skill, add-dotfile, open-port, wrap-up) rely on.

## Non-goals

- Versioning or syncing third-party skills already installed in `~/.claude/skills/` (cite, research, hegelian, ablation, etc.). Those remain managed by their respective plugins/marketplaces.
- Replacing the per-project `tasks/lessons.md` convention from global CLAUDE.md §3. That file keeps its purpose.
- Log rotation, shipping logs off-box, or structured analytics. `~/.claude/lessons.md` is append-only plain text until it becomes a problem.

## Design

### 1. Storage & deploy

**Amended 2026-05-05** — adopts the pattern Louis already established for `claude_skill_open_local_port`, `claude_skill_link_develle_domain`, `claude_skill_share_file`. Skills are co-located with other dotfiles under `~/Setup/dotfiles/`, not in a parallel `~/Setup/skills/` tree.

Authored skills live in `~/Setup/dotfiles/claude_skill_<snake_name>/`, git-tracked. Each skill is a directory containing at minimum a `SKILL.md` (and any helper scripts / references the skill needs). Snake-case in the source dir (matches dotfile entry alias), kebab-case in the deploy path (matches Claude's skill conventions).

Each authored skill gets **one entry** in `dotfiles/dotfiles.json`:

```json
"claude_skill_<snake_name>": {
  "alias": "claude_skill_<snake_name>",
  "main": "dotfiles/claude_skill_<snake_name>",
  "deploy": {
    "<device-identifier>": {
      "deploy_path": "/home/<user>/.claude/skills/<kebab-name>",
      "backups": []
    }
  },
  "only_devices": ["<device-identifier>"],
  "variants": null
}
```

The existing `src_dotfiles/DotFile.py` deployer handles directories fine (`shutil.rmtree` branch when overwriting, `os.symlink` to target). Symlink means edits in `~/Setup/dotfiles/claude_skill_<name>/` propagate to `~/.claude/skills/<kebab-name>/` with no redeploy step.

**`only_devices` defaults to current device only.** Most authored skills are tied to specific machine state (home network, local services); deploying everywhere by default would push them onto laptops where they're irrelevant. To make a skill global, set `only_devices: null`.

### 2. Logging files — two files, different semantics

Louis's global CLAUDE.md §3 already mandates a per-project `tasks/lessons.md` for Claude-authored prose rules. That file is unchanged.

This spec introduces a **separate global** log:

| File | Purpose | Format | Audience |
|---|---|---|---|
| `tasks/lessons.md` (per-project) | Claude-authored prose rules from user corrections. Read at session start. | Prose | Claude |
| `~/.claude/lessons.md` (global) | Skill observability log: every error / friction / long-term-concern. | `YYYY-MM-DD skill-name [LEVEL] : explanation` | Louis + audit meta-skill |

No rotation. File is append-only. Revisit if it exceeds a size that's painful to grep (not expected short-term).

### 3. Helper script `claude-log`

**Location:** `~/Setup/dotfiles/bin/claude-log` (deployed via `dotfiles.json` like any other dotfile — one entry with `main: "dotfiles/bin/claude-log"`, symlinked to `~/.local/bin/claude-log` or similar PATH location). Co-locating supporting binaries under `dotfiles/` keeps the "everything we deploy lives under dotfiles/" invariant.

**Signature:**
```
claude-log <skill-name> <LEVEL> "<message>"
```

Where `<LEVEL>` is one of `INFO`, `WARNING`, `CRITICAL`. Unknown levels exit with error.

**Output:** appends one line to `~/.claude/lessons.md`:
```
YYYY-MM-DD skill-name [LEVEL] : message
```

**Cross-platform concerns** (per Louis's `feedback_cross_platform_shell` memory):
- `date +"%Y-%m-%d"` — portable across BSD and GNU.
- **No `flock`** — not available on macOS without Homebrew. Use a `mkdir`-based lock (`mkdir $LOCKDIR` is atomic on all POSIX fs; trap removes on exit) for atomic append under concurrent subagent logging.
- No `readlink -f`, no `stat -c`, no GNU-only `sed -i`.

**Error handling:**
- Missing args → print usage, exit 2.
- Unknown level → print error, exit 2.
- Lock acquisition timeout (e.g. 5s) → print warning to stderr, try best-effort append anyway. Never lose a log line silently.

**Optional ergonomic wrapper:** a zsh function `claude-log()` in `.zshrc` may shadow the script for tab-completion / aliasing purposes. Skills MUST NOT depend on the function being defined — they call the script on PATH.

### 4. Observability contract

A skill is **observability-enabled** iff its `SKILL.md` satisfies all three:

1. **Has an `## Observability` section** that explicitly acknowledges the universal baseline and lists skill-specific triggers.

   The universal baseline every observability-enabled skill commits to:

   | Level | Universal trigger |
   |---|---|
   | `CRITICAL` | Skill aborted / errored out / required user escape to recover |
   | `WARNING` | User correction mid-skill (Claude was about to produce wrong result); fallback path taken; retry needed; precondition not met |
   | `INFO` (systematic) | **Any user feedback, suggestion, or caveat during skill execution.** No judgment call about whether it's "noteworthy" — every distinct user message that conveys preference, redirection, refinement, or commentary MUST be logged. This is the high-value signal: it's where future proactive automation comes from. |
   | `INFO` | Edge-case code path hit; surprising-but-handled condition |

   **On user-feedback capture:** the `WARNING` (correction) and `INFO` (feedback) triggers are deliberately separate. A correction means Claude would have produced a wrong result without the user's intervention; feedback may be Claude was on track and the user is enriching, refining, or noting future-relevant context. Both are signal; only the former is friction. The message format for feedback INFO should capture: (1) one-line paraphrase of what the user said, (2) the skill phase / step where it landed, (3) what the skill changed in response (or "no change — already on track"). Example:

   ```
   claude-log wrap-up INFO "feedback: 'minimize attention time'; phase=design Q3; changed default to confidence-gated B"
   ```

   Skill-specific triggers are listed as a table beneath the baseline, e.g.:

   ```markdown
   ## Observability

   This skill follows the universal observability baseline (see
   `plans/2026_04_21-spec_skill_storage_observability.md`).

   **Universal baseline:** CRITICAL on abort, WARNING on correction/fallback/retry/precondition-fail, INFO on edge path.

   **Skill-specific triggers:**

   | Level | Trigger | Message template |
   |---|---|---|
   | WARNING | Cloudflare DNS sync fails after retry | `cloudflare sync failed for <record>: <reason>` |
   | INFO | No existing file at deploy path (first-time deploy) | `fresh deploy for <alias> on <device>` |

   Log via: `claude-log <skill-name> <LEVEL> "<message>"`
   ```

2. **At least one `claude-log` invocation** in the skill's instruction body (grep-detectable).

3. **Skill-name argument matches the frontmatter `name`** field. Prevents drift when a skill is renamed.

### 5. Subagent rule

When a skill dispatches a subagent (via Task tool) and that subagent needs to log:

- Use the **parent skill's name** as the first argument to `claude-log`, not the subagent's name.
- Prefix the message with `subagent=<agent-name> ` to preserve attribution.

Rationale: the log is threaded by skill invocation, not by internal structure. Louis scanning `~/.claude/lessons.md` cares "which of my skills hit friction", not "which subagent inside it did". The message prefix keeps the detail findable.

Example:
```
claude-log open-port WARNING "subagent=nat-config cloudflare API returned 429, retrying"
```

### 6. Audit detection rules (hands off to piece 2)

The audit meta-skill (next spec) considers a skill observability-enabled iff all three contract bullets in §4 hold. Detection is mechanical:

- `grep -l '^## Observability' <skill>/SKILL.md`
- `grep -c 'claude-log ' <skill>/SKILL.md` ≥ 1
- The string following `claude-log ` (first arg) matches the `name:` frontmatter value

Anything else is **not** observability-enabled and gets flagged in the audit report with a suggested add.

## Components to build

- [ ] `~/Setup/skills/` directory (empty initially; populated by later specs)
- [ ] `~/Setup/bin/` directory (created; will host `claude-log`)
- [ ] `~/Setup/bin/claude-log` script (portable bash, mkdir-lock, level validation)
- [ ] `dotfiles.json` entry for `claude-log` → PATH target
- [ ] A convention section in `~/Setup/README.md` or `~/Setup/skills/README.md` pointing to this spec
- [ ] Test: run `claude-log test-skill INFO "hello"`, verify one line appended in correct format

## Open questions (defer to implementation)

- **PATH target for `claude-log`:** `~/.local/bin/claude-log` is the conventional user-bin on Linux; `/usr/local/bin` would need sudo and is system-wide. `~/.local/bin` on PATH is the safe default. Confirm during implementation plan.
- **Log file creation:** if `~/.claude/lessons.md` doesn't exist, `claude-log` should create it (with a comment header?) rather than fail. Decide during implementation.
- **Rejection of unknown severity**: hard fail or coerce to INFO? Hard fail is simpler and catches typos; going with hard fail unless there's a reason not to.

## Dependencies downstream

- **Piece 2 (observability-audit meta-skill)** uses §4 and §6 directly. Cannot proceed without this.
- **Piece 3 (add-dotfile skill)** uses §1 (storage/deploy) and §4 (observability contract).
- **Piece 4 (open-port skill)** uses §1, §4, and §5 (subagent rule, since it may dispatch per-subsystem work).
- **Piece 5 (wrap-up skill)** uses §4 (it's a Louis-authored skill and should be observability-enabled).

## References

- Existing dotfile system: `~/Setup/src_dotfiles/DotFile.py`, `~/Setup/dotfiles/dotfiles.json`
- Existing infrastructure primitives: `~/Setup/cloudflare-dns/dns.sh`, `~/Setup/nat_manager/nat.py`
- Cross-platform constraints: memory `feedback_cross_platform_shell`
- Zsh gotcha: never `local path` in functions (memory note) — applies if the zsh wrapper is added
- Global CLAUDE.md §3: per-project `tasks/lessons.md` convention (coexists, not replaced)

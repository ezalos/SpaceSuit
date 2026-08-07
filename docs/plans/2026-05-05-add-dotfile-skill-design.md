# Spec — add-dotfile Skill

**Date:** 2026-05-05
**Status:** Approved — ready for implementation plan
**Depends on:** [2026-04-21 skill storage & observability pattern](2026-04-21-skill-storage-observability-design.md)
**Scope:** A user-invocable skill wrapping the existing `src_dotfiles` system that handles two intents in one entry point: (1) start tracking a file as a dotfile and (2) deploy a tracked dotfile on the current machine.

## Motivation

Adding a new dotfile today requires Louis to manually edit `~/Setup/dotfiles/dotfiles.json`, copy the file, and run the deployer — repeatable but friction-laden. Setting up dotfiles on a new machine has the same friction. A skill collapses both flows to a single utterance.

The skill is the first concrete example of the foundation from piece 1: lives in `~/Setup/skills/add-dotfile/`, deployed via `dotfiles.json` (eat-your-own-dogfood), observability-enabled per the contract.

## Non-goals

- **Removing dotfiles from tracking, listing tracked dotfiles, editing existing entries.** These are rare ad-hoc actions; running `python -m src_dotfiles` directly handles them. YAGNI on wrapping the entire CLI.
- **Per-device variants and `only_devices` on initial add.** These are rare configurations Louis sets manually when needed. The skill always starts with the simple case.
- **Cross-device deployment from one machine.** Deploy only happens on the machine the skill runs on. Other devices get deployed-on-arrival when Louis next runs the skill there.
- **Authoring or wrapping the deployer logic itself.** The skill calls into existing `src_dotfiles` Python entry point. If a CLI flag is missing, fix the CLI, not the skill.

## Design

### 1. Trigger surface

Single user-invocable skill: `/add-dotfile` (or natural-language trigger via description).

Description (frontmatter): "Use when Louis asks to track a file as a dotfile, OR to set up a tracked dotfile on the current machine. Wraps the dotfiles.json registry and `src_dotfiles` deployer. Examples: 'add ~/.tmux.conf to my dotfiles', 'deploy my dotfiles here', 'I'm on a new machine, sync up'."

The skill description is the entry point — Claude routes utterances about either intent to this single skill.

### 2. Intent inference (world-state branching)

When invoked, the skill checks current world state to decide the path:

```
Inputs:
- target_path: the path provided by Louis (e.g. "~/.tmux.conf")
- target_alias: the candidate alias (e.g. ".tmux.conf")
- current_device: the active device identifier from src_dotfiles config
- registry: contents of ~/Setup/dotfiles/dotfiles.json

State                                                 → Path
──────────────────────────────────────────────────────────────────
target file exists, alias NOT in registry             → ADD
target file path-equivalent file already a symlink    → already deployed; report no-op
alias IN registry, NO deploy block for current_device → DEPLOY-HERE
alias IN registry, deploy block EXISTS for device, no symlink at deploy_path → REDEPLOY (offer)
ambiguous (e.g. alias in registry but target path differs from main) → ASK
```

Most invocations are unambiguous. The skill names the inferred path explicitly before executing ("I'll add this as a new dotfile entry…" / "I see this is already tracked but not deployed here — I'll deploy it now").

### 3. ADD path

When state classifies as ADD:

1. **Resolve alias** (Q4):
   - Auto-derive: filename basename. Examples: `~/.zshrc` → `.zshrc`, `~/.tmux.conf` → `.tmux.conf`, `~/.config/nvim/init.lua` → `init.lua`.
   - **Collision check:** if derived alias already exists in registry, prompt Louis with the existing entry's main path and ask for an override alias (suggest a path-disambiguated form, e.g. `nvim/init.lua`).
2. **Compute main path**: `dotfiles/<alias>` relative to `~/Setup/`. Move (or copy + verify) the file from `target_path` to `~/Setup/dotfiles/<alias>`.
3. **Create the entry** in `dotfiles.json`:
   ```json
   "<alias>": {
     "alias": "<alias>",
     "main": "dotfiles/<alias>",
     "deploy": {
       "<current_device>": {
         "deploy_path": "<absolute target_path>",
         "backups": []
       }
     },
     "only_devices": null,
     "variants": null
   }
   ```
4. **Deploy on current device** (and only the current device) by invoking `src_dotfiles`'s `add_file()` flow (which backs up, copies-as-main, deploys symlink). The Python entry point already does this end-to-end — the skill calls it, doesn't re-implement.
5. **Verify**: the target_path is now a symlink pointing into `~/Setup/dotfiles/<alias>`.
6. **Commit**: `git add dotfiles/dotfiles.json dotfiles/<alias>` and create a commit `dotfiles: track <alias>` in `~/Setup`. (Per Louis's CLAUDE.md: "On Louis's own repos: commit directly to default branch.")

### 4. DEPLOY-HERE path

When state classifies as DEPLOY-HERE (entry exists, no deploy block for current device):

1. **Show the existing entry** to Louis (alias, main path, devices already deployed).
2. **Prompt for `deploy_path`** on the current device. Default proposal: the path on whichever device looks most similar to current (same OS, same user-home pattern). Louis can accept or override.
3. **Add the deploy block** to `dotfiles.json`:
   ```json
   "<current_device>": {
     "deploy_path": "<provided>",
     "backups": []
   }
   ```
4. **Deploy** via `src_dotfiles` (uses existing main file as symlink target).
5. **Verify** + **commit**: `dotfiles: deploy <alias> on <current_device>`.

### 5. REDEPLOY path

Entry exists, deploy block for current device exists, but target_path is not a symlink (or doesn't exist). Likely cause: device freshly reinstalled, file restored from backup, etc.

1. Confirm with Louis ("entry exists for this device but symlink is missing — re-deploy?").
2. On confirm, run `src_dotfiles`' deploy step (will back up the existing target file if any, then symlink).
3. No `dotfiles.json` change. No commit.

### 6. ASK path

When state is ambiguous (e.g. target file path differs from the recorded `deploy_path`, or two registry entries reference the file):

- Stop. Print the ambiguity. Hand control back to Louis with the relevant context. Don't guess.
- This is itself an observability event (WARNING — see §8).

### 7. Error handling

- **Target file does not exist on ADD** → CRITICAL log + abort. Probably a typo.
- **`~/Setup` not a git repo / dotfiles.json missing** → CRITICAL log + abort. Setup is broken.
- **`src_dotfiles` deployer raises** → CRITICAL log + report stderr verbatim + abort. Don't try to recover — the deployer's job is to handle its own backups/idempotency.
- **Symlink already correct (target → main)** → INFO log "no-op: already deployed".

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
| CRITICAL | target_path doesn't exist on ADD | `add-dotfile: target <path> does not exist` |
| CRITICAL | src_dotfiles deployer raises | `add-dotfile: deployer failed for <alias>: <reason>` |
| WARNING | Alias collision required user override | `add-dotfile: alias collision for <derived>; using <override>` |
| WARNING | Ambiguous state — handed back to user | `add-dotfile: ambiguous state for <target>; <observation>` |
| WARNING | DEPLOY-HERE prompted for deploy_path with no good default | `add-dotfile: no similar device for <alias>; user provided <path>` |
| INFO | No-op (already correctly deployed) | `add-dotfile: no-op for <alias> on <device>` |
| INFO | First-time deploy on this device (DEPLOY-HERE path taken) | `add-dotfile: first deploy for <alias> on <device>` |
| INFO | Re-deploy (REDEPLOY path taken) | `add-dotfile: redeploy for <alias> on <device>` |

Log via: `claude-log add-dotfile <LEVEL> "<message>"`

# triggers I might have missed: <none>
```

### 9. Side-effects checklist

Every successful run results in:

- [ ] `dotfiles.json` updated and committed in `~/Setup`
- [ ] Main file present at `~/Setup/dotfiles/<alias>` (ADD path) or unchanged (DEPLOY-HERE / REDEPLOY)
- [ ] Symlink at `deploy_path` → `~/Setup/dotfiles/<alias>` on current device
- [ ] Backup created if a non-symlink file existed at `deploy_path` before
- [ ] Log line in `~/.claude/lessons.md` (at least the path-taken INFO)
- [ ] Single git commit in `~/Setup`

## Components to build

- [ ] `~/Setup/skills/add-dotfile/SKILL.md` — the skill body following sections 1–8
- [ ] `dotfiles.json` entry for `add-dotfile` skill itself (eat-own-dogfood)
- [ ] Tests (in `~/Setup/tests/`):
  - ADD: new file, no collision, deploys + commits
  - ADD: alias collision, prompts and uses override
  - ADD: target path missing, aborts with CRITICAL
  - DEPLOY-HERE: entry exists, current device missing, adds block + deploys
  - REDEPLOY: symlink missing, prompts + redeploys
  - ASK: ambiguous state, halts without write
  - Observability: each path produces the documented log line in a temp `lessons.md`

## Open questions (defer to implementation)

- **Move vs copy in ADD step 2:** moving the original file to `dotfiles/<alias>` and then symlinking back is the cleanest; but if the deploy fails partway, the original file is already gone. Going with **copy → deploy (which removes the original and creates symlink) → verify → cleanup** to keep the file intact until the deploy succeeds. Confirm during implementation.
- **Confirmation gate before commit:** does the skill auto-commit, or show diff and prompt? Going with **auto-commit** to honor "minimize attention time"; Louis can `git revert` if anything is wrong. Reconsider if this bites.
- **Atomicity:** if `dotfiles.json` is written but the deployer fails, registry and disk are out of sync. Mitigation: always run the deployer first (which is itself transactional via the existing implementation) and write `dotfiles.json` only on success. Spec assumes this ordering; verify against `src_dotfiles/DotFile.py`.

## References

- Foundation: [docs/plans/2026-04-21-skill-storage-observability-design.md](2026-04-21-skill-storage-observability-design.md)
- Audit meta-skill: [docs/plans/2026-05-05-observability-audit-design.md](2026-05-05-observability-audit-design.md)
- Existing dotfile system: `~/Setup/src_dotfiles/DotFile.py`, `~/Setup/dotfiles/dotfiles.json`

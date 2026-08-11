---
name: add-dotfile
description: Use when Louis asks to track a file as a dotfile (e.g. "add ~/.tmux.conf to my dotfiles", "track this config", "manage X via dotfiles") OR to set up tracked dotfiles on the current machine (e.g. "deploy my dotfiles here", "I'm on a new machine, sync up", "deploy <alias> on this device") OR whenever you are about to add/modify/extend an entry in `dotfiles/dotfiles.json` — including registering a newly created Claude skill, hook, or wrapper script. **Never hand-edit `dotfiles/dotfiles.json` directly; always come through this skill (which uses the `src_dotfiles` CLI). If the operation has no CLI subcommand, build it in `src_dotfiles/__main__.py` first.** Wraps the dotfiles.json registry and src_dotfiles deployer; routes by inspecting registry + filesystem state.
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion
---

# add-dotfile

Single entry point for the dotfile lifecycle: ADD (start tracking a file), DEPLOY-HERE (set up a tracked dotfile on this machine), or REDEPLOY (re-create a missing symlink). Routes by inspecting world state, not by user-supplied subcommand.

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
| CRITICAL | target_path doesn't exist on ADD | `add-dotfile: target <path> does not exist` |
| CRITICAL | src_dotfiles deployer raises | `add-dotfile: deployer failed for <alias>: <reason>` |
| CRITICAL | ~/42/SpaceSuit not a git repo / dotfiles.json missing | `add-dotfile: setup broken: <reason>` |
| WARNING | Alias collision required user override | `add-dotfile: alias collision for <derived>; using <override>` |
| WARNING | Ambiguous state — handed back to user (ASK path) | `add-dotfile: ambiguous state for <target>; <observation>` |
| WARNING | DEPLOY-HERE prompted for deploy_path with no good default | `add-dotfile: no similar device for <alias>; user provided <path>` |
| INFO | No-op (already correctly deployed) | `add-dotfile: no-op for <alias> on <device>` |
| INFO | First-time deploy on this device (DEPLOY-HERE path) | `add-dotfile: first deploy for <alias> on <device>` |
| INFO | Re-deploy (REDEPLOY path) | `add-dotfile: redeploy for <alias> on <device>` |

Concrete invocation examples:

```
claude-log add-dotfile INFO "add-dotfile: starting; target=/home/ezalos/.tmux.conf"
claude-log add-dotfile WARNING "add-dotfile: alias collision for .config; using nvim/init.lua"
claude-log add-dotfile CRITICAL "add-dotfile: target /home/ezalos/.foorc does not exist"
```

# triggers I might have missed: <none>

## Preconditions (check first; CRITICAL + abort on failure)

1. `~/42/SpaceSuit/` is a git repo: `git -C ~/42/SpaceSuit rev-parse --is-inside-work-tree` returns `true`.
2. `~/42/SpaceSuit/dotfiles/dotfiles.json` exists and parses as JSON: `python -c "import json; json.load(open('/home/ezalos/42/SpaceSuit/dotfiles/dotfiles.json'))"`.
3. `~/42/SpaceSuit/.venv/bin/python -m src_dotfiles --help` runs successfully from `~/42/SpaceSuit/`.
   **Always invoke the CLI via `~/42/SpaceSuit/.venv/bin/python`, NOT bare `python`/`python3`** —
   system Python lacks `ezpy_logs` (a sibling editable package the venv provides) and
   every `src_dotfiles` command will `ModuleNotFoundError` otherwise. Use the venv python
   for `config.identifier` and all subcommands throughout this skill.

If any fail: log CRITICAL `add-dotfile: setup broken: <reason>` and report the failing precondition to Louis. Do not proceed.

## Phase 1 — gather inputs

From the user's request, extract:

- **target_path**: the absolute path the user named (resolve `~`, relative paths to absolute).
- **target_alias** (candidate): the basename of target_path (e.g. `~/.zshrc` → `.zshrc`, `~/.config/nvim/init.lua` → `init.lua`).

Determine the current device:

```bash
cd ~/42/SpaceSuit && python -c "from src_dotfiles.config import config; print(config.identifier)"
```

Capture as `current_device` (e.g. `TheBeast.ezalos`).

Read the registry:

```python
import json
registry = json.load(open('/home/ezalos/42/SpaceSuit/dotfiles/dotfiles.json'))
```

## Phase 2 — classify intent (route to one of ADD / DEPLOY-HERE / REDEPLOY / ASK / NO-OP)

Inspect:

- Does `target_path` exist on disk?
- Is `target_path` already a symlink? Where does it point?
- Does an entry with this alias exist in `registry["dotfiles"]`?
- If yes: does that entry have a `deploy[<current_device>]` block?
- If yes: does the entry's `main` file exist at the expected location?

**First, check whether `target_path` is inside `~/42/SpaceSuit/dotfiles/`** (i.e. the file was authored directly into the dotfiles directory, e.g. a freshly written SKILL.md). If yes: there is no copy-from-system step to perform — the source is already in place; only the registry entry and the deploy symlink need to be created. The canonical `python -m src_dotfiles add` subcommand assumes target_path is the deploy_path and would set the wrong fields. **STOP** and route to the `register` subcommand instead: `python -m src_dotfiles register <alias> <absolute deploy_path>` (`--main=` defaults to `dotfiles/<alias>`; pass `--only-device=<current_device>` to scope it to this machine, or omit for a global skill and `extend_to` the other devices afterward). It creates the registry entry and symlink without backing up or copying — the source is already in place. Do not hand-edit the JSON as a shortcut.

**Owned Claude skills are a special case — they use the `skills` fan-out entry, not a per-skill entry.** Every skill Louis authors lives in `~/42/SpaceSuit/skills/<name>/` and is deployed by the single `skills` registry entry (`fanout: true`), which symlinks each child directory into `~/.claude/skills/`. To add a new owned skill: place its directory at `~/42/SpaceSuit/skills/<name>/` (create it there, or `mv`/`git mv` it in) and run `python -m src_dotfiles deploy skills` — the fan-out picks up the new child. Do NOT create an individual `register` entry for it; the old per-skill model was retired 2026-07-07 (see `docs/plans/2026-07-07-skills-fanout-deploy-design.md`). Third-party skills you did not author stay as untracked real dirs in `~/.claude/skills/` and are recorded in `skills/EXTERNAL.md`. **Before treating an untracked skill as owned (folding it in), VERIFY it is actually Louis's** — absence of a `.git` is NOT proof of authorship. Grep its files for a foreign author's hardcoded paths or handles (e.g. `/home/<someone-else>/`) and check for a matching public repo (Claude Plugin Hub, GitHub); if it's external, record it in `skills/EXTERNAL.md` instead of vendoring it. (2026-07-08: `research*` was nearly folded in before a hardcoded `/home/weizhena/` path revealed it was Weizhena/Deep-Research-skills.) The per-skill `register`/`add` routing below applies to non-skill dotfiles and to external one-off directory sources only.

Otherwise use the standard routing table:

| target exists | target is symlink | alias in registry | deploy block for current device | symlink resolves to expected main | → Path |
|---|---|---|---|---|---|
| no | — | no | — | — | **CRITICAL: target missing** |
| no | — | yes | yes | — | **REDEPLOY** (file gone, registry says it should be here) |
| yes | no | no | — | — | **ADD** |
| yes | no | yes | no | — | **DEPLOY-HERE** |
| yes | yes | yes | yes | yes | **NO-OP** (already correctly deployed) |
| yes | yes | yes | yes | no (broken symlink target) | **REDEPLOY** |
| yes | yes | yes | no | — | **ADD** (existing symlink unrelated; back up + replace) |
| any | — | yes | — | conflicting state (e.g. file path differs from `deploy_path`) | **ASK** |

State the inferred path explicitly to Louis before executing ("I'll add this as a new dotfile entry…" / "I see this is already tracked but not deployed here — I'll deploy it now").

If ASK: log WARNING `add-dotfile: ambiguous state for <target>; <observation>` and return control to Louis with the relevant context. Do not guess.

## Phase 3 — ADD path

When intent classified as ADD:

### 3a. Resolve alias (with collision check)

1. Auto-derive: `derived = basename(target_path)`.
2. Check collision: `derived in registry["dotfiles"]`?
   - If no collision → use `derived` as the alias.
   - If collision → show Louis the existing entry's `main` path. Use AskUserQuestion to ask for an override alias. Suggest a path-disambiguated form (e.g. parent directory + filename: `nvim/init.lua` instead of just `init.lua`). Log WARNING `add-dotfile: alias collision for <derived>; using <override>`.

### 3b. Register + copy + deploy via the CLI

The canonical `add` subcommand handles 3b/3c/3d in one transactional call: it creates the model, backs up any existing file at target_path, copies it into `dotfiles/<alias>`, and symlinks target_path back to the copy.

```bash
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles add <absolute target_path> --alias=<alias>
```

For skill-style dotfiles that should not auto-deploy to every device, add `--only_device=<current_device>` so the entry gets `only_devices=[<current_device>]`.

> **`add` is file-only.** Its copy step uses `shutil.copy`, which raises
> `IsADirectoryError` on a directory. A **Claude skill is a directory**
> (`~/.claude/skills/<name>/` with a `SKILL.md` inside), so `add` will fail.
> **For an owned Claude skill, use the fan-out flow instead** (see the "Owned
> Claude skills are a special case" callout in Phase 2): `mv <target_path>
> ~/42/SpaceSuit/skills/<name>/` then `python -m src_dotfiles deploy skills` — no
> per-skill entry. For any OTHER directory target (a non-skill dotfile dir), do
> NOT use `add` — instead:
> 1. `mv <target_path> ~/42/SpaceSuit/dotfiles/<alias>` (move the dir into the dotfiles tree),
> 2. `.venv/bin/python -m src_dotfiles register <alias> <target_path> --only_device=<current_device>`
>    (creates the entry + symlink in place; `--main` defaults to `dotfiles/<alias>`).
>
> This is the same flow the routing note prescribes for "source already inside
> `~/42/SpaceSuit/dotfiles/`". The `add` backup it may leave in `dotfiles/old/` on failure is
> orphaned — remove it with `rip` (never `rm`).

If it exits non-zero or returns `None` (alias collision when `force=False`):
- Log CRITICAL `add-dotfile: deployer failed for <alias>: <reason>`.
- Abort.

**Never hand-edit `dotfiles/dotfiles.json` as a fallback** — if the CLI doesn't fit the case (e.g. source already inside `~/42/SpaceSuit/dotfiles/`, multi-device pre-registration), add the missing subcommand to `src_dotfiles/__main__.py` first. The registry has invariants (`deploy`/`only_devices`/`variants`/`devices`) that the model classes enforce; manual edits drift from them silently.

### 3e. Verify

- `target_path` is a symlink.
- Symlink points to `~/42/SpaceSuit/dotfiles/<alias>`.
- `~/42/SpaceSuit/dotfiles/<alias>` is a real file/dir (not a symlink itself).
- **If the file is consumed by a daemon, verify the daemon still reads it THROUGH the
  symlink** rather than assuming. systemd is the common case: after tracking a unit or
  drop-in, run `systemctl --user daemon-reload` and confirm `DropInPaths` / `LoadState`
  / the overridden properties still show up, and that `is-enabled` survived. Do not
  restart the service to check if something is live-using it (2026-08-04: Louis was
  streaming through the very service being tracked).
- **Deploy paths inside a subdirectory** (e.g. `…/sunshine.service.d/override.conf`)
  depend on that parent dir existing. It does on the machine you are adding from, so
  the symlink succeeds — but a future first deploy on a rebuilt machine may not create
  it. Note the caveat wherever the rebuild runbook lives.

### 3f. Commit

```bash
cd ~/42/SpaceSuit
git add dotfiles/dotfiles.json dotfiles/<alias>
git commit -m "dotfiles: track <alias>"
```

Per Louis's CLAUDE.md: own repos commit directly to default branch. Don't push unless Louis asked.

If the commit fails (hooks, etc.): log WARNING `add-dotfile: commit hook failed for <alias>: <reason>` and report to Louis. Do not `--no-verify`.

Log INFO `add-dotfile: first deploy for <alias> on <current_device>`.

## Phase 4 — DEPLOY-HERE path

When intent classified as DEPLOY-HERE:

1. Show the existing entry to Louis: alias, main path, devices already deployed (paths).
2. Propose a `deploy_path` for current_device:
   - Heuristic: pick the most similar existing device's `deploy_path` (same OS prefix, same user-home pattern). For example, if there's a `Linux` device with `/home/<user>/.zshrc`, propose `/home/<current-user>/.zshrc`.
   - If no similar device: log WARNING `add-dotfile: no similar device for <alias>; user provided <path>` and use AskUserQuestion to ask Louis for the path.
3. Add the deploy entry via the CLI (do **not** hand-edit `dotfiles.json`):

   ```bash
   cd ~/42/SpaceSuit && python -m src_dotfiles extend_to <alias> <current_device> --deploy-path=<provided>
   ```

4. Deploy: `python -m src_dotfiles deploy --alias=<alias>`. Same error handling as Phase 3b.
5. Verify: symlink at `<provided>` → `~/42/SpaceSuit/<main>`.
6. Commit: `dotfiles: deploy <alias> on <current_device>`.
7. Log INFO `add-dotfile: first deploy for <alias> on <current_device>`.

## Phase 5 — REDEPLOY path

When intent classified as REDEPLOY (entry exists, deploy block exists, but symlink missing or broken):

1. Confirm with Louis using AskUserQuestion: "Entry for `<alias>` exists for this device but the symlink is missing/broken. Re-deploy?" Default: yes.
2. On confirm: `python -m src_dotfiles deploy --alias=<alias>`. Same error handling.
3. Verify: symlink restored.
4. **No `dotfiles.json` change. No commit.**
5. Log INFO `add-dotfile: redeploy for <alias> on <current_device>`.

## Phase 6 — NO-OP path

When intent classified as NO-OP (already correctly deployed):

1. Tell Louis: "Already deployed: `<target_path>` → `~/42/SpaceSuit/<main>`. Nothing to do."
2. Log INFO `add-dotfile: no-op for <alias> on <current_device>`.
3. End.

## Phase 7 — self-improvement

After completing any path (ADD / DEPLOY-HERE / REDEPLOY / NO-OP / abort), review user feedback from this run. If user input would have produced better behavior, this skill itself can be edited (see wrap-up Phase 3 "Skill self-improvement"). Defer to wrap-up to actually edit if user runs that next; otherwise leave a note in the consolidated report.

## Constraints

- **Never use `git add -A`** — stage explicit paths.
- **Never use `--no-verify`** — if a hook fails, report and stop.
- **Never delete original target_path** before the deployer succeeds — the deployer itself does the swap (file → symlink) atomically.
- **Never hand-edit `dotfiles/dotfiles.json`.** All registry mutations go through `python -m src_dotfiles <subcommand>` (`add`, `register`, `extend_to`, `deploy`). If no subcommand fits the case, add one to `src_dotfiles/__main__.py` first — write/Edit on the JSON is a violation even "just to bridge the gap." The registry persists model invariants that drift silently when bypassed.
- **Cross-platform paths**: use `python -c "import os; print(os.path.expanduser('<path>'))"` for `~`-resolution rather than relying on shell tilde expansion in passed-through Bash strings.

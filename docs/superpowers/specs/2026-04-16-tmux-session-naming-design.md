# Tmux session naming — design

**Date:** 2026-04-16
**Status:** Approved, pending implementation plan

## Goal

Rename newly created tmux sessions to encode both the working directory they were born in and their creation timestamp, and provide a one-shot tool to retroactively rename existing auto-generated sessions using the same scheme. Changes must propagate to all of Louis's machines via the existing dotfiles sync flow.

## Current state

- `tn()` in `dotfiles/.zshrc:480` creates sessions with default name `w-MMDD-HHhMM` (e.g. `w-0416-14h30`) via `date +w-%m%d-%Hh%M`. Explicit arg (`tn foo`) overrides the default.
- Existing helpers: `tls` (list with idle times), `tclean` (phantom/stale cleanup), `ta` (attach), `tsave`/`trestore` (save/restore across reboots).
- `dotfiles/.tmux.conf` is shared across all machines via the dotfiles manager.

## New naming scheme

**Format:** `{sanitized_basename}@YYYY-MM-DD-HHhMM`

**Edge case:** if basename is empty (only occurs when `$PWD` is `/`), drop the prefix: `@YYYY-MM-DD-HHhMM`.

**Sanitization rules for the basename portion:**

1. Take `basename "$PWD"`.
2. Lowercase.
3. Replace any run of characters outside `[a-zA-Z0-9]` with a single `_`.
4. Trim leading and trailing `_`.
5. Truncate to 20 characters max.

Examples:

| `$PWD`                      | basename           | sanitized           | final session name                  |
|-----------------------------|--------------------|---------------------|-------------------------------------|
| `/home/ezalos/Setup`        | `Setup`            | `setup`             | `setup@2026-04-16-14h30`            |
| `/home/ezalos`              | `ezalos`           | `ezalos`            | `ezalos@2026-04-16-14h30`           |
| `/tmp/my.project v2`        | `my.project v2`    | `my_project_v2`     | `my_project_v2@2026-04-16-14h30`    |
| `/`                         | (empty)            | (empty)             | `@2026-04-16-14h30`                 |
| `/very/deeply/extremely-long-directory-name-here` | `extremely-long-directory-name-here` | `extremely_long_dir` (20-char cap) | `extremely_long_dir@2026-04-16-14h30` |

**Rationale for `@` as the separator:** unambiguous boundary between name and date, doesn't collide with sanitized chars (which are `[a-zA-Z0-9_]`), avoids a leading `-` that would be parsed as a flag by tmux commands, sorts well lexically.

## Component 1: updated `tn()` in `.zshrc`

**Change:** the default (no-arg) name generation switches from `date +w-%m%d-%Hh%M` to the new scheme computed from `$PWD` and the current time. Explicit-arg behavior is unchanged (`tn myname` → session named `myname`).

**Behavior:**

```
tn                 → creates session  setup@2026-04-16-14h30   (when run from ~/Setup)
tn myproj          → creates session  myproj                   (unchanged)
```

**Implementation note:** the name-generation logic is factored into a small zsh helper `_tmux_gen_session_name <dir> <epoch>` in `.zshrc`, used by both `tn()` and (via a shell re-implementation) the rename script. Keeping both implementations in sync is low-risk because the logic is ~10 lines and tested explicitly.

## Component 2: rename script `scripts/tmux-rename-sessions.sh`

A one-shot tool to retroactively rename sessions created under the old scheme.

**Invocation:**

```
scripts/tmux-rename-sessions.sh          # dry-run: print planned old → new renames
scripts/tmux-rename-sessions.sh --apply  # actually perform the renames
```

Exposed as a shell alias `trename` in `.zshrc` for convenience.

**Behavior:**

1. Enumerate sessions via `tmux list-sessions -F '#{session_name}|#{session_created}'`.
2. For each session, **skip unless the name matches the old auto-pattern** `^w-[0-9]{4}-[0-9]{2}h[0-9]{2}$`. This leaves any custom-named session untouched.
3. For qualifying sessions:
   - Derive the basename from the first window's pane path. The first window is the one with the lowest `window_index` (not hard-coded to 0, since `base-index` can be either 0 or 1 across Louis's machines). Query via `tmux list-windows -t "$session" -F '#{window_index}|#{pane_current_path}' | sort -n | head -1`. Flagged in output as "best-effort" since the user may have `cd`'d since creation.
   - Derive the date from `session_created` (epoch) via `date -d "@$epoch" +%Y-%m-%d-%Hh%M` on Linux; macOS uses `date -r "$epoch" +...`. The script detects which is available at top (see cross-platform note below).
   - Run the shared sanitization to produce the new name.
4. Print the full plan as `old_name → new_name`, one per line.
5. If `--apply`:
   - For each planned rename, check `tmux has-session -t new_name`. If it already exists, **print a warning and skip that one** (do not abort the run).
   - Otherwise, run `tmux rename-session -t "$old" "$new"`.
6. Print a summary: `N renamed, M skipped (collision), K skipped (custom name)`.

**Cross-platform note:** Setup supports both macOS (BSD) and Linux (GNU). `date` epoch parsing differs — detect once at top of the script and branch accordingly. This matches the existing pattern in other `scripts/` files (per global feedback memory about cross-platform shell).

**Error handling:**

- No tmux server running → exit 0 with message, same as `tsave`.
- A session whose window 0 doesn't exist (unlikely but possible) → skip with warning.
- Collision on rename → warn and continue, as above.

## Component 3: alias wiring in `.zshrc`

Alongside the existing `tsave` / `trestore` definitions (around `.zshrc:511`), add:

```zsh
trename()  { "$PATH_SETUP_DIR/scripts/tmux-rename-sessions.sh" "$@"; }
```

## Deployment flow (per machine)

1. `git pull` in `~/Setup`.
2. Redeploy dotfiles via Louis's usual flow (`.zshrc` + the new script are both under already-synced paths).
3. `source ~/.zshrc` (or open a new shell).
4. `trename` to preview, then `trename --apply` to commit.

The script change is a plain addition to `scripts/`, which the dotfiles manager already syncs, so no deployment-logic changes are needed.

## Explicit non-goals

- **Window naming is unchanged.** Only sessions are touched.
- **No changes to `tsave`/`trestore`.** Session names flow through those transparently.
- **No behavior change for sessions with custom names.** They are filtered out by the regex in step 2 of the rename script.
- **No automatic periodic re-renaming.** The rename is a one-shot operation, invoked manually.

## Testing

- `tn` (no arg) from several directories: `~/Setup`, `~`, `/`, a path with spaces, a path with a dot — verify the produced names match the table above.
- `tn myproj` — verify override still works.
- Dry-run `trename` with a mix of old-pattern and custom-named sessions — verify only old-pattern sessions are listed.
- `trename --apply` on a disposable test session — verify rename happens.
- Collision case — create two sessions that would hash to the same new name and verify the second is skipped with a warning, not aborted.
- Run the script on macOS once the Linux path is working, to confirm the `date` branch.

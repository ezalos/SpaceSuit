---
name: proton-drive
description: Use when Louis asks to move files between this machine and his Proton Drive — e.g. "push/upload this to my drive", "pull/get X from proton drive", "what's on my drive", "put this in proton". Wraps the pdrive CLI (official Proton Drive CLI underneath; browser-auth session in the OS keyring).
allowed-tools: Read, Bash, AskUserQuestion
---

## Observability

This skill follows the universal observability baseline (see `docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (Claude was about to be wrong), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during the run.** Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change — already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | pdrive exits 3 (no session) | `proton-drive: session expired; notified Louis to run 'pdrive login'` |
| CRITICAL | pdrive/proton-drive missing from PATH | `proton-drive: binary missing; pointed at bin/proton-drive-update.sh` |
| WARNING | Push target looks secret-bearing (name contains "secret", "password", "key", ".envrc", or lives under ~/.claude/channels) | `proton-drive: refused/queried push of '<name>' (possible secret)` |
| WARNING | File > 1GB | `proton-drive: large push <name> (<size>); confirmed with Louis` |
| INFO | Successful push/pull round | `proton-drive: <push|pull> <name> <-> <remote-path>` |

## Usage

    pdrive push <local-path> [remote-dir]   # default remote dir: /my-files/TheBeast
    pdrive pull <remote-path> [local-dir]   # default local dir: cwd
    pdrive ls [remote-dir]                  # default: /my-files
    pdrive login                            # Louis-only; prints a URL to open on any device

Remote paths are rooted at `/my-files` (Proton Drive's root as the CLI sees it).
Unsure of a remote path? `pdrive ls` first. For operations beyond push/pull/ls, use the
underlying `proton-drive` binary directly — `proton-drive --help` lists the full surface.

Operational notes (verified 2026-08-03):
- Upload does NOT auto-create the target folder — the parent must exist or you get
  `Node not found: <name>`. The default push dir `/my-files/TheBeast` already exists.
  New folder: `proton-drive filesystem create-folder <parentPath> <name>`.
- Removal is two-step: `proton-drive filesystem trash <path>` (recoverable), then
  `filesystem delete` only works on already-trashed items. Prefer `trash`.
- Listing is eventually consistent: a just-trashed item can linger for a few seconds.

## Session expiry (exit 3)

`pdrive` exits 3 with `no valid session` when the keyring session is dead. Do NOT retry.
Use the notify-louis skill: he must run `pdrive login` himself — it prints a URL he opens
in a browser on any device (his Mac; he is SSH-only on this machine) while the terminal
stays open. Never ask for, handle, or store his Proton password.

## Caveats

- Never push vault material, `.envrc` files, or anything from `~/.claude/channels/` — Drive
  is personal storage, not a secrets channel.
- Works only while Louis's desktop session has an unlocked keyring; headless-while-logged-out
  fails at auth — that's a known limit, not a bug.
- Ad-hoc transfers only: no sync, no mount. For sharing a file with someone else, the
  share-file skill is the right tool, not Drive.

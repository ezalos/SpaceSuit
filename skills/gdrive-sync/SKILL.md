---
name: gdrive-sync
description: Use when Louis wants anything from his Google Drive research folder or the local mirror of it — e.g. "get the paper on X from my drive", "what's in backups/Research", "organize the research folder", "sync my drive", "is the Drive mirror up to date", "add folder Y to the mirror". The mirror lives at ~/Drive (same tree as Drive); `gdrive-sync` wraps rclone bisync with a visible delta before any sync and halts rather than guesses.
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
---

## Observability

This skill follows the universal observability baseline (see `docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction, fallback, retry, precondition-fail.
- INFO (systematic) on any user feedback, suggestion, or caveat during the run. Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>`.
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | `gdrive-sync run` exits 7 (halted) | `gdrive-sync: HALTED: <reason>; stopped, Louis notified by the wrapper` |
| CRITICAL | `gdrive-sync run` exits 1 with "too many deletes" (refused) | `gdrive-sync: REFUSED: <reason>; stopped, Louis notified by the wrapper` |
| CRITICAL | `gdrive-sync check` fails before a read | `gdrive-sync: mirror stale/halted; refusing to answer from stale files` |
| WARNING | `diff` shows `*` on a file the task would modify | `gdrive-sync: '<path>' differs on both sides; not touching it` |
| INFO | `run` before/after a task | `gdrive-sync: synced before read` / `synced after organize; N files changed` |

```
claude-log gdrive-sync INFO "gdrive-sync: synced before read"
claude-log gdrive-sync CRITICAL "gdrive-sync: HALTED: Safety abort: too many deletes; stopped"
```

# gdrive-sync

The local mirror is `~/Drive/<same path as on Drive>`; today `~/Drive/backups/Research/`.
Drive is the source of truth (Louis edits from his tablet); Drive wins every conflict and
the local loser survives as `paper.pdf.conflict1`. The timer syncs every 10 min.

## Contract

1. **Before reading**: `gdrive-sync run` (fresh copy; exit 0) — or at least `gdrive-sync check`
   (exit 0 = synced within the hour). Never answer from a halted or stale mirror.
2. **Organising** (mv/rename/new subfolders inside `~/Drive/backups/Research/`): work on the local
   tree with `mv`, never `rm` (use `rip`; rclone moves Drive deletes to trash anyway). Then
   `gdrive-sync plan` — read it — then `gdrive-sync run`. Tell Louis what moved.
3. **Never modify** a file that `gdrive-sync diff` marks `*` (both sides changed): report it.
4. **Halt (exit 7)** — bisync's critical abort: the wrapper already Telegrammed Louis with the commands; stop. **Refusal (exit 1, "too many deletes")** — the run would delete more than 10 % of one side; nothing changed, Louis was Telegrammed; stop. In both cases: do not `resync`, do not `run --force`, do not retry, do not delete anything to "help". Both decisions are Louis's.
5. **Widening** (another Drive folder): add `+ /path/**` above `- **` in GroundControl
   `monitoring/thebeast/gdrive-sync.filters`, run `monitoring/thebeast/install-gdrive-sync.sh`
   (copies it to `~/.config/gdrive-sync/filters`), commit, `gdrive-sync markers`, then ask Louis
   before `gdrive-sync resync --yes` (the resync makes Drive win on every differing file).
6. Downloads from the web into the research folder: save into `~/Drive/backups/Research/...`,
   then `gdrive-sync run`. Nothing over 2 GB, 25 MB/s cap (household line rule).
7. **Write atomically.** The timer can list the tree while you write. Download or render to a
   path OUTSIDE `~/Drive` (e.g. `~/Inbox/` or a temp dir on the same filesystem), then `mv`
   the finished file into place — one rename, never a file that grows inside the mirror.
   Same for batches: assemble the folder elsewhere, `mv` it in, then `gdrive-sync run`.

## Commands

`gdrive-sync diff | plan | run [--force] | resync [--yes] | status | check | markers`
Journal: `journalctl --user -u gdrive-sync -n 50`. Local backups of anything a sync
deleted/overwrote: `~/.local/state/gdrive-sync/backup/<UTC ts>/`.

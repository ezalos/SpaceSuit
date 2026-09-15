# gdrive-sync

Two-way mirror of chosen Google Drive folders into a local tree that keeps Drive's
layout, built on `rclone bisync` (>= 1.75). Drive is Path1 and always wins a
conflict; the local loser survives as `*.conflict1`. Nothing is ever unlinked: Drive
deletes go to Drive's trash, local deletes/overwrites are moved to
`<state>/backup/<UTC ts>/`. A run that would delete more than `GDRIVE_MAX_DELETE`
percent of either side is *refused*: nothing changes, one Telegram goes out
immediately, and every later run retries (so restoring the files heals it without
touching the wrapper); `gdrive-sync run --force` is the human override. A *halt* is
different: bisync's own critical abort (empty listing, missing `RCLONE_TEST` marker,
inconsistent listings) freezes the mirror until `gdrive-sync resync --yes`. A resync
overwrites local files in place but moves the previous version to `<state>/backup/<ts>/`
first.

## Config: `~/.config/gdrive-sync/env` (override: `--env`, `$GDRIVE_SYNC_ENV`)

| Key | Meaning | Default |
|---|---|---|
| `GDRIVE_REMOTE` | rclone remote, Path1 (`gdrive:`) | required |
| `GDRIVE_LOCAL` | mirror root, Path2 | required |
| `GDRIVE_FILTERS` | bisync filters file; `+ /a/b/**` lines then `- **` | required |
| `GDRIVE_BWLIMIT` | rclone `--bwlimit` | `25M` |
| `GDRIVE_STATE_DIR` | state.json, lock, last-run.log, workdir/, backup/ | `~/.local/state/gdrive-sync` |
| `GDRIVE_MAX_DELETE` | percent per side per run | `10` |
| `GDRIVE_STALE_AFTER` | seconds before `check` fails | `3600` |
| `GDRIVE_TRANSFERS` | rclone `--transfers` (parallel file transfers; Drive is per-file latency-bound, so bulk phases scale with it) | `8` |
| `GDRIVE_CHECKERS` | rclone `--checkers` | `16` |
| `RCLONE_CONFIG_PASS` | `pass://` ref; the wrapper re-execs under `secrets run --` | — |

## Subcommands

`diff` (read-only compare: = same, + Drive only, - local only, * differ), `plan` (bisync `--dry-run`), `run [--force]`,
`resync [--yes]` (shows diff; requires --yes; Drive wins; previous local versions land in backup/), `status`,
`check` (exit 1 = halted/refused/never/stale), `markers`, `auth`.

## Widening the mirror

Add a `+ /other/folder/**` line before `- **`, run `gdrive-sync markers`, then
`gdrive-sync resync --yes` (bisync refuses to run on a changed filters file until a
resync — by design).

## Units

`gdrive-sync.service` + `.timer` here; symlink into `~/.config/systemd/user/` and
`systemctl --user enable --now gdrive-sync.timer`. Exit 7 is a designed halt: the unit
reads as failed until a human runs `resync`, which is the honest state.

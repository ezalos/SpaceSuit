---
name: pull-uploads
description: Use when Louis asks to fetch files dropped from his phone — e.g. "pull uploads", "check the inbox", "get the files from my phone", "fetch from upload.develle.fr", "what's in the inbox", "anything new from my phone". Wraps the `pull-uploads` CLI which rsyncs `/srv/upload/inbox/` on TinyButMighty down to `~/Inbox/` and clears the remote.
allowed-tools: Bash, Read, AskUserQuestion
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
| CRITICAL | `pull-uploads` CLI errors out (ssh fail, rsync fail) | `pull-uploads: CLI failed: <stderr-tail>` |
| CRITICAL | TinyButMighty unreachable (ssh ConnectTimeout) | `pull-uploads: TinyButMighty unreachable; check NAT / Pi power` |
| WARNING | Inbox empty but Louis was clearly expecting files | `pull-uploads: nothing fetched; flagged in case Louis just uploaded` |
| WARNING | A fetched filename suggests sensitive content (contains "secret", "password", "key", "private") | `pull-uploads: filename '<name>' may be sensitive; surfaced to Louis` |
| INFO | Same-name-same-second collision resolved by the `_N` suffix | `pull-uploads: collision on '<name>' resolved as '<final-name>'` |
| INFO | Files fetched successfully | `pull-uploads: fetched <N> file(s) to ~/Inbox/` |
| INFO | Inbox was empty | `pull-uploads: inbox empty; no-op` |

Concrete invocation examples:

```
claude-log pull-uploads INFO  "pull-uploads: starting"
claude-log pull-uploads INFO  "pull-uploads: fetched 3 file(s) to ~/Inbox/"
claude-log pull-uploads CRITICAL "pull-uploads: CLI failed: ssh: connect to host tinybutmighty port 22: Connection refused"
```

# triggers I might have missed: disk full on TheBeast (rsync would fail mid-transfer)

# pull-uploads

Fetches whatever's sitting in the WebDAV inbox on TinyButMighty into `~/Inbox/` on this machine, then removes the source on the Pi. The companion to the upload-from-phone web UI at https://upload.develle.fr/.

## When to run

- Louis says "pull uploads", "check inbox", "fetch from phone", "anything new on the Pi", "grab my uploads".
- After Louis uploads something from his phone and asks where the file landed.
- As a routine inbox-clear before starting a session that uses files he sent.

Don't run it on a cadence (no cron / no /loop) unless Louis explicitly asks — pulling is manual on purpose so he decides when files come down.

## Workflow

```bash
pull-uploads
```

The script (`~/Setup/bin/pull-uploads`) prints either:

- `Nothing in the inbox.` — no-op, exit cleanly.
- `Fetched N file(s):` followed by one absolute path per line.

Hand the path list back to Louis verbatim. If he asked to do something with the files (open, summarize, extract, etc.), use the printed paths.

If the alias is missing, fall back to `~/Setup/bin/pull-uploads` directly — the binary is on `$PATH` via `$PATH_SETUP_DIR/bin` so this almost never happens.

## Inputs to gather

None. The script takes no arguments. Three env-var overrides exist for advanced use:

| Env var | Default | When to override |
|---|---|---|
| `UPLOAD_INBOX_DEST` | `~/Inbox` | Pulling to a different staging dir for a specific session |
| `UPLOAD_REMOTE_HOST` | `TinyButMighty` | Testing against a different host |
| `UPLOAD_REMOTE_PATH` | `/srv/upload/inbox/` | Pulling from a non-default remote dir |

## Sensitive-filename heuristic

After printing the fetched list, scan filenames for tokens like `secret`, `password`, `private`, `key`, `cred`, `token`, `.env`. If a match comes up, flag it to Louis ("`name.env` looks sensitive — want it moved out of `~/Inbox` immediately?") rather than auto-acting.

## When the inbox infra isn't reachable

If `pull-uploads` errors with an ssh or rsync failure, the most likely causes are:

- TinyButMighty powered off / not on the network → check with `ping TinyButMighty` and tell Louis.
- SFR Box NAT rule dropped → see `open-local-port` skill for the 443 rule (port 22 is internal-only, not NAT'd; SSH works via LAN).
- Caddy / WebDAV stack broken → see `~/Setup/upload_file/README.md` and `~/Setup/share_file/README.md` for bootstrap.

Don't try to bootstrap the stack from this skill — point Louis at the README and stop.

## Reminders / caveats

- **Files are removed from the Pi on successful pull.** If Louis wants to fetch without removing (rare), tell him to `rsync` directly without `--remove-source-files` rather than modifying the script.
- **Files arrive datetime-prefixed** (`YYYY-MM-DD_HHMMSS_<original-name>`). The prefix is applied **at reception on the Pi** by the tusd post-finish hook (the root fix — repeat uploads of the same name all survive server-side; source: `~/Setup/upload_file/tusd/post-finish`). The pull script preserves that name, lands files via a staging dir (never overwrites `~/Inbox/`), and only adds its own mtime-stamp to files that arrive unprefixed (legacy files, native WebDAV PUTs). When telling Louis where a file landed, quote the full prefixed name.
- **`~/Inbox/` accumulates indefinitely.** No cleanup. Suggest archiving older files periodically if it gets cluttered.

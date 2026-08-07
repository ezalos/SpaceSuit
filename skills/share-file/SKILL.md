---
name: share-file
description: Use when Louis asks to share a local file via a private link — e.g. "share this file", "send a temporary link", "upload X for someone", "give me a link that expires in 2h". Wraps the share-file CLI which uploads to TinyButMighty and serves via Caddy on share.develle.fr.
allowed-tools: Read, Bash, AskUserQuestion
---

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
| CRITICAL | `share-file` CLI errors out (scp fail, etc.) | `share-file: CLI failed: <stderr-tail>` |
| CRITICAL | Share infra not bootstrapped (`/srv/share` missing on Pi) | `share-file: infra not bootstrapped; pointed Louis at share_file/README.md` |
| WARNING | Filename suggests sensitive content (e.g. contains "secret", "password", "key", "private") | `share-file: filename '<name>' may be sensitive; suggested rename` |
| WARNING | File size > 1GB (no quota enforced; flag for user awareness) | `share-file: large file <name> (<size>); confirmed with Louis` |
| WARNING | User asked to share a directory or multiple files (CLI rejects) | `share-file: directory/multi-file share requested; suggested zip` |
| WARNING | Duration parse failure (e.g. combined units like '2h30m') | `share-file: bad duration '<input>'; asked Louis for single-unit form` |
| INFO | URL generated successfully | `share-file: shared <name> for <duration>; URL handed to Louis` |
| INFO | Fell back to direct python invocation (alias missing) | `share-file: alias missing; used python3 direct path` |

Concrete invocation examples:

```
claude-log share-file INFO "share-file: starting; path=<path> duration=<dur>"
claude-log share-file WARNING "share-file: filename 'secrets.env' may be sensitive; suggested rename"
claude-log share-file CRITICAL "share-file: CLI failed: ssh: connect to host tinybutmighty port 22: Connection refused"
```

# triggers I might have missed: token-collision (extremely rare with 192-bit entropy), TinyButMighty disk full

# share-file

Generates a long-random-token URL that serves a single local file from `share.develle.fr` for a bounded time, then expires.

## Defaults

- **Duration**: 1 week (`7d`)
- **Token**: 32-char URL-safe base64 (192 bits of entropy, generated on TheBeast)
- **URL shape**: `https://share.develle.fr/<token>/<filename>`
- **Backend**: scp → `tinybutmighty:/srv/share/<token>/<filename>`. Caddy serves it. A systemd timer prunes expired tokens every 5 minutes.

## Inputs to gather

| Field | Required | Default | Notes |
|---|---|---|---|
| `path` | yes | — | absolute or relative path to a single file |
| `duration` | no | `7d` | `Ns`/`Nm`/`Nh`/`Nd` — no combined units |

If Louis asks to share **multiple files or a directory**, the CLI takes a single file only — so zip
them into one archive first and share that. Zip is the natural, universally-openable choice (opens
on any OS without extra steps), so prefer it over tar:

```bash
zip -j bundle.zip file1 file2 ...   # -j flattens paths for loose files; drop -j to keep a dir tree
share-file bundle.zip [--duration <Nh|Nm|Nd>]
```

Confirm the archive name with Louis (it appears in the URL). Don't invoke `share-file` once per file
when the intent is to hand over a set — one zip is cleaner.

## Workflow

```bash
share-file <path> [--duration <Nh|Nm|Nd>]
```

The script (located at `~/Setup/share_file/share.py`, aliased to `share-file` in `.zshrc`) prints the URL on stdout and the expiry timestamp on stderr.

If the alias is missing for some reason, fall back to:

```bash
python3 ~/Setup/share_file/share.py <path> --duration <duration>
```

After running, copy the URL line and hand it to Louis. Do not click/curl the link to "verify" it — that would generate access logs in `/var/log/caddy/share.access.log` for an unintended viewer.

## When the share infra isn't bootstrapped yet

If `ssh TinyButMighty 'test -d /srv/share'` fails, or `share-file` errors with a connection issue, the share stack hasn't been set up. Walk Louis through `~/Setup/share_file/README.md` — do not start running its bootstrap commands without confirming first; they touch the Pi, the SFR Box, and Cloudflare DNS.

The bootstrap relies on the `open-local-port` and `link-develle-domain` skills for steps 4 and 5 respectively.

## Reminders / caveats

- **Token is the only secret.** Anyone with the URL can fetch the file until it expires. There is no per-recipient auth.
- **Files are unencrypted on the Pi** until cleanup. Don't share secrets without `age`-encrypting first.
- **Public IP is exposed** for `share.develle.fr` (record is not Cloudflare-proxied). Fine for casual sharing; flag it if Louis seems to want CF-level hiding.
- **Filename appears in the URL.** If the filename itself is sensitive (e.g. `internal-roadmap-q3.pdf`), suggest renaming before sharing.
- **No quota / size limit enforced.** The CLI happily uploads a 4GB file. Use judgment.
- **Default 7d fits the common "hand a file to someone this week" case.** For anything sensitive, pass a short explicit duration (`1h`, `30m`) — the file sits unencrypted on the Pi for the whole lifetime. Prefer days (`2d`) over very long hours (`48h`) — same thing, but more readable.

## Verifying expiry

To confirm a link has expired (e.g. for forensics), `ssh TinyButMighty 'ls /srv/share/'` — the token directory should be gone after the next janitor pass (≤5 min after expiry).

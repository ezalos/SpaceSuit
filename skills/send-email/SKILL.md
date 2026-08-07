---
name: send-email
description: Use when Louis asks to send an email — e.g. "send a mail to X", "email so-and-so", "send a test email", "email me when X finishes", or when an agent needs to notify by mail. Wraps the send-email CLI which submits via Proton hosted SMTP (smtp.protonmail.ch:587) as louis@develle.fr.
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
| CRITICAL | SMTP auth failed (`535`/`5.7.x` from Proton) | `send-email: auth rejected; PROTON_SMTP_TOKEN likely revoked or wrong` |
| CRITICAL | `send-email` CLI exits non-zero (network, TLS, 5xx) | `send-email: CLI failed: <stderr-tail>` |
| CRITICAL | Required env var missing and `.secrets.sh` unreadable | `send-email: PROTON_SMTP_* missing; ~/Setup/.secrets.sh not loaded` |
| WARNING | Recipient address looks malformed (no `@` or no TLD) | `send-email: recipient '<addr>' looks malformed; confirmed with Louis` |
| WARNING | Subject empty or `<3` chars (spam risk) | `send-email: weak subject '<subj>'; asked Louis to expand` |
| WARNING | Body > 50 KB (likely paste of large content) | `send-email: large body (<n> KB); confirmed with Louis` |
| WARNING | Louis asked for HTML / attachments / multi-recipient (unsupported in v1) | `send-email: <feature> requested; not supported, suggested workaround` |
| INFO | Body taken from stdin or `--body-file` | `send-email: body from <stdin\|body-file>` |
| INFO | Mail accepted by Proton | `send-email: queued for <to>; subject='<subject>'` |

Concrete invocation examples:

```
claude-log send-email INFO  "send-email: starting; to=<addr> subject='<subj>' body-src=<arg|stdin|file>"
claude-log send-email WARNING "send-email: HTML body requested; v1 sends plain text only, stripped tags"
claude-log send-email CRITICAL "send-email: auth rejected by smtp.protonmail.ch; check PROTON_SMTP_TOKEN"
```

# triggers I might have missed: Proton 421 rate-limit, recipient bounce (async — no 5xx at SMTP-time), TLS cert rotation issues

# send-email

Submits a plain-text email through Proton's hosted SMTP submission endpoint. Authenticated with an SMTP token stored in `~/Setup/.secrets.sh`. From-address is fixed to `louis@develle.fr`.

## Defaults

- **Server**: `smtp.protonmail.ch:587` (STARTTLS)
- **From**: `louis@develle.fr` (cannot be changed — Proton enforces the authenticated user as MAIL FROM)
- **Subject**: required, no default
- **Body source**: `--body`, `--body-file`, or stdin (in that priority order)
- **Encoding**: UTF-8, plain text

## Inputs to gather

| Field | Required | Default | Notes |
|---|---|---|---|
| `--to` | yes | — | single recipient address |
| `--subject` | yes | — | quote it; flag if `<3` chars or empty |
| `--body` / `--body-file` / stdin | one of | — | pick the form that fits the trigger |

If Louis asks for any of these (none supported in v1), stop and surface it:
- HTML body, attachments, multiple recipients, BCC, CC, custom From-address.

## Workflow

```bash
send-email --to <addr> --subject "<text>" --body "<text>"
# or
send-email --to <addr> --subject "<text>" --body-file <path>
# or
<command-producing-text> | send-email --to <addr> --subject "<text>"
```

The script lives at `~/Setup/bin/send-email` (on PATH). On success it prints `send-email: sent to=… subject="…"` on stderr and exits 0. On failure curl's error surface comes out on stderr.

If the binary is missing for some reason, fall back to direct execution:

```bash
bash ~/Setup/bin/send-email --to <addr> --subject "<text>" --body "<text>"
```

## Reminders / caveats

- **Token is in `~/Setup/.secrets.sh`.** Never embed the value in scripts, mail bodies, or logs. Never read or paste it back when explaining what happened — refer to it as `$PROTON_SMTP_TOKEN`.
- **From is locked to `louis@develle.fr`.** Don't try to send "from" anyone else — Proton's SMTP submission requires MAIL FROM to be the authenticated user. Aliases require Proton-side setup, not a CLI flag.
- **Spam-on-cold-start.** First mails to a new recipient may land in Promotions or Spam in Gmail, even with SPF/DKIM/DMARC clean. Mention this when sending to a brand-new address.
- **No HTML, no attachments, no multi-recipient yet.** If Louis asks for one of these, surface the gap rather than half-implementing — extend the wrapper, don't pile onto curl manually.
- **Don't use for blocker pings to Louis.** Use the `notify-louis` Telegram skill instead — it's instant on his phone. `send-email` is for emailing *other people*, scheduled summaries, or external integrations.
- **Body-from-stdin makes piping easy:** `git log -5 | send-email --to … --subject "weekly recap"`. Use it where the body naturally comes from another command.

## When the SMTP creds aren't set up

If `send-email` fails with `PROTON_SMTP_* not set`, walk Louis through generating a token at `account.proton.me → Account & password → Mail credentials → SMTP submission`, then appending the four `PROTON_SMTP_*` vars to `~/Setup/.secrets.sh`. Do not commit the file — it's gitignored.

# notify-louis — Telegram notification skill

## Purpose

Let Claude Code reach Louis on Telegram for the narrow set of moments where the
session genuinely cannot proceed without him, plus explicit "notify me when
done" handoffs on long work. Everything else stays in the terminal.

The Telegram plugin (`plugin:telegram`) is already configured: bot token in
`~/.claude/channels/telegram/.env`, Louis's user ID `6194225401` allowlisted in
`~/.claude/channels/telegram/access.json`. Inbound replies are already routed by
the plugin; this skill only handles **outbound** pings.

## Scope

In:

- A global skill that fires on three trigger conditions (blocker / guidance /
  explicit-done).
- A helper script that gathers context, formats the message, and POSTs to the
  Telegram Bot API.

Out:

- Inbound message handling (already covered by the telegram plugin).
- Automatic detection of "long work completed" — completion pings require an
  explicit user opt-in earlier in the conversation.
- Hooks (Stop, PostToolUse, etc.). The script is hook-friendly by design but
  wiring hooks is not part of this spec.

## Layout

```
~/.claude/skills/notify-louis/
├── SKILL.md         # frontmatter + trigger rules + invocation contract
└── notify.sh        # context gathering + Telegram send
```

Global skill (lives under `~/.claude/skills/`), so it's available in every
project Claude touches.

## Trigger rules (in SKILL.md)

Fire only for:

- **🚫 blocker** — cannot proceed without Louis. Examples: OAuth token /
  password / credential needed, physical action required (plug in a device,
  flip a switch), destructive op awaiting approval, external resource locked by
  another process.
- **❓ guidance** — there are ≥2 reasonable paths and the choice would
  meaningfully reshape the work. Not for minor style preferences or questions
  Claude can answer itself.
- **✅ done** — *only* when Louis explicitly said "notify me when done" / "ping
  me when it's ready" / similar earlier in the conversation. No implicit
  detection of "this seems like a long task."

Do **not** ping for:

- Routine permission prompts when there's an active terminal session.
- Questions Claude can answer itself.
- Every multi-step task.
- Mid-work status updates.

After a 🚫 or ❓ ping, Claude ends the turn — it's blocked by definition.

## Message format

Plain text (no Telegram HTML/Markdown — avoids escaping pain on `<`, `>`, `_`,
`*` in paths and reasons). Format B with leading emoji:

```
🚫 Blocker — Setup
Need GitHub OAuth token to push PR branch.

What I need from you: paste the token, or tell me to abort.

ta 'Setup-1:3'
cd /home/ezalos/Setup
```

Header line: `<emoji> <Kind> — <project>`, where:

| kind     | emoji | label            |
|----------|-------|------------------|
| blocker  | 🚫    | Blocker          |
| guidance | ❓    | Guidance needed  |
| done     | ✅    | Done             |

Body: the reason verbatim from the script's second arg, then a blank line, then
**"What I need from you:"** for blocker/guidance (omitted for done — completion
pings don't ask for action).

Trailer (always included, regardless of kind, so Louis can see at a glance
which session pinged him): tmux attach hint and `cd`. The `ta` line uses
Louis's existing zsh
function (`~/Setup/dotfiles/.zshrc:490`), which accepts tmux's
`session:window` target syntax and selects the right window on attach.

If `tmux display-message` fails (not inside tmux), omit the `ta` line and keep
only the `cd` line.

## `notify.sh <kind> "<reason>"` behavior

Usage:

```
notify.sh <kind> "<reason>"
   kind ∈ {blocker, guidance, done}
```

Steps:

1. Validate `kind` is one of the three; reject otherwise (exit 1).
2. Load `TELEGRAM_BOT_TOKEN` from `~/.claude/channels/telegram/.env`. Missing
   file or unset var → exit 2 with stderr hint
   `"telegram not configured — run /telegram:configure"`.
3. Read chat_id from `~/.claude/channels/telegram/access.json` →
   `.allowFrom[0]` via `jq`. Missing file or empty allowlist → exit 3.
4. Gather context:
   - `session_window=$(tmux display-message -p '#S:#I' 2>/dev/null || true)`
   - `project=$(basename "$PWD")`
5. Compose the plain-text message per the format above.
6. POST `https://api.telegram.org/bot$TOKEN/sendMessage` with form fields
   `chat_id` + `text`. Use `curl --silent --show-error --fail-with-body` so
   non-2xx responses surface the Telegram error JSON on stderr (exit 4).

### Env knob

- `NOTIFY_DRY_RUN=1` prints the composed payload to stdout and exits 0 without
  hitting the network. Used for testing without spamming the real chat.

### Dependencies

- `bash`, `curl`, `jq`, `tmux` (optional — only used when present), `basename`.
- `command -v jq` is checked up front; missing → exit 5 with a clear message.

## Error handling summary

| condition                       | exit | stderr message                                          |
|---------------------------------|------|---------------------------------------------------------|
| bad/missing kind arg            | 1    | usage hint                                              |
| no `.env` or token unset        | 2    | "telegram not configured — run /telegram:configure"     |
| no `access.json` or empty list  | 3    | "telegram allowlist empty — run /telegram:access pair"  |
| curl non-2xx                    | 4    | full Telegram API error JSON                            |
| `jq` missing                    | 5    | "jq required — install via apt/brew"                    |

Claude treats any non-zero exit as: don't pretend the ping went through;
surface the failure to Louis in the terminal instead.

## Cross-platform constraints

Per the user's `feedback_cross_platform_shell` memory, scripts must run on both
macOS (BSD) and Linux (GNU):

- `#!/usr/bin/env bash`, `set -euo pipefail`.
- Only portable flags on `basename` / `date` / `curl` / `jq`.
- No `readlink -f` (BSD readlink lacks `-f`).
- No GNU `date -d`.
- No `flock` (Linux-only).
- Use `printf` not `echo -e`.

## Testing (manual, one-shot)

- `notify.sh blocker "test blocker"` from inside a tmux session → Telegram
  receives a message with the `ta '<session>:<window>'` line.
- `notify.sh done "test done"` from outside tmux → message arrives without the
  `ta` line, only `cd <pwd>`.
- `NOTIFY_DRY_RUN=1 notify.sh guidance "test guidance"` → payload printed to
  stdout, no HTTP request made.
- Temporarily rename `.env` → `notify.sh blocker "x"` exits 2 with the
  configured stderr hint; nothing is sent.
- `notify.sh bogus "x"` exits 1 with a usage line.

## Out-of-scope, parked for later

- A `Stop` hook that auto-fires `notify.sh done "<task>"` when long work
  finishes. Worth doing once the script is proven stable and the wording feels
  right.
- Reaction support / message editing for progress updates.
- Multiple chat targets (groups). Single DM target is enough for now.

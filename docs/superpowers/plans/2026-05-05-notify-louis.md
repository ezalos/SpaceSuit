# notify-louis Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global Claude Code skill (`~/.claude/skills/notify-louis/`) that pings Louis on Telegram for blockers, guidance requests, and explicit "notify me when done" handoffs.

**Architecture:** Two artifacts — `SKILL.md` holds the trigger rules + invocation contract; `notify.sh` gathers tmux/cwd context, composes a plain-text message per the spec format, and POSTs to the Telegram Bot API. Telegram is already configured (token in `~/.claude/channels/telegram/.env`, chat_id read from `~/.claude/channels/telegram/access.json:.allowFrom[0]`). Manual verification only — no automated test suite for a one-shot helper script.

**Tech Stack:** Bash, `curl`, `jq`, `tmux` (optional). Shell helpers must work on both macOS (BSD) and Linux (GNU).

**Spec:** [`docs/superpowers/specs/2026-05-05-notify-louis-design.md`](../specs/2026-05-05-notify-louis-design.md)

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `~/.claude/skills/notify-louis/SKILL.md` | Frontmatter (`name`, `description`), trigger rules, invocation contract |
| `~/.claude/skills/notify-louis/notify.sh` | Argument validation, dependency check, token + chat_id loading, context gathering, message composition, Telegram POST, dry-run support |

Both files live outside any git repository (under `~/.claude/`), so this plan tracks no commits to those files. The plan and spec live under `~/Setup/docs/superpowers/`. Verification at each task is by running `notify.sh` with specific inputs and inspecting stdout / stderr / exit code (and, at the end, a real Telegram delivery).

---

## Task 1: Scaffold the skill directory and skeleton script

**Files:**
- Create: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Create the directory and skeleton script**

```bash
mkdir -p ~/.claude/skills/notify-louis
cat > ~/.claude/skills/notify-louis/notify.sh <<'SH'
#!/usr/bin/env bash
# notify.sh <kind> "<reason>"
#   kind ∈ {blocker, guidance, done}
# Sends a Telegram ping to Louis. See SKILL.md for trigger rules.
set -euo pipefail

ENV_FILE="$HOME/.claude/channels/telegram/.env"
ACCESS_FILE="$HOME/.claude/channels/telegram/access.json"

main() {
  echo "TODO: implement" >&2
  return 99
}

main "$@"
SH
chmod +x ~/.claude/skills/notify-louis/notify.sh
```

- [ ] **Step 2: Verify the skeleton runs and reports its placeholder**

Run: `~/.claude/skills/notify-louis/notify.sh blocker "ignored"`
Expected: stderr `TODO: implement`, exit code `99`.

```bash
~/.claude/skills/notify-louis/notify.sh blocker "ignored"; echo "exit=$?"
```

---

## Task 2: Validate `kind` argument and check `jq` dependency

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Replace `main` with arg validation + jq dep check**

Replace the body of `main()` (and add a `usage()` helper above it) so the file reads:

```bash
#!/usr/bin/env bash
# notify.sh <kind> "<reason>"
#   kind ∈ {blocker, guidance, done}
# Sends a Telegram ping to Louis. See SKILL.md for trigger rules.
set -euo pipefail

ENV_FILE="$HOME/.claude/channels/telegram/.env"
ACCESS_FILE="$HOME/.claude/channels/telegram/access.json"

usage() {
  printf 'usage: notify.sh <blocker|guidance|done> "<reason>"\n' >&2
}

main() {
  if [ "$#" -ne 2 ]; then
    usage
    return 1
  fi
  local kind="$1"
  local reason="$2"
  case "$kind" in
    blocker|guidance|done) ;;
    *)
      printf 'unknown kind: %s\n' "$kind" >&2
      usage
      return 1
      ;;
  esac
  if ! command -v jq >/dev/null 2>&1; then
    printf 'jq required — install via apt/brew\n' >&2
    return 5
  fi
  printf 'kind=%s reason=%s\n' "$kind" "$reason"
}

main "$@"
```

- [ ] **Step 2: Verify arg validation and jq check**

```bash
# bad arg count → exit 1
~/.claude/skills/notify-louis/notify.sh; echo "exit=$?"

# bad kind → exit 1
~/.claude/skills/notify-louis/notify.sh bogus "x"; echo "exit=$?"

# good args (jq present on this machine) → exit 0, prints kind/reason
~/.claude/skills/notify-louis/notify.sh blocker "test reason"; echo "exit=$?"
```

Expected:
- Empty call: stderr usage line, exit `1`.
- `bogus`: stderr `unknown kind: bogus` + usage line, exit `1`.
- Good call: stdout `kind=blocker reason=test reason`, exit `0`.

---

## Task 3: Load Telegram bot token from `.env`

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Add token-loading function and wire it into `main`**

Insert this function above `main()`:

```bash
load_token() {
  if [ ! -f "$ENV_FILE" ]; then
    printf 'telegram not configured — run /telegram:configure\n' >&2
    return 2
  fi
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    printf 'telegram not configured — run /telegram:configure\n' >&2
    return 2
  fi
  printf '%s' "$TELEGRAM_BOT_TOKEN"
}
```

In `main()`, after the jq check, replace the final `printf` with:

```bash
  local token
  token="$(load_token)"
  printf 'token loaded (len=%d)\n' "${#token}"
```

- [ ] **Step 2: Verify token-loading paths**

```bash
# happy path
~/.claude/skills/notify-louis/notify.sh blocker "x"; echo "exit=$?"

# missing .env
mv ~/.claude/channels/telegram/.env ~/.claude/channels/telegram/.env.bak
~/.claude/skills/notify-louis/notify.sh blocker "x"; echo "exit=$?"
mv ~/.claude/channels/telegram/.env.bak ~/.claude/channels/telegram/.env

# .env present but token unset
mv ~/.claude/channels/telegram/.env ~/.claude/channels/telegram/.env.bak
printf 'OTHER_VAR=foo\n' > ~/.claude/channels/telegram/.env
~/.claude/skills/notify-louis/notify.sh blocker "x"; echo "exit=$?"
mv ~/.claude/channels/telegram/.env.bak ~/.claude/channels/telegram/.env
```

Expected:
- Happy path: stdout `token loaded (len=N)` (N>40 typically), exit `0`.
- Missing `.env`: stderr `telegram not configured — run /telegram:configure`, exit `2`.
- Token unset: same stderr message, exit `2`.

---

## Task 4: Resolve chat_id from `access.json`

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Add chat_id resolver and wire it in**

Add above `main()`:

```bash
load_chat_id() {
  if [ ! -f "$ACCESS_FILE" ]; then
    printf 'telegram allowlist empty — run /telegram:access pair\n' >&2
    return 3
  fi
  local chat_id
  chat_id="$(jq -r '.allowFrom[0] // empty' "$ACCESS_FILE")"
  if [ -z "$chat_id" ]; then
    printf 'telegram allowlist empty — run /telegram:access pair\n' >&2
    return 3
  fi
  printf '%s' "$chat_id"
}
```

In `main()`, replace the temporary `printf 'token loaded …'` line with:

```bash
  local chat_id
  chat_id="$(load_chat_id)"
  printf 'token=len:%d chat_id=%s\n' "${#token}" "$chat_id"
```

- [ ] **Step 2: Verify chat_id resolution paths**

```bash
# happy path
~/.claude/skills/notify-louis/notify.sh blocker "x"; echo "exit=$?"

# missing access.json
mv ~/.claude/channels/telegram/access.json ~/.claude/channels/telegram/access.json.bak
~/.claude/skills/notify-louis/notify.sh blocker "x"; echo "exit=$?"
mv ~/.claude/channels/telegram/access.json.bak ~/.claude/channels/telegram/access.json

# empty allowlist
cp ~/.claude/channels/telegram/access.json ~/.claude/channels/telegram/access.json.bak
jq '.allowFrom = []' ~/.claude/channels/telegram/access.json.bak > ~/.claude/channels/telegram/access.json
~/.claude/skills/notify-louis/notify.sh blocker "x"; echo "exit=$?"
mv ~/.claude/channels/telegram/access.json.bak ~/.claude/channels/telegram/access.json
```

Expected:
- Happy path: stdout `token=len:N chat_id=6194225401`, exit `0`.
- Missing file: stderr `telegram allowlist empty — run /telegram:access pair`, exit `3`.
- Empty allowlist: same stderr, exit `3`.

---

## Task 5: Gather tmux + cwd context

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Add context gatherers**

Add above `main()`:

```bash
get_session_window() {
  # Returns "session:window" if inside tmux, empty string otherwise.
  if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
    tmux display-message -p '#S:#I' 2>/dev/null || true
  fi
}

get_project() {
  basename "$PWD"
}
```

In `main()`, replace the temporary `printf 'token=…'` line with:

```bash
  local session_window project
  session_window="$(get_session_window)"
  project="$(get_project)"
  printf 'token=len:%d chat_id=%s sw=%q project=%s\n' \
    "${#token}" "$chat_id" "$session_window" "$project"
```

- [ ] **Step 2: Verify context inside and outside tmux**

```bash
# Inside tmux (run from a tmux pane):
~/.claude/skills/notify-louis/notify.sh blocker "x"

# Outside tmux:
env -u TMUX ~/.claude/skills/notify-louis/notify.sh blocker "x"
```

Expected:
- Inside: prints `sw=<session>:<idx> project=Setup` (or whatever the cwd basename is).
- Outside: prints `sw='' project=Setup` (empty `session_window`).

---

## Task 6: Compose the message

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Add message composer and wire it in**

Add above `main()`:

```bash
compose_message() {
  local kind="$1" reason="$2" session_window="$3" project="$4" cwd="$5"
  local emoji label asks
  case "$kind" in
    blocker)  emoji='🚫'; label='Blocker';         asks=1 ;;
    guidance) emoji='❓'; label='Guidance needed'; asks=1 ;;
    done)     emoji='✅'; label='Done';            asks=0 ;;
  esac

  printf '%s %s — %s\n' "$emoji" "$label" "$project"
  printf '%s\n' "$reason"
  if [ "$asks" -eq 1 ]; then
    printf '\nWhat I need from you: '
    case "$kind" in
      blocker)  printf 'reply with what I asked for, or tell me to abort.\n' ;;
      guidance) printf 'pick a direction or weigh in.\n' ;;
    esac
  fi
  printf '\n'
  if [ -n "$session_window" ]; then
    printf "ta '%s'\n" "$session_window"
  fi
  printf 'cd %s\n' "$cwd"
}
```

In `main()`, replace the diagnostic `printf` line with:

```bash
  local message
  message="$(compose_message "$kind" "$reason" "$session_window" "$project" "$PWD")"
  printf '%s\n' "$message"
```

- [ ] **Step 2: Verify message composition for all three kinds**

```bash
# blocker (inside tmux)
~/.claude/skills/notify-louis/notify.sh blocker "Need GitHub OAuth token to push PR branch."

# guidance
~/.claude/skills/notify-louis/notify.sh guidance "Two equally valid migration paths — pick one."

# done
~/.claude/skills/notify-louis/notify.sh done "Refactor finished, all tests green."

# done outside tmux (no ta line)
env -u TMUX ~/.claude/skills/notify-louis/notify.sh done "Outside tmux."
```

Expected:
- Blocker output begins with `🚫 Blocker — <project>`, includes `What I need from you: reply with what I asked for, or tell me to abort.`, ends with `ta '<sw>'` and `cd <pwd>` lines.
- Guidance output begins with `❓ Guidance needed — <project>`, includes `What I need from you: pick a direction or weigh in.`.
- Done output begins with `✅ Done — <project>`, has **no** `What I need from you` line, but **does** include the `ta` + `cd` trailer.
- Done outside tmux: trailer has only `cd <pwd>`, no `ta` line.

---

## Task 7: Add dry-run mode

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Branch on `NOTIFY_DRY_RUN` after composing the message**

In `main()`, after the `printf '%s\n' "$message"` line from Task 6, add:

```bash
  if [ "${NOTIFY_DRY_RUN:-0}" = "1" ]; then
    return 0
  fi
```

…and remove the now-redundant `printf '%s\n' "$message"` (we'll re-print only in dry-run). The final structure:

```bash
  local message
  message="$(compose_message "$kind" "$reason" "$session_window" "$project" "$PWD")"
  if [ "${NOTIFY_DRY_RUN:-0}" = "1" ]; then
    printf '%s\n' "$message"
    return 0
  fi
  # (Task 8 will add the curl POST here.)
  printf 'TODO: send via curl\n' >&2
  return 99
```

- [ ] **Step 2: Verify dry-run behavior**

```bash
NOTIFY_DRY_RUN=1 ~/.claude/skills/notify-louis/notify.sh blocker "dry-run test"; echo "exit=$?"
~/.claude/skills/notify-louis/notify.sh blocker "no dry-run"; echo "exit=$?"
```

Expected:
- Dry-run: prints the full composed message to stdout, exit `0`, no network activity.
- Without dry-run: stderr `TODO: send via curl`, exit `99` (placeholder for Task 8).

---

## Task 8: POST to Telegram Bot API

**Files:**
- Modify: `~/.claude/skills/notify-louis/notify.sh`

- [ ] **Step 1: Replace the placeholder send with real curl**

Replace the `TODO: send via curl` block in `main()` with:

```bash
  local response http_code body
  response="$(curl --silent --show-error \
    --write-out '\n%{http_code}' \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "text=${message}" \
    "https://api.telegram.org/bot${token}/sendMessage")" || {
    printf 'curl failed\n' >&2
    return 4
  }
  http_code="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [ "$http_code" != "200" ]; then
    printf 'telegram API error (HTTP %s): %s\n' "$http_code" "$body" >&2
    return 4
  fi
```

- [ ] **Step 2: Send a real test message**

```bash
~/.claude/skills/notify-louis/notify.sh blocker "Test ping from notify.sh — please ignore."; echo "exit=$?"
```

Expected:
- Exit `0`.
- A Telegram message arrives on Louis's phone matching the composed format.
- The `ta '<session>:<window>'` line in the message corresponds to the tmux session/window the command was run from. Manually run that command in a fresh terminal to confirm it attaches correctly.

- [ ] **Step 3: Verify API-error path with a bogus token**

```bash
TELEGRAM_BOT_TOKEN=invalid_token \
  bash -c '
    set -e
    cp ~/.claude/channels/telegram/.env ~/.claude/channels/telegram/.env.bak
    printf "TELEGRAM_BOT_TOKEN=invalid\n" > ~/.claude/channels/telegram/.env
    ~/.claude/skills/notify-louis/notify.sh blocker "should fail" || echo "exit=$?"
    mv ~/.claude/channels/telegram/.env.bak ~/.claude/channels/telegram/.env
  '
```

Expected: stderr `telegram API error (HTTP 401): {...}`, exit `4`. `.env` restored afterwards.

---

## Task 9: Write `SKILL.md`

**Files:**
- Create: `~/.claude/skills/notify-louis/SKILL.md`

- [ ] **Step 1: Write the SKILL.md file**

```bash
cat > ~/.claude/skills/notify-louis/SKILL.md <<'MD'
---
name: notify-louis
description: Use when stuck on a blocker that needs Louis's action (credential, decision, manual step), when ≥2 reasonable paths require Louis's call, OR when Louis explicitly said "notify me when done" earlier in the conversation. Sends a Telegram message and ends the turn for blockers/guidance.
---

# notify-louis

Ping Louis on Telegram for the narrow set of moments where the session genuinely cannot proceed — or when he explicitly asked to be told when long work finished. Everything else stays in the terminal.

## When to fire

Fire **only** for these three cases:

- **🚫 blocker** — cannot proceed without Louis. Examples: OAuth token / password / credential needed; physical action required (plug in a device, flip a switch); destructive op awaiting approval; external resource locked by another process.
- **❓ guidance** — there are ≥2 reasonable paths and the choice would meaningfully reshape the work. Not for minor style preferences or questions Claude can answer itself.
- **✅ done** — only when Louis explicitly said "notify me when done" / "ping me when it's ready" / similar earlier in the conversation. **No implicit detection** of "this seems like a long task."

## When NOT to fire

- Routine permission prompts when there's an active terminal session — the prompt already shows up where Louis is.
- Questions Claude can answer itself.
- Every multi-step task, just because it has multiple steps.
- Mid-work status updates.

## How to fire

Run the helper script with one of `blocker`, `guidance`, or `done` and a one-line reason:

```bash
~/.claude/skills/notify-louis/notify.sh blocker "Need GitHub OAuth token to push the PR branch."
~/.claude/skills/notify-louis/notify.sh guidance "Two valid migration paths — destructive vs additive. Which?"
~/.claude/skills/notify-louis/notify.sh done "Refactor complete, 47/47 tests green, ready to review."
```

The script gathers tmux session/window and cwd automatically and includes a `ta '<session>:<window>'` line so Louis can jump straight back into the right pane.

## After firing

- After **🚫 blocker** or **❓ guidance** — end the turn. You're blocked by definition; don't keep working on assumptions.
- After **✅ done** — continue or end naturally; the ping is informational.

## Failure modes

The script exits non-zero if Telegram is misconfigured or the API call fails:

| exit | meaning |
|------|---------|
| 1    | bad/missing args |
| 2    | telegram not configured (no `.env` or token unset) — run `/telegram:configure` |
| 3    | empty allowlist — run `/telegram:access pair` |
| 4    | Telegram API error (full JSON on stderr) |
| 5    | `jq` missing |

If the script exits non-zero, **don't pretend the ping went through**. Surface the failure to Louis in the terminal instead.

## Dry run

Set `NOTIFY_DRY_RUN=1` to print the composed message to stdout without hitting the network. Useful for iterating on wording.
MD
```

- [ ] **Step 2: Sanity-check the file**

```bash
ls -la ~/.claude/skills/notify-louis/
head -5 ~/.claude/skills/notify-louis/SKILL.md
```

Expected: `SKILL.md` (~2KB) and `notify.sh` (executable) both present. First five lines of `SKILL.md` are the frontmatter block.

---

## Task 10: End-to-end verification

- [ ] **Step 1: Run the spec's manual test matrix**

From spec section 6:

```bash
# 1. Blocker inside tmux → message includes ta line
~/.claude/skills/notify-louis/notify.sh blocker "manual e2e: blocker inside tmux"

# 2. Done outside tmux → message has no ta line
env -u TMUX ~/.claude/skills/notify-louis/notify.sh done "manual e2e: done outside tmux"

# 3. Dry-run guidance → stdout payload, no send
NOTIFY_DRY_RUN=1 ~/.claude/skills/notify-louis/notify.sh guidance "manual e2e: dry-run guidance"

# 4. Missing .env → exit 2
mv ~/.claude/channels/telegram/.env ~/.claude/channels/telegram/.env.bak
~/.claude/skills/notify-louis/notify.sh blocker "x" || echo "exit=$?"
mv ~/.claude/channels/telegram/.env.bak ~/.claude/channels/telegram/.env

# 5. Bogus kind → exit 1
~/.claude/skills/notify-louis/notify.sh bogus "x" || echo "exit=$?"
```

Expected:
- Test 1: Telegram delivery, message starts `🚫 Blocker — Setup`, ends with `ta '<session>:<window>'` and `cd <pwd>`.
- Test 2: Telegram delivery, message starts `✅ Done — Setup`, no `ta` line, only `cd`.
- Test 3: stdout shows full payload starting `❓ Guidance needed —`, no message arrives on phone.
- Test 4: stderr `telegram not configured — run /telegram:configure`, exit `2`.
- Test 5: stderr `unknown kind: bogus` + usage, exit `1`.

- [ ] **Step 2: Confirm the `ta` paste-back works**

In a fresh terminal (outside the current tmux session), copy the `ta '<session>:<window>'` line from one of the delivered messages and run it. Expected: tmux attaches and selects the correct window.

- [ ] **Step 3: Trigger from the Skill tool**

In a separate Claude Code session, manually invoke the skill end-to-end (e.g., ask Claude to "ping me with a test blocker"). Confirm Claude finds the skill, runs the script, and the message arrives.

---

## Task 11: Commit the plan

**Files:**
- Modify: `~/Setup/docs/superpowers/plans/2026-05-05-notify-louis.md` (this file, already created)

The skill files themselves (`SKILL.md`, `notify.sh`) live under `~/.claude/skills/notify-louis/` and are not tracked by any git repo — local config, like `~/.zshrc` overrides. If Louis later decides to track them in his dotfiles, that's a separate change.

- [ ] **Step 1: Commit this plan to the Setup repo**

```bash
cd ~/Setup
git add docs/superpowers/plans/2026-05-05-notify-louis.md
git commit -m "$(cat <<'EOF'
plan: notify-louis telegram notification skill

Implementation plan for a global Claude Code skill that pings Louis
on Telegram for blockers, guidance requests, and explicit "notify me
when done" handoffs. Helper script gathers tmux/cwd context and
POSTs directly to the Telegram Bot API.
EOF
)"
```

Expected: clean commit on `master`, only the plan file added.

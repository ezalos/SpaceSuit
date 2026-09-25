#!/usr/bin/env bash
# ABOUTME: notify.sh — sends a Telegram ping to Louis for blockers, guidance requests, and done handoffs.
# ABOUTME: Usage: notify.sh [--session <id>] <blocker|guidance|done> "<reason>"
#   kind ∈ {blocker, guidance, done}
# Sends a Telegram ping to Louis. See SKILL.md for trigger rules.
set -euo pipefail

ENV_FILE="$HOME/.claude/channels/telegram/.env"
ACCESS_FILE="$HOME/.claude/channels/telegram/access.json"

usage() {
  printf 'usage: notify.sh [--session <id>] <blocker|guidance|done> "<reason>"\n' >&2
}

load_token() {
  # A token already in the environment wins: `secrets run --only TELEGRAM_BOT_TOKEN -- notify.sh ...` supplies it
  # from the vault, so no plaintext .env is needed (the Lighthouse seat has none).
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    printf '%s' "$TELEGRAM_BOT_TOKEN"
    return 0
  fi
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

compose_message() {
  local kind="$1" reason="$2" session_window="$3" project="$4" cwd="$5" session_id="$6"
  local emoji label asks
  case "$kind" in
    blocker)  emoji='🚫'; label='Blocker';         asks=1 ;;
    guidance) emoji='❓'; label='Guidance needed'; asks=1 ;;
    done)     emoji='✅'; label='Done';            asks=0 ;;
  esac

  # The session's 8-char id opens the first line: a Telegram reply reaches Seven
  # without its target, so this is how Seven routes the answer back (claude-session).
  printf '%s ' "$session_id"
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
  printf "cd '%s'\n" "$cwd"
}

get_session_window() {
  # Returns "session:window" if inside tmux, empty string otherwise.
  if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
    tmux display-message -p '#S:#I' 2>/dev/null || true
  fi
}

get_session_id() {
  # First 8 chars of the Claude Code session UUID — what Louis's status line shows.
  # --session wins over the env Claude Code sets; without either, refuse to send:
  # a notification Seven cannot route back is a reply Louis loses.
  local id="${1:-${CLAUDE_CODE_SESSION_ID:-}}"
  id="${id:0:8}"
  if ! [[ "$id" =~ ^[0-9a-f]{8}$ ]]; then
    printf 'session id missing or invalid (%s). Get it with:\n' "${id:-empty}" >&2
    printf '  jq -r .sessionId ~/.claude/sessions/$CLAUDE_PID.json\n' >&2
    printf 'then rerun: notify.sh --session <that id> <kind> "<reason>"\n' >&2
    return 6
  fi
  printf '%s' "$id"
}

get_project() {
  basename "$PWD"
}

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

main() {
  local session_arg=""
  if [ "${1:-}" = "--session" ]; then
    session_arg="${2:-}"
    shift 2 || { usage; return 1; }
  fi
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
  local session_window project session_id
  session_window="$(get_session_window)"
  project="$(get_project)"
  session_id="$(get_session_id "$session_arg")" || return 6
  local message
  message="$(compose_message "$kind" "$reason" "$session_window" "$project" "$PWD" "$session_id")"
  if [ "${NOTIFY_DRY_RUN:-0}" = "1" ]; then
    printf '%s\n' "$message"
    return 0
  fi
  if ! command -v jq >/dev/null 2>&1; then
    printf 'jq required — install via apt/brew\n' >&2
    return 5
  fi
  local token
  token="$(load_token)"
  local chat_id
  chat_id="$(load_chat_id)"
  # Message convention (GroundControl docs/naming.md, "Telegram messages"):
  # agent emoji by the repo this session works in, [claude] as the service.
  local agent_emoji="🧑‍🚀"
  case "$PWD" in
    *SevenLeagues*) agent_emoji="⛸️" ;;
    *GroundControl*) agent_emoji="🖥️" ;;
  esac
  local response http_code body
  # The URL carries the token, so it goes to curl on STDIN (--config -), written by printf, a shell builtin that starts
  # no process: an argument would be readable by every local user through `ps` / /proc/<pid>/cmdline.
  response="$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" | curl --silent --show-error \
    --config - \
    --write-out '\n%{http_code}' \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "text=${agent_emoji} [claude] ${message}")" || {
    printf 'curl failed\n' >&2
    return 4
  }
  http_code="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [ "$http_code" != "200" ]; then
    printf 'telegram API error (HTTP %s): %s\n' "$http_code" "$body" >&2
    return 4
  fi
}

main "$@"

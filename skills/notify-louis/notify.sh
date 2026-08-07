#!/usr/bin/env bash
# ABOUTME: notify.sh — sends a Telegram ping to Louis for blockers, guidance requests, and done handoffs.
# ABOUTME: Usage: notify.sh <blocker|guidance|done> "<reason>"
#   kind ∈ {blocker, guidance, done}
# Sends a Telegram ping to Louis. See SKILL.md for trigger rules.
set -euo pipefail

ENV_FILE="$HOME/.claude/channels/telegram/.env"
ACCESS_FILE="$HOME/.claude/channels/telegram/access.json"

usage() {
  printf 'usage: notify.sh <blocker|guidance|done> "<reason>"\n' >&2
}

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
  printf "cd '%s'\n" "$cwd"
}

get_session_window() {
  # Returns "session:window" if inside tmux, empty string otherwise.
  if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
    tmux display-message -p '#S:#I' 2>/dev/null || true
  fi
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
  local session_window project
  session_window="$(get_session_window)"
  project="$(get_project)"
  local message
  message="$(compose_message "$kind" "$reason" "$session_window" "$project" "$PWD")"
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
}

main "$@"

#!/usr/bin/env bash
# ABOUTME: PreToolUse guard: blocks anything that could transmit a message AS Louis.
# ABOUTME: Outbound email/messages are Louis-only; agents draft, never send. (Incident 2026-08-11.)
set -uo pipefail

payload=$(cat)
tool=$(printf '%s' "$payload" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0

block() {
  echo "BLOCKED by outbound-comms guard: $1 Outbound email/messages are Louis-only — prepare a draft and hand it to him (CLAUDE.md 'External comms'; incident 2026-08-11)." >&2
  exit 2
}

case "$tool" in
  mcp__beeper__send_message)
    block "sending a Beeper message transmits as Louis."
    ;;
  Skill)
    skill=$(printf '%s' "$payload" | jq -r '.tool_input.skill // empty' 2>/dev/null)
    [ "$skill" = "send-email" ] && block "the send-email skill transmits email as Louis."
    exit 0
    ;;
  Bash)
    cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null)
    # Vectors: the send-email CLI (invocation-position, any /path form, or the
    # flagged form real sends always use), classic MTAs/senders, raw SMTP, python
    # smtplib, the Proton SMTP host. Invocation-position matching (not bare word)
    # keeps mentions like `git log --grep send-email` unblocked — the 2026-08-11
    # incident hook taught us real sends always carry flags.
    if printf '%s' "$cmd" | grep -qiE \
      '(^[[:space:]]*|[;&|(][[:space:]]*)[^[:space:]]*/?send-email([[:space:]]|$)|send-email[[:space:]]+--?(to|subject|body|cc|bcc|attach)|(^|[[:space:];&|(])(sendmail|msmtp|swaks|mutt)([[:space:]]|$)|smtps?://|smtp\.protonmail|smtplib|SMTP\('; then
      block "this command could transmit email/messages as Louis."
    fi
    exit 0
    ;;
esac
exit 0

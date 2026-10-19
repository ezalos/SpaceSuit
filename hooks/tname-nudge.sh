#!/usr/bin/env bash
# ABOUTME: UserPromptSubmit hook: while this session's tmux tab still has its automatic name, adds one line of
# ABOUTME: context asking the session to name it (the tname skill); silent once the tab is named or outside tmux.
set -uo pipefail

cat >/dev/null
[ -n "${TMUX_PANE:-}" ] || exit 0
# tname's rename-window turns automatic-rename off, so "on" means nobody has named this tab yet
auto=$(tmux display-message -p -t "$TMUX_PANE" '#{automatic-rename}' 2>/dev/null) || exit 0
[ "$auto" = 1 ] || exit 0
echo "This session's tmux tab has no name yet. Once you know what this session is working on, name it with the tname skill (1-2 words, 12 characters at most), then carry on."

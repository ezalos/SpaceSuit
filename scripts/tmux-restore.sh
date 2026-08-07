#!/usr/bin/env bash
# ABOUTME: Restore tmux sessions from a previous tsave snapshot.
# ABOUTME: Recreates sessions, windows, panes, layouts, and optionally resumes Claude Code.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: trestore [-c LINES] [-L SOCKET] [-p] [-b] [-h]

Restore tmux sessions from a previous tsave snapshot.

Options:
  -c LINES   Scrollback context lines shown per pane (default: 25, 0 to disable)
  -L SOCKET  Restore into an explicit tmux socket instead of the default server.
             Non-destructive: leaves a wedged/old default server untouched.
             Attach later with:  tmux -L SOCKET attach
  -p         Pick which snapshot to restore from (live save + rolling history)
  -b         Batch / non-interactive: recreate topology and RESUME every Claude
             pane, no prompts. Takes the "most recent" column without showing
             the table. You attach to already-running sessions. Set
             TMUX_RESTORE_NO_LAUNCH=1 to only pre-type the commands (no Enter)
             instead of running them.
  -h         Show this help

Without -b, a table of which conversation each pane resumes is shown: columns
are candidate policies (most recent, earlier points in time, last manual save),
each candidate labelled with its title. Pick a column with one key. The same
table is available any time as `cresume`.
EOF
  exit 0
}

SAVE_DIR="${TMUX_SAVE_DIR:-$HOME/.tmux-save}"
HISTORY_DIR="${TMUX_SAVE_HISTORY_DIR:-$HOME/.tmux-save-history}"
CONTEXT_LINES=25
TMUX_SOCKET="${TMUX_SOCKET:-}"
BATCH=0
PICK=0

while getopts ":c:L:pbh" opt; do
  case "$opt" in
    c) CONTEXT_LINES="$OPTARG" ;;
    L) TMUX_SOCKET="$OPTARG" ;;
    p) PICK=1 ;;
    b) BATCH=1 ;;
    h) usage ;;
    *) echo "Unknown option: -$OPTARG"; usage ;;
  esac
done

# Route every tmux call through the requested socket by construction, so an
# isolated restore (-L) can never leak onto the default server. This is the
# structural fix for what a bare, easily-unset TMUX_TMPDIR let slip during the
# 3.5a upgrade work: there, one un-prefixed tmux command killed the live server.
tmux() { command tmux ${TMUX_SOCKET:+-L "$TMUX_SOCKET"} "$@"; }

# Optionally choose the source snapshot interactively (live save + history).
if (( PICK )); then
  chosen=$("$(dirname "$0")/tmux-snapshots.sh" --pick) || exit 1
  [[ -n "$chosen" ]] || { echo "No snapshot chosen."; exit 1; }
  SAVE_DIR="$chosen"
fi

STATE="$SAVE_DIR/state.tsv"

if [[ ! -f "$STATE" ]]; then
  echo "No saved state found. Run tsave first."
  exit 1
fi

# An existing but EMPTY state.tsv is not an empty snapshot, it is a lost one. A
# power cut moments after a save leaves the entire directory as correctly-named
# zero-length files (the save now syncs before the swap to prevent exactly that;
# see tmux-save.sh). Treating it as valid meant restoring nothing and reporting
# "Restored 0 session(s)" with a blank timestamp - no hint that the rolling
# history still held a good snapshot from minutes earlier. Say what happened and
# point at the newest snapshot that does have panes in it.
if [[ ! -s "$STATE" ]]; then
  echo "Snapshot at $SAVE_DIR is empty: state.tsv holds no panes."
  echo "That is the signature of an unclean shutdown truncating a save."
  echo
  fallback=""
  while IFS= read -r cand; do
    [[ -s "$cand/state.tsv" ]] && { fallback="$cand"; break; }
  done < <(ls -1d "$HISTORY_DIR"/*/ 2>/dev/null | sed 's#/$##' | sort -r)

  if [[ -z "$fallback" ]]; then
    echo "No usable snapshot in $HISTORY_DIR either."
    exit 1
  fi

  fb_panes=$(wc -l < "$fallback/state.tsv" | tr -d ' ')
  fb_sessions=$(cut -f1 "$fallback/state.tsv" | sort -u | wc -l | tr -d ' ')
  fb_claude=$(awk -F'\t' '$7 == 1' "$fallback/state.tsv" | wc -l | tr -d ' ')
  echo "Newest usable snapshot: $(basename "$fallback")"
  echo "  saved   $(cat "$fallback/saved_at" 2>/dev/null || echo '?')"
  echo "  holds   $fb_sessions session(s), $fb_panes pane(s), $fb_claude with Claude Code"
  echo
  echo "Restore it with either:"
  echo "  trestore -p                                  # pick it from the table"
  echo "  TMUX_SAVE_DIR=$fallback trestore"
  exit 1
fi

# Show the tail of saved scrollback in a pane for context
show_pane_context() {
  local target="$1" session="$2" win_idx="$3" pane_idx="$4"
  (( CONTEXT_LINES > 0 )) || return 0
  local safe_session="${session//\//__}"
  local scrollback="$SAVE_DIR/pane_contents/${safe_session}_${win_idx}_${pane_idx}.txt"
  [[ -f "$scrollback" ]] || return 0
  # Strip trailing blank lines (tmux capture includes empty pane area), then tail
  tmux send-keys -t "$target" \
    "echo '── saved scrollback ──' && sed -e :a -e '/^[[:space:]]*\$/{\$d;N;ba' -e '}' '$scrollback' | tail -n $CONTEXT_LINES && echo '─────────────────────'" Enter
}

echo "Restoring from $(cat "$SAVE_DIR/saved_at")..."

# Warm up the server before the restore loop. The very first `new-session` on a
# freshly-started tmux (e.g. right after a binary upgrade) can race server startup
# and report "server exited unexpectedly", losing just that one session - it cost
# curriculumvitae a restore on the 3.2a -> 3.5a cutover. Create and kill a throwaway
# session first (retrying the exact racy op) so the real restore hits a warm server.
warmed=0
for _ in 1 2 3 4 5; do
  if tmux new-session -d -s "__trestore_warmup__" -c "$HOME" 2>/dev/null; then
    tmux kill-session -t "__trestore_warmup__" 2>/dev/null || true
    warmed=1
    break
  fi
  sleep 0.3 2>/dev/null || true
done
(( warmed )) || echo "  (warning: tmux server did not warm up cleanly; continuing)"

# Session names with these characters break tmux target parsing or are
# unresolved template variables (e.g. VS Code's ${workspaceFolder}).
is_bad_session_name() {
  [[ "$1" =~ [\$\\\{\}] ]]
}

prev_session=""
prev_win=""
skip_session=""
first_win_of_session=""
pane_created_count=0
active_windows=()

while IFS=$'\t' read -r session win_idx win_name win_layout pane_idx pane_dir is_claude win_active claude_session_id; do

  # Skip all lines belonging to a session we couldn't create or already exists
  [[ "$session" == "$skip_session" ]] && continue

  # --- New session ---
  if [[ "$session" != "$prev_session" ]]; then
    skip_session=""
    if is_bad_session_name "$session"; then
      echo "  SKIP: session '$session' has invalid characters"
      skip_session="$session"
      prev_session="$session"
      continue
    fi
    if tmux has-session -t "$session" 2>/dev/null; then
      echo "  SKIP: session '$session' already exists"
      skip_session="$session"
      prev_session="$session"
      continue
    fi

    # Capture tmux's stderr instead of discarding it. The real failure here is
    # almost never the name (those are validated above) - it is the server
    # ('server exited unexpectedly', 'protocol version mismatch (client N,
    # server M)', ...). Masking it once sent a whole debugging session chasing a
    # phantom 'invalid name' problem.
    if ! new_err=$(tmux new-session -d -s "$session" -c "$pane_dir" 2>&1); then
      echo "  SKIP: cannot create session '$session': ${new_err:-unknown tmux error}"
      skip_session="$session"
      prev_session="$session"
      continue
    fi

    # Move the auto-created window to the correct index if it differs
    auto_win_idx=$(tmux list-windows -t "$session" -F '#{window_index}' | head -1)
    if [[ "$auto_win_idx" != "$win_idx" ]]; then
      tmux move-window -s "$session:$auto_win_idx" -t "$session:$win_idx"
    fi
    tmux rename-window -t "$session:$win_idx" "$win_name"

    first_win_of_session="$win_idx"
    prev_session="$session"
    prev_win="$win_idx"
    pane_created_count=1

    # First pane was created with the session — position it
    tmux send-keys -t "$session:$win_idx.$pane_idx" "cd '${pane_dir}' && clear" Enter
    show_pane_context "$session:$win_idx.$pane_idx" "$session" "$win_idx" "$pane_idx"

    [[ "$win_active" == "1" ]] && active_windows+=("$session:$win_idx")

    tmux select-layout -t "$session:$win_idx" "$win_layout" 2>/dev/null
    continue
  fi

  # --- New window within existing session ---
  if [[ "$win_idx" != "$prev_win" ]]; then
    tmux new-window -t "$session:$win_idx" -n "$win_name" -c "$pane_dir"
    prev_win="$win_idx"
    pane_created_count=1

    [[ "$win_active" == "1" ]] && active_windows+=("$session:$win_idx")
  else
    # --- Additional pane (split) within current window ---
    tmux split-window -t "$session:$win_idx" -c "$pane_dir"
    pane_created_count=$((pane_created_count + 1))
  fi

  # Position the pane
  tmux send-keys -t "$session:$win_idx.$pane_idx" "cd '${pane_dir}' && clear" Enter
  show_pane_context "$session:$win_idx.$pane_idx" "$session" "$win_idx" "$pane_idx"


  # Reapply layout after each pane so geometry stays correct
  tmux select-layout -t "$session:$win_idx" "$win_layout" 2>/dev/null

done < "$STATE"

# Select the window that was active in each session
if [[ ${#active_windows[@]} -gt 0 ]]; then
  for aw in "${active_windows[@]}"; do
    tmux select-window -t "$aw" 2>/dev/null
  done
fi

# Summary
total_sessions=$(cut -f1 "$STATE" | sort -u | wc -l)
echo "Restored $total_sessions session(s)."
echo "Scrollback captures available in: $SAVE_DIR/pane_contents/"

# --- Claude conversation resumption ---
#
# Delegated to the claude_resume package. The logic it replaced picked each
# pane's conversation by mtime in state.tsv order without checking which pane
# had held it, so panes sharing a directory traded conversations and a
# tool-spawned session could take a real pane's slot. That is what scrambled
# the 2026-08-03 crash restore.
# See docs/plans/2026-08-03-claude-resume-table-design.md.
#
# Bare python3, not uv: this runs on the restore path, possibly right after a
# reboot, and the package is stdlib-only so it needs no venv. tmux-save.sh takes
# the same approach for the same reason.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
resume_args=(--snapshot "$SAVE_DIR")
[[ -n "$TMUX_SOCKET" ]] && resume_args+=(--socket "$TMUX_SOCKET")
(( BATCH )) && resume_args+=(--batch)
[[ -n "${TMUX_RESTORE_NO_LAUNCH:-}" ]] && resume_args+=(--no-launch)

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m claude_resume "${resume_args[@]}" || \
  echo "  (claude_resume failed; panes left at a shell. Re-run with: cresume)"

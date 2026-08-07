#!/usr/bin/env bash
# ABOUTME: Save all tmux sessions, windows, panes, and working directories to disk.
# ABOUTME: Detects active Claude Code sessions for automatic resumption after reboot.

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: tsave [-L SOCKET] [--origin manual|cron|shutdown] [-h]

Save all tmux sessions to ~/.tmux-save/ (run before reboot).
  -L SOCKET  Save from an explicit tmux socket instead of the default server
             (for isolated tests; never touches the default server).
  --origin   Record who took this snapshot. Defaults to `manual` when run from
             a terminal and `cron` otherwise. The resume table offers the last
             `manual` snapshot as a column, so hand-taken checkpoints before a
             reboot are worth marking. Shown by `tsnaps --list`.
Restore with: trestore [-c LINES]
EOF
  exit 0
fi

# Tools installed under the user prefix (notably `rip`) must resolve even when
# this script runs from cron or the systemd shutdown unit, whose PATH is minimal
# (`/usr/bin:/bin`). Without this, every non-interactive save died at
# `rip: command not found` and nothing was ever saved.
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

# Optional: target an explicit tmux socket (-L NAME) so an isolated test save can
# never read the default server. Routed through a wrapper so every tmux call below
# picks up the flag by construction (no per-call leak like a bare TMUX_TMPDIR).
TMUX_SOCKET="${TMUX_SOCKET:-}"
ORIGIN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -L) TMUX_SOCKET="${2:-}"; shift 2 || true ;;
    --origin) ORIGIN="${2:-}"; shift 2 || true ;;
    *) shift ;;
  esac
done

# Provenance of this snapshot, so the resume table can offer "the checkpoint you
# took by hand" as a column. Explicit flag wins; otherwise a tty means a human
# typed `tsave`, and no tty means cron or the shutdown unit.
if [[ -z "$ORIGIN" ]]; then
  if [[ -t 0 ]]; then ORIGIN="manual"; else ORIGIN="cron"; fi
fi
case "$ORIGIN" in
  manual|cron|shutdown) ;;
  *) echo "Unknown --origin '$ORIGIN' (expected manual, cron or shutdown)" >&2; exit 2 ;;
esac

tmux() { command tmux ${TMUX_SOCKET:+-L "$TMUX_SOCKET"} "$@"; }

SAVE_DIR="${TMUX_SAVE_DIR:-$HOME/.tmux-save}"
SAVE_LOG="${TMUX_SAVE_LOG:-$HOME/.tmux-save.log}"
STAGING_DIR="${SAVE_DIR}.staging"

# Bail BEFORE touching the existing save if there is nothing to capture. Wiping
# the old snapshot first (the previous behaviour) meant a run with no server -
# e.g. a cron tick or the shutdown unit firing while tmux was already down -
# destroyed the last good save and wrote nothing in its place.
if ! tmux list-sessions &>/dev/null; then
  echo "No tmux server running."
  exit 1
fi

# Build the new snapshot in a staging dir, then swap it into place only once it
# is complete (see end of script). A failure partway through therefore leaves
# the previous good save untouched.
[[ -d "$STAGING_DIR" ]] && rip "$STAGING_DIR"
mkdir -p "$STAGING_DIR/pane_contents"

# Skip sessions whose names contain characters that will break tmux target
# parsing or are clearly unresolved template variables (e.g. VS Code's
# ${workspaceFolder}).
is_bad_session_name() {
  [[ "$1" =~ [\$\\\{\}] ]]
}

# Save each session → window → pane
tmux list-sessions -F '#{session_name}' | while read -r session; do
  if is_bad_session_name "$session"; then
    echo "  SKIP (bad name): $session"
    continue
  fi
  tmux list-windows -t "$session" -F '#{window_index}|#{window_name}|#{window_layout}|#{window_active}' \
  | while IFS='|' read -r win_idx win_name win_layout win_active; do
    tmux list-panes -t "$session:$win_idx" \
      -F '#{pane_index}|#{pane_current_path}|#{pane_pid}|#{pane_tty}' \
    | while IFS='|' read -r pane_idx pane_dir pane_pid pane_tty; do
      # Detect Claude Code via the pane's tty: `ps -t` lists every process on that
      # terminal, at any depth, on both Linux and macOS. The previous approach
      # (`ps -g` + GNU-only `ps --ppid`) saw only the pane shell on macOS, so no
      # Claude pane was ever detected there (every save reported "0 with Claude").
      is_claude=0
      claude_session_id=""
      claude_pid=$(ps -t "${pane_tty#/dev/}" -o pid=,comm= 2>/dev/null \
        | awk '$2 == "claude" || $2 ~ /\/claude$/ {print $1; exit}')
      if [[ -n "$claude_pid" ]]; then
        is_claude=1
        # Claude Code maintains ~/.claude/sessions/<pid>.json with its sessionId
        if [[ -f "$HOME/.claude/sessions/$claude_pid.json" ]]; then
          claude_session_id=$(python3 -c \
            "import json; print(json.load(open('$HOME/.claude/sessions/$claude_pid.json'))['sessionId'])" \
            2>/dev/null || true)
        fi
      fi

      # Write metadata line (9 columns: session, win_idx, win_name, win_layout,
      #   pane_idx, pane_dir, is_claude, win_active, claude_session_id)
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$session" "$win_idx" "$win_name" "$win_layout" \
        "$pane_idx" "$pane_dir" "$is_claude" "$win_active" "$claude_session_id" \
        >> "$STAGING_DIR/state.tsv"

      # Capture scrollback (last 10k lines)
      # Sanitize session name for safe filenames (sessions can contain slashes)
      safe_session="${session//\//__}"
      tmux capture-pane -t "$session:$win_idx.$pane_idx" -p -S -10000 \
        > "$STAGING_DIR/pane_contents/${safe_session}_${win_idx}_${pane_idx}.txt" 2>/dev/null
    done
  done
done

date '+%Y-%m-%d %H:%M:%S' > "$STAGING_DIR/saved_at"
# Into the staging dir so the marker lands atomically with the snapshot; the
# later `cp -a` into history carries it along with no extra work.
printf '%s\n' "$ORIGIN" > "$STAGING_DIR/origin"

# Force the staged snapshot onto stable storage BEFORE anything destructive.
# Everything above is plain buffered I/O, so at this point the files exist by
# name but their contents are only in page cache - ext4 has not even allocated
# blocks for them (`filefrag -v` shows `delalloc`). The shutdown save is the one
# that matters most and is also the most exposed: it runs seconds before the
# machine loses power, well inside the writeback window. On 2026-08-06 that cost
# a complete 29-pane save - written at 11:02:06, power cut at 11:02:09, and every
# file in both the save and its history copy came back 0 bytes. The only survivor
# was ~/.tmux-save.log, purely because it is written via temp-file+rename, which
# trips ext4's auto_da_alloc heuristic and forces its data out.
#
# Syncing here rather than at the end also means the previous good save is only
# destroyed once its replacement is genuinely on disk.
#
# Bare `sync`, no arguments: `sync FILE` is GNU-only and this script runs on
# macOS too. (There sync(2) schedules writeback without waiting for it, so the
# guarantee is weaker - but it is the portable barrier available.)
sync

# Snapshot is complete: swap it into place. The previous save goes to the
# graveyard (recoverable via `rip --unbury`) only now that a full new one exists.
[[ -d "$SAVE_DIR" ]] && rip "$SAVE_DIR"
mv "$STAGING_DIR" "$SAVE_DIR"

# Keep a pruned rolling history of complete snapshots, OUTSIDE the save dir (which
# is replaced wholesale each run). This lets you recover a recent-but-not-latest
# state: a long prompt lost between ticks, or a good pre-crash snapshot you only
# notice hours later.
#
# Tiered retention so a slow-to-notice crash cannot prune the last good save the way
# a flat "keep newest 5" (~1h) could:
#   - newest KEEP_RECENT snapshots outright           (default 8  = ~2h at 15-min)
#   - newest snapshot per hour, last KEEP_HOURLY hours (default 72 = 3 days)
#   - newest snapshot per day,  last KEEP_DAILY days   (default 30 = ~1 month)
# Names are YYYY-MM-DD_HH-MM-SS, so lexical sort == chronological. Pure sort/awk
# (portable to macOS bash 3.2, no bash-4 assoc arrays) and rip (recoverable) with
# || true so a pruning hiccup never aborts the save.
HISTORY_DIR="${TMUX_SAVE_HISTORY_DIR:-$HOME/.tmux-save-history}"
KEEP_RECENT="${TMUX_SAVE_KEEP_RECENT:-8}"
KEEP_HOURLY="${TMUX_SAVE_KEEP_HOURLY:-72}"
KEEP_DAILY="${TMUX_SAVE_KEEP_DAILY:-30}"
if mkdir -p "$HISTORY_DIR" && cp -a "$SAVE_DIR" "$HISTORY_DIR/$(date '+%Y-%m-%d_%H-%M-%S')"; then
  # Walk newest-first; keep the first snapshot seen in each hour/day bucket.
  keep=$(ls -1d "$HISTORY_DIR"/*/ 2>/dev/null | while read -r p; do basename "$p"; done \
    | sort -r \
    | awk -v kr="$KEEP_RECENT" -v kh="$KEEP_HOURLY" -v kd="$KEEP_DAILY" '
        { keep=0; rank++
          if (rank<=kr) keep=1
          h=substr($0,1,13)                       # YYYY-MM-DD_HH
          d=substr($0,1,10)                       # YYYY-MM-DD
          if (!(h in hs)) { hs[h]=1; if (++hn<=kh) keep=1 }
          if (!(d in ds)) { ds[d]=1; if (++dn<=kd) keep=1 }
          if (keep) print
        }')
  for dir in "$HISTORY_DIR"/*/; do
    name=$(basename "$dir")
    printf '%s\n' "$keep" | grep -qxF "$name" || rip "$dir" 2>/dev/null || true
  done
fi

# Second barrier: the swap and the history copy above are renames and fresh
# writes of their own. The data they move was already synced, but their directory
# entries have not been. Without this, a power cut in the next few seconds can
# replay to a tree where the new snapshot is still sitting in the staging dir.
sync

# Summary
if [[ -f "$SAVE_DIR/state.tsv" ]]; then
  total_panes=$(wc -l < "$SAVE_DIR/state.tsv")
  total_sessions=$(cut -f1 "$SAVE_DIR/state.tsv" | sort -u | wc -l)
  claude_panes=$(awk -F'\t' '$7 == 1' "$SAVE_DIR/state.tsv" | wc -l)
  summary="Saved $total_panes pane(s) across $total_sessions session(s) ($claude_panes with Claude Code)"
  echo "$summary"
else
  summary="No panes found to save."
  echo "$summary"
fi

# Append to persistent log (lives outside the save dir, so it survives the swap)
printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$summary" >> "$SAVE_LOG"
# Keep only last 100 lines
tail -n 100 "$SAVE_LOG" > "$SAVE_LOG.tmp" && mv "$SAVE_LOG.tmp" "$SAVE_LOG"

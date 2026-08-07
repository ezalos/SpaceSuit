#!/usr/bin/env bash
# ABOUTME: List / inspect / pick tmux-save snapshots (live save + rolling history).
# ABOUTME: Backs `trestore -p`; standalone as `tsnaps` with a dynamic scrollback preview.

set -euo pipefail

SAVE_DIR="${TMUX_SAVE_DIR:-$HOME/.tmux-save}"
HISTORY_DIR="${TMUX_SAVE_HISTORY_DIR:-$HOME/.tmux-save-history}"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

usage() {
  cat <<'EOF'
Usage: tsnaps [--list] [--pick] [--preview DIR] [--inspect DIR] [-h]

Browse the tmux-save snapshots (the live save + the rolling history).

  --list           Table of snapshots: time, #sessions, #panes, #claude (default)
  --pick           Interactively choose one; prints its directory to stdout.
                   Uses fzf (with a live per-pane scrollback preview) if present,
                   else a numbered menu.
  --preview DIR    Render one snapshot's sessions/panes + the tail of each pane's
                   captured scrollback (this is the fzf preview; also usable directly).
  --inspect DIR    Alias for --preview.
  -h               Show this help
EOF
  exit 0
}

# Counts for a snapshot dir: "<sessions> <panes> <claude>"
snap_meta() {
  local dir="$1" st="$1/state.tsv"
  [[ -f "$st" ]] || { echo "0 0 0"; return; }
  local s p c
  s=$(cut -f1 "$st" | sort -u | wc -l | tr -d ' ')
  p=$(wc -l < "$st" | tr -d ' ')
  c=$(awk -F'\t' '$7==1' "$st" | wc -l | tr -d ' ')
  echo "$s $p $c"
}

# Newest-first list of snapshot dirs: the live save first, then history.
all_snapshot_dirs() {
  [[ -f "$SAVE_DIR/state.tsv" ]] && printf '%s\n' "$SAVE_DIR"
  ls -1d "$HISTORY_DIR"/*/ 2>/dev/null | sed 's#/$##' | sort -r
}

# Provenance of a snapshot: manual (typed by hand), cron, shutdown, or '?' for
# snapshots taken before the marker existed.
snap_origin() {
  local o="$1/origin"
  [[ -f "$o" ]] && head -1 "$o" || echo "?"
}

# One pretty line for a snapshot dir.
row_label() {
  local dir="$1" tag="${2:-}" meta
  meta=$(snap_meta "$dir")
  local saved; saved=$(cat "$dir/saved_at" 2>/dev/null || echo "?")
  local origin; origin=$(snap_origin "$dir")
  # shellcheck disable=SC2086
  set -- $meta
  printf '%-19s  %-8s  %2s sess  %3s panes  %2s claude  %s%s' \
    "$saved" "$origin" "$1" "$2" "$3" "$(basename "$dir")" "$tag"
}

# Full detail view for one snapshot: sessions -> panes + tail of each capture.
render_preview() {
  local dir="$1"
  if [[ ! -f "$dir/state.tsv" ]]; then echo "no snapshot at: $dir"; return 0; fi
  local meta; meta=$(snap_meta "$dir")
  echo "snapshot : $(basename "$dir")"
  echo "saved_at : $(cat "$dir/saved_at" 2>/dev/null)"
  echo "origin   : $(snap_origin "$dir")"
  # shellcheck disable=SC2086
  set -- $meta
  echo "contents : $1 sessions, $2 panes, $3 with claude"
  echo
  local cur=""
  while IFS=$'\t' read -r session win_idx win_name win_layout pane_idx pane_dir is_claude win_active claude_session_id; do
    if [[ "$session" != "$cur" ]]; then
      echo "══ $session"
      cur="$session"
    fi
    local tag=""; [[ "$is_claude" == "1" ]] && tag="  [claude]"
    echo "  ${win_idx}.${pane_idx}  ${win_name}  ·  ${pane_dir}${tag}"
    local safe="${session//\//__}"
    local cap="$dir/pane_contents/${safe}_${win_idx}_${pane_idx}.txt"
    if [[ -f "$cap" ]]; then
      grep -v '^[[:space:]]*$' "$cap" | tail -6 | sed 's/^/      | /'
    fi
  done < "$dir/state.tsv"
}

list_table() {
  local dir first=1
  while IFS= read -r dir; do
    local tag=""
    [[ "$dir" == "$SAVE_DIR" ]] && tag="  (live)"
    if (( first )); then first=0; fi
    printf '%s\n' "$(row_label "$dir" "$tag")"
  done < <(all_snapshot_dirs)
}

pick() {
  local dirs=()
  local d
  while IFS= read -r d; do dirs+=("$d"); done < <(all_snapshot_dirs)
  if (( ${#dirs[@]} == 0 )); then
    echo "No snapshots found." >&2
    return 1
  fi

  if command -v fzf >/dev/null 2>&1; then
    local chosen
    chosen=$(
      for d in "${dirs[@]}"; do
        local tag=""; [[ "$d" == "$SAVE_DIR" ]] && tag="  (live)"
        printf '%s\t%s\n' "$d" "$(row_label "$d" "$tag")"
      done | fzf --delimiter=$'\t' --with-nth=2.. \
                 --prompt='restore from> ' --height=90% --border \
                 --preview="$SELF --preview {1}" \
                 --preview-window='right,62%,wrap'
    ) || return 1
    [[ -n "$chosen" ]] || return 1
    printf '%s\n' "${chosen%%$'\t'*}"
    return 0
  fi

  # Fallback: numbered menu with on-demand preview.
  echo "Snapshots (newest first):" >&2
  local i=1
  for d in "${dirs[@]}"; do
    local tag=""; [[ "$d" == "$SAVE_DIR" ]] && tag="  (live)"
    printf '%3d) %s\n' "$i" "$(row_label "$d" "$tag")" >&2
    i=$((i + 1))
  done
  echo "Enter a number to restore, 'v N' to preview, or 'q' to cancel." >&2
  local ans idx
  while true; do
    printf 'choice> ' >&2
    read -r ans || return 1
    case "$ans" in
      q|Q|"") return 1 ;;
      v\ *|V\ *)
        idx="${ans#* }"
        if [[ "$idx" =~ ^[0-9]+$ ]] && (( idx >= 1 && idx <= ${#dirs[@]} )); then
          render_preview "${dirs[$((idx - 1))]}" >&2
        else
          echo "bad index" >&2
        fi
        ;;
      *)
        if [[ "$ans" =~ ^[0-9]+$ ]] && (( ans >= 1 && ans <= ${#dirs[@]} )); then
          printf '%s\n' "${dirs[$((ans - 1))]}"
          return 0
        fi
        echo "bad index" >&2
        ;;
    esac
  done
}

cmd="${1:---list}"
case "$cmd" in
  --list|list) list_table ;;
  --pick|pick) pick ;;
  --preview|--inspect) shift; render_preview "${1:?need a snapshot dir}" ;;
  -h|--help) usage ;;
  *) echo "Unknown option: $cmd" >&2; usage ;;
esac

#!/usr/bin/env bash
# ABOUTME: Rename old-pattern tmux sessions to {basename}@YYYY-MM-DD-HHhMM scheme.
# ABOUTME: Also exposes --gen-name for .zshrc's tn() helper to share naming logic.

set -euo pipefail

# ---------------------------------------------------------------------------- #
# Cross-platform date formatter: takes an epoch, prints YYYY-MM-DD-HHhMM.
# Detects GNU vs BSD date once at script start.
# ---------------------------------------------------------------------------- #
if date -d "@0" +%Y >/dev/null 2>&1; then
  _fmt_date() { date -d "@$1" +%Y-%m-%d-%Hh%M; }
else
  _fmt_date() { date -r "$1" +%Y-%m-%d-%Hh%M; }
fi

# ---------------------------------------------------------------------------- #
# Sanitize a raw basename per the spec:
#   lowercase → non-alnum runs → single '_' → trim → cap at 20 chars.
# Pure function: takes stdin-less arg, prints result.
# ---------------------------------------------------------------------------- #
_sanitize_name() {
  local raw="$1"
  local s
  s=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')
  s=$(printf '%s' "$s" | sed -E 's/[^a-z0-9]+/_/g')
  s="${s#_}"
  s="${s%_}"
  s="${s:0:20}"
  printf '%s' "$s"
}

# ---------------------------------------------------------------------------- #
# Generate a session name from a directory path and an epoch timestamp.
# Empty sanitized basename (e.g. from '/') → leading '@' only.
# ---------------------------------------------------------------------------- #
_gen_session_name() {
  local dir="$1" epoch="$2"
  local base
  base=$(basename -- "$dir")
  base=$(_sanitize_name "$base")
  local stamp
  stamp=$(_fmt_date "$epoch")
  if [[ -n "$base" ]]; then
    printf '%s@%s' "$base" "$stamp"
  else
    printf '@%s' "$stamp"
  fi
}

# ---------------------------------------------------------------------------- #
# Self-test: asserts sanitize and name generation on a fixed matrix.
# Exits 0 on success, non-zero on first failure.
# ---------------------------------------------------------------------------- #
_self_test() {
  local failed=0
  _assert_eq() {
    local got="$1" want="$2" label="$3"
    if [[ "$got" != "$want" ]]; then
      printf 'FAIL %s: got %q, want %q\n' "$label" "$got" "$want" >&2
      failed=1
    else
      printf 'ok   %s\n' "$label"
    fi
  }

  _assert_eq "$(_sanitize_name 'Setup')"              'setup'               'sanitize: Setup'
  _assert_eq "$(_sanitize_name 'ezalos')"             'ezalos'              'sanitize: ezalos'
  _assert_eq "$(_sanitize_name 'my.project v2')"      'my_project_v2'       'sanitize: my.project v2'
  _assert_eq "$(_sanitize_name '')"                   ''                    'sanitize: empty'
  _assert_eq "$(_sanitize_name '___foo___')"          'foo'                 'sanitize: underscore trim'
  _assert_eq "$(_sanitize_name 'extremely-long-directory-name-here')" 'extremely_long_direc' 'sanitize: 20-char cap'
  _assert_eq "$(_sanitize_name '!!!')"                ''                    'sanitize: all-bad-chars'

  local name
  name=$(_gen_session_name "/home/ezalos/Setup" 1713283800)
  [[ "$name" =~ ^setup@[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}h[0-9]{2}$ ]] \
    && printf 'ok   gen: setup dir format\n' \
    || { printf 'FAIL gen: setup dir format: %q\n' "$name" >&2; failed=1; }

  name=$(_gen_session_name "/" 1713283800)
  [[ "$name" =~ ^@[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}h[0-9]{2}$ ]] \
    && printf 'ok   gen: root dir format\n' \
    || { printf 'FAIL gen: root dir format: %q\n' "$name" >&2; failed=1; }

  return $failed
}

# ---------------------------------------------------------------------------- #
# Old auto-pattern regex: matches e.g. w-0416-14h30
# ---------------------------------------------------------------------------- #
_OLD_PATTERN='^w-[0-9]{4}-[0-9]{2}h[0-9]{2}$'

# ---------------------------------------------------------------------------- #
# Iterate qualifying sessions and print "old|new" lines on stdout.
# Prints skip notices for non-matching or problematic sessions to stderr.
# ---------------------------------------------------------------------------- #
_plan_renames() {
  if ! tmux list-sessions >/dev/null 2>&1; then
    echo "no tmux server running" >&2
    return 0
  fi

  local old_name created first_pane_path new_name
  while IFS='|' read -r old_name created; do
    if [[ ! "$old_name" =~ $_OLD_PATTERN ]]; then
      printf 'skip (custom name): %s\n' "$old_name" >&2
      continue
    fi
    first_pane_path=$(tmux list-windows -t "$old_name" \
      -F '#{window_index}|#{pane_current_path}' 2>/dev/null \
      | awk -F'|' 'NR==1 || $1+0 < min+0 {min=$1; line=$0} END { if (line != "") { sub(/^[^|]*\|/,"",line); print line } }')
    if [[ -z "$first_pane_path" ]]; then
      printf 'skip (no first window): %s\n' "$old_name" >&2
      continue
    fi
    new_name=$(_gen_session_name "$first_pane_path" "$created")
    printf '%s|%s\n' "$old_name" "$new_name"
  done < <(tmux list-sessions -F '#{session_name}|#{session_created}')
}

# ---------------------------------------------------------------------------- #
# Entry point
# ---------------------------------------------------------------------------- #
main() {
  case "${1:-}" in
    --self-test) _self_test ;;
    -h|--help)
      cat <<'USAGE'
Usage: tmux-rename-sessions.sh [--apply] [--gen-name <dir>] [--self-test]

Dry-run by default: lists planned renames for auto-pattern sessions.
  --apply           actually rename sessions
  --gen-name <dir>  print the name tn() would use for a new session in <dir>
  --self-test       run the sanitize/gen unit tests
USAGE
      ;;
    --gen-name)
      local dir="${2:-}"
      if [[ -z "$dir" ]]; then
        echo "error: --gen-name requires a directory argument" >&2
        exit 2
      fi
      _gen_session_name "$dir" "$(date +%s)"
      echo
      ;;
    "")
      echo "Dry-run: planned renames (best-effort — basename comes from current first-window path, not creation-time path)"
      echo "Pass --apply to execute."
      echo "---"
      local old new count=0
      while IFS='|' read -r old new; do
        printf '  %s  →  %s\n' "$old" "$new"
        count=$((count + 1))
      done < <(_plan_renames)
      echo "---"
      echo "$count session(s) would be renamed."
      ;;
    --apply)
      local renamed=0 skipped_collision=0 old new
      while IFS='|' read -r old new; do
        if [[ "$old" == "$new" ]]; then
          printf 'skip (already correct): %s\n' "$old"
          continue
        fi
        if tmux has-session -t "=$new" 2>/dev/null; then
          printf 'skip (collision: %s already exists): %s\n' "$new" "$old" >&2
          skipped_collision=$((skipped_collision + 1))
          continue
        fi
        if tmux rename-session -t "=$old" "$new"; then
          printf '  %s  →  %s\n' "$old" "$new"
          renamed=$((renamed + 1))
        else
          printf 'error renaming %s → %s\n' "$old" "$new" >&2
        fi
      done < <(_plan_renames)
      echo "---"
      echo "$renamed renamed, $skipped_collision skipped (collision)"
      ;;
    *)
      echo "error: unknown arg: $1" >&2
      exit 2
      ;;
  esac
}

main "$@"

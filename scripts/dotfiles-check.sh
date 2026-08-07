#!/usr/bin/env bash
# ABOUTME: Updates ~/Setup sync status cache. Local state recomputes every call
# ABOUTME: (no network, ~20ms); git fetch is rate-limited to once per 30min globally.

CACHE_FILE="${HOME}/.cache/dotfiles_sync_status"
FETCH_MARKER="${HOME}/.cache/dotfiles_sync.last_fetch"
LOCK_DIR="${HOME}/.cache/dotfiles_sync.lock.d"
SETUP_DIR="${HOME}/Setup"
MAX_AGE=1800  # 30 minutes in seconds

mkdir -p "${HOME}/.cache"

# Portable mtime. GNU stat must come first: on Linux, "stat -f" means
# "filesystem info" (succeeds with wrong output), while "stat -c" fails
# cleanly on macOS. BSD stat on macOS uses "-f <format>".
get_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0
}

cd "$SETUP_DIR" || exit 1

# Decide whether to fetch. Only the network call is rate-limited —
# local status computation always runs below so commits/pushes show instantly.
needs_fetch=1
if [[ -f "$FETCH_MARKER" ]]; then
  age=$(( $(date +%s) - $(get_mtime "$FETCH_MARKER") ))
  (( age < MAX_AGE )) && needs_fetch=0
fi

if (( needs_fetch )); then
  # Atomic mkdir lock (portable; no flock dependency)
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT
    # Double-check under lock — another session may have just finished fetching
    if [[ -f "$FETCH_MARKER" ]]; then
      age=$(( $(date +%s) - $(get_mtime "$FETCH_MARKER") ))
      (( age < MAX_AGE )) && needs_fetch=0
    fi
    if (( needs_fetch )); then
      git fetch --quiet 2>/dev/null
      touch "$FETCH_MARKER"
    fi
  fi
  # If we couldn't get the lock, another session is fetching — just skip
  # the fetch and compute status against whatever @{u} we already have.
fi

# Always recompute local status against current refs (cheap, no network).
local_head=$(git rev-parse HEAD 2>/dev/null)
remote_head=$(git rev-parse @{u} 2>/dev/null)
merge_base=$(git merge-base HEAD @{u} 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | head -1)

status=""
[[ -n "$dirty" ]] && status="dirty"

if [[ -n "$remote_head" && "$local_head" != "$remote_head" ]]; then
  if [[ "$local_head" == "$merge_base" ]]; then
    status="${status:+$status }behind"
  elif [[ "$remote_head" == "$merge_base" ]]; then
    status="${status:+$status }ahead"
  else
    status="${status:+$status }diverged"
  fi
fi

echo "${status:-ok}" > "$CACHE_FILE"

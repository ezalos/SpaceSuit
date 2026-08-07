#!/usr/bin/env bash
# ABOUTME: Deploy crontab entries and systemd user services from this directory.
# ABOUTME: Uses marker comments for crontab; symlinks + enable for systemd services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRONTAB_FILE="$SCRIPT_DIR/crontabs"

MARKER_BEGIN="# --- BEGIN Setup/scripts/crontabs (managed, do not edit) ---"
MARKER_END="# --- END Setup/scripts/crontabs ---"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: cron-deploy.sh [-h] [--remove]

Deploy crontab entries from scripts/crontabs into the user's crontab,
and install systemd user services from this directory.

Options:
  --remove   Remove the managed crontab block and disable systemd services
  -h         Show this help
EOF
  exit 0
fi

if [[ ! -f "$CRONTAB_FILE" ]]; then
  echo "Error: $CRONTAB_FILE not found."
  exit 1
fi

# Read current crontab (empty string if none)
existing=$(crontab -l 2>/dev/null || true)

# Strip the old managed block (if any)
cleaned=$(echo "$existing" | awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
  $0 == b { skip=1; next }
  $0 == e { skip=0; next }
  !skip { print }
')

# --- Systemd user services ---
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="tmux-save-on-shutdown.service"
SERVICE_SRC="$SCRIPT_DIR/$SERVICE_FILE"

deploy_systemd_service() {
  if [[ ! -f "$SERVICE_SRC" ]]; then
    echo "Warning: $SERVICE_SRC not found, skipping systemd service."
    return
  fi
  mkdir -p "$SYSTEMD_USER_DIR"
  ln -sf "$SERVICE_SRC" "$SYSTEMD_USER_DIR/$SERVICE_FILE"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE_FILE"
  echo "Deployed systemd service: $SERVICE_FILE (enabled + started)"
}

remove_systemd_service() {
  if systemctl --user is-enabled "$SERVICE_FILE" &>/dev/null; then
    systemctl --user disable --now "$SERVICE_FILE"
    echo "Disabled systemd service: $SERVICE_FILE"
  fi
  if [[ -L "$SYSTEMD_USER_DIR/$SERVICE_FILE" ]]; then
    rip "$SYSTEMD_USER_DIR/$SERVICE_FILE"
    systemctl --user daemon-reload
    echo "Removed systemd service: $SERVICE_FILE"
  fi
}

# Remove mode: strip crontab block and disable systemd services
if [[ "${1:-}" == "--remove" ]]; then
  echo "$cleaned" | crontab -
  echo "Removed managed crontab block."
  remove_systemd_service
  exit 0
fi

# Build the new managed block from the crontabs file (skip comments and blanks)
entries=""
while IFS= read -r line; do
  # Skip comments and blank lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// /}" ]] && continue
  entries+="$line"$'\n'
done < "$CRONTAB_FILE"

if [[ -z "$entries" ]]; then
  echo "No crontab entries found in $CRONTAB_FILE."
  exit 0
fi

# Assemble: cleaned existing + new managed block
new_crontab="$cleaned"
# Ensure a newline separator if there's existing content
if [[ -n "${cleaned// /}" ]]; then
  new_crontab+=$'\n'
fi
new_crontab+="$MARKER_BEGIN"$'\n'
new_crontab+="$entries"
new_crontab+="$MARKER_END"$'\n'

echo "$new_crontab" | crontab -

# Show what was deployed
count=$(echo -n "$entries" | grep -c '^')
echo "Deployed $count crontab entry/entries from $CRONTAB_FILE"
echo ""
echo "Current crontab:"
crontab -l

# Deploy systemd services
echo ""
deploy_systemd_service

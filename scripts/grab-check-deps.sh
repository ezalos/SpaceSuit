#!/usr/bin/env bash
# ABOUTME: Verifies grab's runtime dependencies (socat, python3) are installed.
# ABOUTME: Used as the ack_cmd for the grab_setup notice; exits 0 when all deps present.

set -eu

missing=()

for cmd in socat python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("$cmd")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "grab-check-deps: missing: ${missing[*]}" >&2
  echo "  Linux:  sudo apt install -y ${missing[*]}" >&2
  echo "  macOS:  brew install ${missing[*]}" >&2
  exit 1
fi

# Also verify python3 can import tarfile's "data" filter (Python 3.12+ preferred,
# available as a backport on 3.8–3.11 too). If missing, fall back works but warn.
if ! python3 -c "import tarfile; tarfile.data_filter" 2>/dev/null; then
  echo "grab-check-deps: warning — tarfile.data_filter not available (Python < 3.8?)" >&2
  echo "  The receiver will still run but you lose the extra safety filter." >&2
  # Non-fatal: don't block ack.
fi

echo "grab-check-deps: socat=$(command -v socat), python3=$(command -v python3)"
echo "grab-check-deps: OK"

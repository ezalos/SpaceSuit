#!/usr/bin/env bash
# ABOUTME: Installs/updates the official Proton Drive CLI to ~/.local/bin/proton-drive.
# ABOUTME: Discovers the latest version from proton.me's CLI download index; Linux + macOS.

set -euo pipefail

INDEX_URL="https://proton.me/download/drive/cli/index.html"
DEST="$HOME/.local/bin/proton-drive"

case "$(uname -s)" in
  Linux)  os="linux" ;;
  Darwin) os="darwin" ;;
  *) echo "proton-drive-update: unsupported OS $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64)        arch="x64" ;;
  arm64|aarch64) arch="arm64" ;;
  *) echo "proton-drive-update: unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

version=$(curl -fsSL "$INDEX_URL" | grep -oE 'cli/[0-9]+\.[0-9]+\.[0-9]+' | head -n1 | cut -d/ -f2)
if [[ -z "$version" ]]; then
  echo "proton-drive-update: could not find a version on $INDEX_URL" >&2
  exit 1
fi

url="https://proton.me/download/drive/cli/${version}/${os}-${arch}/proton-drive"
tmp=$(mktemp)
echo "downloading proton-drive ${version} (${os}-${arch})..."
curl -fSL -o "$tmp" "$url"
chmod +x "$tmp"
mkdir -p "$(dirname "$DEST")"
mv "$tmp" "$DEST"
echo "installed proton-drive ${version} -> $DEST"
"$DEST" version

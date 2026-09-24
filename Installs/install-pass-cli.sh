#!/usr/bin/env bash
# ABOUTME: Installs the Proton Pass CLI (pass-cli) into ~/.local/bin from the vendor index, sha256-verified.
# ABOUTME: Arch-aware (linux/macos, x86_64/aarch64), idempotent (skips when the index version is installed), no sudo.
set -euo pipefail

INDEX_URL="${PASS_CLI_INDEX_URL:-https://proton.me/download/pass-cli/versions.json}"
DEST="$HOME/.local/bin/pass-cli"
TMP="$(mktemp -d)"
# The script's own mktemp scratch: plain rm, not rip, so the graveyard never fills with download junk.
trap 'rm -rf "$TMP"' EXIT

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) echo "install-pass-cli: unsupported OS $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x86_64 ;;
  aarch64|arm64) arch=aarch64 ;;
  *) echo "install-pass-cli: unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

curl -fsSL "$INDEX_URL" -o "$TMP/index.json"
version="$(jq -r '.passCliVersions.version // empty' "$TMP/index.json")"
url="$(jq -r ".passCliVersions.urls.${os}.${arch}.url" "$TMP/index.json")"
want="$(jq -r ".passCliVersions.urls.${os}.${arch}.hash" "$TMP/index.json")"
[ -n "$url" ] && [ "$url" != "null" ] || { echo "install-pass-cli: no ${os}.${arch} entry in the index" >&2; exit 1; }

if [ -x "$DEST" ] && [ -n "$version" ] && "$DEST" --version 2>/dev/null | grep -q "$version"; then
  echo "install-pass-cli: $version already installed at $DEST"; exit 0
fi

curl -fsSL "$url" -o "$TMP/pass-cli"
if command -v sha256sum >/dev/null 2>&1; then got="$(sha256sum "$TMP/pass-cli" | awk '{print $1}')"
else got="$(shasum -a 256 "$TMP/pass-cli" | awk '{print $1}')"; fi
[ "$got" = "$want" ] || { echo "install-pass-cli: sha256 MISMATCH for $url (got $got, index says $want)" >&2; exit 1; }

mkdir -p "$(dirname "$DEST")"
install -m 755 "$TMP/pass-cli" "$DEST"
echo "install-pass-cli: installed ${version:-pass-cli} at $DEST"

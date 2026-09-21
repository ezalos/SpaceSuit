#!/usr/bin/env bash
# ABOUTME: Builds tmux from source and refuses to install one that cannot emit OSC 52 clipboard escapes.
# ABOUTME: The 2026-07-08 hand-rolled build linked plain tiparm and killed the clipboard silently for two months.
set -euo pipefail

# ncurses >= 6.4 answers tparm() with NULL when a capability takes string
# parameters, so a tmux that did not find tiparm_s at configure time cannot
# expand its Ms capability. It then drops EVERY OSC 52 write with no error
# anywhere: paste buffers still fill, `show-options` still says
# `set-clipboard on`, and the only trace is `could not expand Ms` in a
# `tmux -vv` server log. That is what happened to the 3.5a build of
# 2026-07-08, and why this script exists instead of a remembered
# ./configure && make. Both gates below check for it -- one before the build
# and one on the binary itself, because the useful failure is the silent one.

VERSION="${TMUX_VERSION:-3.5a}"
PREFIX="${TMUX_PREFIX:-$HOME/.local/opt/tmux35}"
URL="https://github.com/tmux/tmux/releases/download/${VERSION}/tmux-${VERSION}.tar.gz"

# Pinned from the release tarball on 2026-09-21. A different version needs its
# own hash passed in rather than an unchecked download.
declare -A KNOWN_SHA256=(
    [3.5a]="16216bd0877170dfcc64157085ba9013610b12b082548c7c9542cc0103198951"
)
SHA256="${TMUX_SHA256:-${KNOWN_SHA256[$VERSION]:-}}"

die() { printf 'build-tmux: %s\n' "$*" >&2; exit 1; }

[ -n "$SHA256" ] || die "no known sha256 for tmux $VERSION; pass TMUX_SHA256=<hash> after checking the release yourself"

for tool in curl tar make gcc sha256sum; do
    command -v "$tool" >/dev/null || die "missing build tool: $tool"
done
# bison and the two -dev packages are what a bare box is actually missing.
command -v bison >/dev/null || die "missing bison (apt install bison)"
pkg-config --exists libevent || die "missing libevent headers (apt install libevent-dev)"
pkg-config --exists ncurses || die "missing ncurses headers (apt install libncurses-dev)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

printf 'build-tmux: fetching tmux %s\n' "$VERSION"
# Capped per the household network rule: this line shares a home fibre link.
curl -sSL --limit-rate 50M -o "$WORK/tmux.tar.gz" "$URL"
printf '%s  %s\n' "$SHA256" "$WORK/tmux.tar.gz" | sha256sum -c - >/dev/null 2>&1 \
    || die "sha256 mismatch on the downloaded tarball; refusing to build it"

tar xzf "$WORK/tmux.tar.gz" -C "$WORK"
cd "$WORK/tmux-${VERSION}"

printf 'build-tmux: configuring with prefix %s\n' "$PREFIX"
./configure --prefix="$PREFIX" >"$WORK/configure.log" 2>&1 \
    || { tail -20 "$WORK/configure.log" >&2; die "configure failed"; }

# Gate 1: configure must have FOUND tiparm_s, not merely finished.
grep -q "checking for tiparm_s\.\.\. yes" "$WORK/configure.log" \
    || die "configure did not find tiparm_s (install libncurses-dev and rerun) -- a tmux built now would drop every OSC 52 clipboard write in silence"

make -j"$(nproc)" >"$WORK/build.log" 2>&1 \
    || { tail -20 "$WORK/build.log" >&2; die "build failed"; }

# Gate 2: the built binary must really reference the symbol. A byte scan finds
# it in .dynstr without needing binutils on the box.
grep -q tiparm_s ./tmux \
    || die "the built tmux does not reference tiparm_s; refusing to install a binary that cannot set the clipboard"

mkdir -p "$PREFIX/bin"
# Install by rename, never over the live file: a running tmux server holds its
# binary open, so a plain copy fails with ETXTBSY, and the rename leaves that
# server on its own inode until it exits.
cp ./tmux "$PREFIX/bin/tmux.incoming"
chmod 755 "$PREFIX/bin/tmux.incoming"
mv "$PREFIX/bin/tmux.incoming" "$PREFIX/bin/tmux"

printf 'build-tmux: installed %s\n' "$("$PREFIX/bin/tmux" -V)"
cat <<'NOTE'
build-tmux: a running tmux server keeps the OLD binary until it exits -- the
server process is what expands the clipboard capability, so nothing changes in
a live session until it restarts. Snapshot and rebuild sessions around that
with scripts/tmux-save.sh and scripts/tmux-restore.sh, then re-attach.
Verify the result with GroundControl monitoring/tools/tmux-osc52, which copies
a marker on a real pty and reads what reaches the terminal.
NOTE

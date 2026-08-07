#!/bin/sh
# ABOUTME: Install a modern tmux (>=3.3, required for OSC 52 clipboard forwarding over ssh/nested tmux).
# ABOUTME: macOS -> Homebrew; Linux -> build TMUX_VERSION from source into ~/.local (system tmux untouched).
set -eu
TMUX_VERSION="${TMUX_VERSION:-3.5a}"
PREFIX="${PREFIX:-$HOME/.local}"
log() { printf '[install-tmux] %s\n' "$*"; }

if [ "$(uname -s)" = "Darwin" ]; then
	command -v brew >/dev/null || { log "Homebrew required on macOS (https://brew.sh)"; exit 1; }
	brew list tmux >/dev/null 2>&1 && brew upgrade tmux || brew install tmux
	log "done: $(tmux -V)"
	exit 0
fi

# ---- Linux: build a release tarball into $PREFIX. Needs a C toolchain + libevent +
#      ncurses + yacc (tmux's configure requires yacc even from a release tarball). ----
need=""
command -v gcc >/dev/null || need="$need build-essential"
command -v make >/dev/null || need="$need make"
command -v pkg-config >/dev/null || need="$need pkg-config"
command -v yacc >/dev/null || command -v bison >/dev/null || need="$need bison"
pkg-config --exists libevent 2>/dev/null || need="$need libevent-dev"
{ pkg-config --exists ncurses 2>/dev/null || [ -e /usr/include/ncurses.h ]; } || need="$need libncurses-dev"
if [ -n "$need" ]; then
	log "missing build deps:$need"
	if command -v apt-get >/dev/null; then
		log "installing via sudo apt-get (you will be prompted)"
		sudo apt-get update && sudo apt-get install -y $need
	else
		log "install these with your package manager, then re-run"; exit 1
	fi
fi

work=$(mktemp -d)
trap 'rip -f "$work" 2>/dev/null || rm -rf "$work"' EXIT
cd "$work"
url="https://github.com/tmux/tmux/releases/download/${TMUX_VERSION}/tmux-${TMUX_VERSION}.tar.gz"
log "downloading $url"
curl -fLO "$url"
tar xzf "tmux-${TMUX_VERSION}.tar.gz"
cd "tmux-${TMUX_VERSION}"
# tmux's configure looks for `yacc`; Debian/Ubuntu ship bison without a yacc symlink, so
# point YACC at bison's yacc-compat mode when there's no plain `yacc`.
if ! command -v yacc >/dev/null 2>&1 && command -v bison >/dev/null 2>&1; then
	export YACC="bison -y"
fi
log "building into $PREFIX"
./configure --prefix="$PREFIX" >/dev/null
make -j"$(nproc 2>/dev/null || echo 2)" >/dev/null
make install >/dev/null
hash -r 2>/dev/null || true
log "installed: $("$PREFIX/bin/tmux" -V)   (PATH tmux -> $(command -v tmux))"

cat <<'EOF'

[install-tmux] NEXT — activate it (the RUNNING tmux server is still the old binary):
  1. Confirm PATH prefers the new one:  command -v tmux   (want ~/.local/bin/tmux)
  2. Restarting the server adopts the new binary but ENDS current sessions. If you use
     tmux-resurrect, save first, then:   tmux kill-server   (start a fresh tmux after).
  3. Verify the clipboard option is live: tmux show -gv set-clipboard   (want "on").
EOF

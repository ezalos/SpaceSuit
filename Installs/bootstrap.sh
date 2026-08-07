#!/usr/bin/env bash
# ABOUTME: idempotent, user-space toolchain bootstrap for a pristine machine --
# ABOUTME: installs everything dotfiles/.zshrc and dotfiles/.p10k.zsh actually
# ABOUTME: source, so a fresh box can load them without error before the rest
# ABOUTME: of Setup is deployed. Safe to re-run: every step checks for an
# ABOUTME: existing install first.
#
# Driven by reading dotfiles/.zshrc and dotfiles/.p10k.zsh, not guessed:
#   - oh-my-zsh                         (export ZSH=$HOME/.oh-my-zsh; source $ZSH/oh-my-zsh.sh)
#   - powerlevel10k                     (ZSH_THEME="powerlevel10k/powerlevel10k")
#   - zsh-autosuggestions               (plugins=(... zsh-autosuggestions ...))
#   - zsh-syntax-highlighting           (plugins=(... zsh-syntax-highlighting ...))
#   - zsh-history-substring-search      (sourced directly, NOT via plugins=(),
#                                         and NOT guarded -- .zshrc line 69)
#   - rust / cargo (rustup)             (needed to build rip2)
#   - rip2                              (CLAUDE.md: "Always use rip", cargo install)
#   - nvim                              (EDITOR='nvim' when not over ssh)
#   - direnv                            (direnv export/hook zsh, unconditional)
#
# Deliberately does NOT use apt/sudo for anything: every tool here has an
# official user-space install path, and bootstrap.sh must work whether or not
# the account running it has passwordless sudo. Whatever "apt-based where
# possible" would have bought us (direnv is the only apt-friendly candidate
# here) isn't worth the sudo dependency.
set -uo pipefail

FAILED=0

status() {
    # status <tool> <ok|skipped(present)|FAILED>
    echo "[bootstrap] $1 $2"
    [[ "$2" == "FAILED" ]] && FAILED=1
}

ZSH_HOME="${ZSH:-$HOME/.oh-my-zsh}"
ZSH_CUSTOM="${ZSH_CUSTOM:-$ZSH_HOME/custom}"

# --- oh-my-zsh -------------------------------------------------------------
if [[ -f "$ZSH_HOME/oh-my-zsh.sh" ]]; then
    status "oh-my-zsh" "skipped(present)"
else
    if RUNZSH=no CHSH=no KEEP_ZSHRC=yes sh -c \
        "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" \
        >/tmp/bootstrap-omz.log 2>&1; then
        status "oh-my-zsh" "ok"
    else
        status "oh-my-zsh" "FAILED"
        cat /tmp/bootstrap-omz.log >&2
    fi
fi

# --- powerlevel10k -----------------------------------------------------------
p10k_dir="$ZSH_CUSTOM/themes/powerlevel10k"
if [[ -d "$p10k_dir/.git" ]]; then
    status "powerlevel10k" "skipped(present)"
elif git clone --depth=1 -q https://github.com/romkatv/powerlevel10k.git "$p10k_dir" 2>/tmp/bootstrap-p10k.log; then
    status "powerlevel10k" "ok"
else
    status "powerlevel10k" "FAILED"
    cat /tmp/bootstrap-p10k.log >&2
fi

# --- zsh plugins .zshrc references -----------------------------------------
clone_plugin() {
    local name="$1" url="$2"
    local dir="$ZSH_CUSTOM/plugins/$name"
    if [[ -d "$dir/.git" ]]; then
        status "$name" "skipped(present)"
    elif git clone --depth=1 -q "$url" "$dir" 2>/tmp/bootstrap-plugin.log; then
        status "$name" "ok"
    else
        status "$name" "FAILED"
        cat /tmp/bootstrap-plugin.log >&2
    fi
}
clone_plugin zsh-autosuggestions          https://github.com/zsh-users/zsh-autosuggestions
clone_plugin zsh-syntax-highlighting      https://github.com/zsh-users/zsh-syntax-highlighting.git
clone_plugin zsh-history-substring-search https://github.com/zsh-users/zsh-history-substring-search

# --- rust / cargo (rustup) ---------------------------------------------------
if [[ -x "$HOME/.cargo/bin/rustc" ]]; then
    status "rustc" "skipped(present)"
else
    if curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal >/tmp/bootstrap-rustup.log 2>&1; then
        status "rustc" "ok"
    else
        status "rustc" "FAILED"
        cat /tmp/bootstrap-rustup.log >&2
    fi
fi
# shellcheck disable=SC1091
[[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"

# --- rip2 (chain-tests the rust toolchain just installed) -------------------
if [[ -x "$HOME/.cargo/bin/rip" ]]; then
    status "rip2" "skipped(present)"
elif command -v cargo >/dev/null 2>&1 \
    && cargo install rip2 >/tmp/bootstrap-rip2.log 2>&1; then
    status "rip2" "ok"
else
    status "rip2" "FAILED"
    [[ -f /tmp/bootstrap-rip2.log ]] && cat /tmp/bootstrap-rip2.log >&2
fi

# --- nvim: official linux-x86_64 tarball -------------------------------------
NVIM_VERSION="v0.10.4"
NVIM_HOME="$HOME/.local/opt/nvim"
if [[ -x "$NVIM_HOME/bin/nvim" ]]; then
    status "nvim" "skipped(present)"
else
    mkdir -p "$HOME/.local/opt" "$HOME/.local/bin"
    if curl -fsSL -o /tmp/nvim.tar.gz \
            "https://github.com/neovim/neovim/releases/download/${NVIM_VERSION}/nvim-linux-x86_64.tar.gz" \
        && rm -rf "$NVIM_HOME" \
        && mkdir -p "$NVIM_HOME" \
        && tar -xzf /tmp/nvim.tar.gz -C "$NVIM_HOME" --strip-components=1 \
        && ln -sf "$NVIM_HOME/bin/nvim" "$HOME/.local/bin/nvim"; then
        status "nvim" "ok"
    else
        status "nvim" "FAILED"
    fi
    rm -f /tmp/nvim.tar.gz
fi

# --- direnv: official install script (user-space, no apt/sudo needed) -------
if [[ -x "$HOME/.local/bin/direnv" ]]; then
    status "direnv" "skipped(present)"
else
    mkdir -p "$HOME/.local/bin"
    if curl -sfL https://direnv.net/install.sh \
        | bin_path="$HOME/.local/bin" bash >/tmp/bootstrap-direnv.log 2>&1; then
        status "direnv" "ok"
    else
        status "direnv" "FAILED"
        cat /tmp/bootstrap-direnv.log >&2
    fi
fi

# --- tmux: base toolchain, but apt-only -- verify, don't try to install -----
# dotfiles/.tmux.conf needs a real tmux binary. Unlike everything above, tmux
# has no clean official user-space install path (no static release binary,
# no cargo/rustup-style installer); the only sane way to get it is apt, which
# needs root. bootstrap.sh is deliberately root/sudo-free (see file header),
# so on a machine where this Docker image's own `apt-get install tmux` step
# hasn't already run, this step can only report the gap, not close it.
if command -v tmux >/dev/null 2>&1; then
    status "tmux" "skipped(present)"
else
    status "tmux" "FAILED"
    echo "[bootstrap] tmux needs 'sudo apt install tmux' (or equivalent) -- bootstrap.sh cannot do this without root" >&2
fi

if [[ $FAILED -eq 0 ]]; then
    echo "[bootstrap] done: all-ok"
else
    echo "[bootstrap] done: has-failures"
fi
exit $FAILED

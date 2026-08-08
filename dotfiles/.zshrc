# ---------------------------------------------------------------------------- #
#                                     PATH                                     #
# ---------------------------------------------------------------------------- #


# Give background agents a plain prompt, but keep p10k for interactive
# terminals even when they inherit CURSOR_AGENT (e.g. Cursor's own terminal).
# A real keyboard session is interactive with stdout on a TTY; a captured
# agent shell is not, so it falls through to the plain-prompt path below.
if [[ -n "$CURSOR_AGENT" ]] && { [[ ! -o interactive ]] || [[ ! -t 1 ]]; }; then
    export WHICH_COMPUTER="CURSOR_AGENT"
fi



export PATH_SETUP_DIR="$HOME/42/SpaceSuit"
# Helper utilities ---------------------------------------------------- #
# Functions to safely add directories to $PATH (duplicates removed)
path_prepend() { for d in "$@"; do [[ -d $d ]] && path=($d $path); done }
path_append()  { for d in "$@"; do [[ -d $d ]] && path=($path $d); done }
typeset -gU path

# Sensible zsh options ------------------------------------------------ #
setopt autocd pushd_ignore_dups share_history hist_ignore_space inc_append_history 
# zmodload zsh/zprof

# Initial PATH bootstrap
path_prepend "$PATH_SETUP_DIR/bin" "$PATH_SETUP_DIR/usr/bin" "$HOME/.local/bin"

if [[ "$WHICH_COMPUTER" == "CURSOR_AGENT" ]]; then
export ZSH_THEME=""
else
# From: https://github.com/romkatv/powerlevel10k/issues/702#issuecomment-626222730
# Guarded: a pristine machine (direnv not yet installed) would otherwise spray
# "command not found: direnv" on every shell start -- found via the
# resurrection e2e test (~/42/GroundControl/resurrection/).
(( ${+commands[direnv]} )) && emulate zsh -c "$(direnv export zsh)"

# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# From: https://github.com/romkatv/powerlevel10k/issues/702#issuecomment-626222730
(( ${+commands[direnv]} )) && emulate zsh -c "$(direnv hook zsh)"

ZSH_THEME="powerlevel10k/powerlevel10k"
# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
fi

# **************************************************************************** #
#                                                                              #
#                                                         :::      ::::::::    #
#    .zshrc                                             :+:      :+:    :+:    #
#                                                     +:+ +:+         +:+      #
#    By: ezalos <ezalos@student.42.fr>              +#+  +:+       +#+         #
#                                                 +#+#+#+#+#+   +#+            #
#                                                      #+#    #+#              #
#    Created: 2021/03/27 23:02:39 by ezalos           ###   ########.fr        #
#                                                                              #
# **************************************************************************** #

# ---------------------------------------------------------------------------- #
#                                   Oh-my-zsh                                  #
# ---------------------------------------------------------------------------- #
# Path to your oh-my-zsh installation.
export ZSH=$HOME/.oh-my-zsh

# Guarded: a pristine machine (oh-my-zsh/plugins not yet installed via
# Installs/bootstrap.sh) would otherwise spray "no such file or directory" on
# every shell start -- found via the resurrection e2e test
# (~/42/GroundControl/resurrection/).
[[ -f "$ZSH/custom/plugins/zsh-history-substring-search/zsh-history-substring-search.zsh" ]] \
  && source "$ZSH/custom/plugins/zsh-history-substring-search/zsh-history-substring-search.zsh"

plugins=(
  git
  zsh-autosuggestions
  zsh-syntax-highlighting
)
[[ -f "$ZSH/oh-my-zsh.sh" ]] && source "$ZSH/oh-my-zsh.sh"
HISTCONTROL=ignorespace
export LANG=en_US.UTF-8

# BINDKEYS
bindkey '^[[1;2A' history-substring-search-up
bindkey '^[[1;2B' history-substring-search-down
# ---------------------------------------------------------------------------- #
#                                     Init                                     #
# ---------------------------------------------------------------------------- #

# Preferred editor for local and remote sessions
if [[ -n $SSH_CONNECTION ]]; then
   export EDITOR='vim'
else
   export EDITOR='nvim'
fi


# ---------------------------------------------------------------------------- #
#                                 Environment file                             #
# ---------------------------------------------------------------------------- #
SETUP_ENV_FILE="$PATH_SETUP_DIR/.setup_env"

# Load persisted WHICH_COMPUTER if present
if [[ -f "$SETUP_ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$SETUP_ENV_FILE"
fi

# Machine-local overrides: identity + capability flags for THIS box only.
# Untracked, one per machine -- a box's own hostname/identity and any
# machine-specific tweaks (SSH key, conda, extra PATH entries, ...) live here
# instead of in this public file. See the flags this file reads further down
# (ZSHRC_LOCAL_*) for the supported hooks.
[ -f ~/.zshrc.local ] && source ~/.zshrc.local

# Set computer identifier
if [[ -z "${WHICH_COMPUTER:-}" ]]; then
    if [[ `uname -n` = "Louiss-MBP.lan" ]]; then
        export WHICH_COMPUTER="MacBook"
    elif [[ `uname -n` = "Louiss-MacBook-Pro.local" ]]; then
        export WHICH_COMPUTER="MacBook"
    elif [[ `uname -n` =~ ^louiss-macbook-pro-[0-9]+\.home$ ]]; then
        export WHICH_COMPUTER="MacBook"
    elif [[ `uname -n` =~ ^Louiss-MacBook-Pro-[0-9]+\.local$ ]]; then
        export WHICH_COMPUTER="MacBook"
    elif [[ `uname -n` = "MacBook-Pro-de-Louis.local" ]] || [[ `uname -n` = "mbp-de-louis.home" ]]; then
        export WHICH_COMPUTER="MacBook_Heuritech" # Macbook from Heuritech
    elif [[ `uname -n` =~ ^rnd ]]; then
        export WHICH_COMPUTER="rnd_Heuritech" # Remote Heuritech machine
    elif [[ `uname -n` = "smic" ]]; then
        export WHICH_COMPUTER="smic_Heuritech" # Remote Heuritech machine
    else
        export WHICH_COMPUTER="Unknown"
    fi

    export WHICH_COMPUTER

    # Persist only if recognised
    if [[ $WHICH_COMPUTER != "Unknown" ]]; then
        if [[ ! -f "$SETUP_ENV_FILE" ]] || ! grep -q '^export WHICH_COMPUTER=' "$SETUP_ENV_FILE"; then
            echo "export WHICH_COMPUTER=\"$WHICH_COMPUTER\"" >> "$SETUP_ENV_FILE"
        fi
    fi
fi


# ---------------------------------------------------------------------------- #
#                              DBUS SESSION (Linux)                             #
# ---------------------------------------------------------------------------- #
# SSH shells don't inherit DBUS_SESSION_BUS_ADDRESS, so keyring-backed CLIs
# (proton-drive/pdrive, secret-tool, ...) fail with "Cannot autolaunch D-Bus
# without X11 $DISPLAY". Point it at the running user bus when one exists.
# No-op on macOS (Keychain, no such socket) and on headless boxes without a bus.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "/run/user/$(id -u)/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
fi


# ---------------------------------------------------------------------------- #
#                                   SSH INIT                                   #
# ---------------------------------------------------------------------------- #
# One-shot SSH agent/key setup ----------------------------------------------- #
setup_ssh() {
  # Guarded: a pristine machine (no openssh-client yet) would otherwise spray
  # "command not found: ssh-agent" on every shell start -- found via the
  # resurrection e2e test (~/42/GroundControl/resurrection/).
  if (( ! ${+SSH_AUTH_SOCK} )) && (( ${+commands[ssh-agent]} )); then
    eval "$(ssh-agent -s)" >/dev/null 2>&1
  fi

  # ZSHRC_LOCAL_SSH_KEY (set in ~/.zshrc.local) takes priority -- machine-local
  # override. Falls back to the generic case below for machines without one.
  local key="${ZSHRC_LOCAL_SSH_KEY:-}"
  if [[ -z $key ]]; then
    case "$WHICH_COMPUTER" in
        MacBook)        key="$HOME/.ssh/gthb" ;;
        *_Heuritech)    key="$HOME/.ssh/ghub_ezalos" ;;
    esac
  fi

  # Only warn when we actually expected a specific key (a recognized
  # WHICH_COMPUTER or an explicit ZSHRC_LOCAL_SSH_KEY) and it's missing --
  # not on every unrecognized/unconfigured machine, where there was never a
  # key to expect in the first place (e.g. a fresh resurrection).
  if [[ -n $key && -f $key ]]; then
      ssh-add "$key" >/dev/null 2>&1
  elif [[ -n $key ]]; then
      echo >&2 "⚠️  SSH key $key not found"
  fi
}

setup_ssh


# ---------------------------------------------------------------------------- #
#                                     Conda                                    #
# ---------------------------------------------------------------------------- #

# ZSHRC_LOCAL_CONDA_ENABLED (set in ~/.zshrc.local) opts a machine into this
# whole block: conda, machine-local secrets, and the OpenClaw aliases below.
if [[ -n "${ZSHRC_LOCAL_CONDA_ENABLED:-}" ]]; then
    export PATH=$PATH:/home/ezalos/miniconda3/bin
	# Lazy-load conda to speed up shell startup
	lazy_conda() {
		unset -f lazy_conda
		eval "$(/home/ezalos/miniconda3/bin/conda shell.zsh hook 2> /dev/null)"
	}
	add-zsh-hook precmd lazy_conda

	# Machine-local secrets (tokens, keys) — never committed
	[[ -f "$PATH_SETUP_DIR/.secrets.sh" ]] && source "$PATH_SETUP_DIR/.secrets.sh"

	# OpenClaw aliases
	alias oc-cli='docker compose -f ~/openclaw/docker-compose.yml -f ~/openclaw/docker-compose.extra.yml run --rm openclaw-cli'
	alias oc-restart='docker compose -f ~/openclaw/docker-compose.yml -f ~/openclaw/docker-compose.extra.yml restart openclaw-gateway'
	alias oc-reload='docker compose -f ~/openclaw/docker-compose.yml -f ~/openclaw/docker-compose.extra.yml down && docker compose -f ~/openclaw/docker-compose.yml -f ~/openclaw/docker-compose.extra.yml up -d openclaw-gateway'
	alias oc-logs='docker compose -f ~/openclaw/docker-compose.yml -f ~/openclaw/docker-compose.extra.yml logs -f openclaw-gateway'
fi

# ---------------------------------------------------------------------------- #
#                                     ALIAS                                    #
# ---------------------------------------------------------------------------- #

# Helper scripts
[[ -f "$PATH_SETUP_DIR/scripts/setup_helpers.sh" ]] && source "$PATH_SETUP_DIR/scripts/setup_helpers.sh"
[[ -f "$PATH_SETUP_DIR/scripts/heuritech_env.sh" ]] && source "$PATH_SETUP_DIR/scripts/heuritech_env.sh"

# Refresh dotfiles sync cache in background before each prompt.
# Script is fast (~20ms for local state) and backgrounded so it never blocks
# the prompt. The git fetch inside is globally rate-limited to once per 30min.
_dotfiles_sync_bg() {
  ("$PATH_SETUP_DIR/scripts/dotfiles-check.sh" &) 2>/dev/null
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _dotfiles_sync_bg

# General aliases
# copy: stdin (or file args) -> clipboard. Over SSH, uses OSC 52 so the bytes travel
# back through the ssh+tmux session to the LOCAL terminal's clipboard (xclip/pbcopy on a
# remote box can't reach your laptop). Locally, uses the native clipboard tool.
# OSC 52 needs: tmux `set-clipboard on` + the `clipboard` terminal-feature (tmux 3.2a
# has no allow-passthrough), and a terminal that honours OSC 52 writes (WezTerm does).
# Terminals cap OSC 52 at ~74-100 KB.
copy() {
	local data
	if [ $# -gt 0 ] && [ -e "$1" ]; then data=$(cat "$@"); else data=$(cat); fi
	if [ -n "$SSH_CONNECTION" ]; then
		# plain OSC 52 to the controlling terminal; tmux (set-clipboard on + clipboard
		# feature) forwards it to the outer terminal, which sets the laptop clipboard.
		local b64; b64=$(printf '%s' "$data" | base64 | tr -d '\n')
		printf '\033]52;c;%s\007' "$b64" > /dev/tty
	elif command -v pbcopy >/dev/null 2>&1; then printf '%s' "$data" | pbcopy
	elif command -v wl-copy >/dev/null 2>&1; then printf '%s' "$data" | wl-copy
	elif command -v xclip  >/dev/null 2>&1; then printf '%s' "$data" | xclip -sel c
	else print -u2 "copy: no clipboard method available"; return 1
	fi
}

alias indent="python3 ~/42/Python_Indentation/Indent.py -f"
# gcl + identity-aware clone wrappers (gcl-ezalos / gcl-entropic / gcl-anon); blocks
# plain github.com clones so no clone ever silently defaults to the wrong ssh key.
[ -f "$HOME/.config/git-identity/clone-aliases.zsh" ] && source "$HOME/.config/git-identity/clone-aliases.zsh"
alias ec="$EDITOR $HOME/.zshrc"
alias sc="source $HOME/.zshrc"
alias neo="neofetch --separator '\t'"
alias iscuda="python3 -c 'import sys; print(f\"{sys.version = }\"); import torch; print(f\"{torch. __version__ = }\"); print(f\"{torch.cuda.is_available() = }\"); print(f\"{torch.cuda.device_count() = }\")'"
alias bt="batcat --paging=never --style=plain "

# Docker cleanup aliases
alias docker_clean_ps='docker rm $(docker ps --filter=status=exited --filter=status=created -q)'
alias docker_clean_images='docker rmi $(docker images -a --filter=dangling=true -q)'
alias docker_clean_overlay='docker rm -vf $(docker ps --filter=status=exited --filter=status=created -q) ; docker rmi -f $(docker images --filter=dangling=true -q) ; docker volume prune -f ; docker system prune -a -f'
alias docker_kill_all='docker kill $(docker ps -a -q)'

# Spawn a dev docker container (cpu or gpu) from anywhere
dwork() {
  local variant="${1:-cpu}"
  local compose_file="$PATH_SETUP_DIR/docker-compose.${variant}.yaml"
  local service="work.${variant}"

  if [[ ! -f "$compose_file" ]]; then
    echo "Unknown variant: $variant (expected cpu or gpu)"
    return 1
  fi

  # Start the container if not already running
  if ! docker compose -f "$compose_file" ps --status=running --format '{{.Name}}' 2>/dev/null | grep -q "$service"; then
    echo "Starting $service..."
    docker compose -f "$compose_file" up -d
  fi

  docker compose -f "$compose_file" exec "$service" zsh
}

# Convert epoch timestamp to human-readable idle string (e.g. "2d 5h", "15m")
_tmux_idle_fmt() {
  local now=$(date +%s)
  local delta=$(( now - $1 ))
  local days=$(( delta / 86400 ))
  local hours=$(( (delta % 86400) / 3600 ))
  local mins=$(( (delta % 3600) / 60 ))
  local secs=$(( delta % 60 ))
  if (( days > 0 )); then
    printf "%dd %dh" "$days" "$hours"
  elif (( hours > 0 )); then
    printf "%dh %dm" "$hours" "$mins"
  elif (( mins > 0 )); then
    printf "%dm" "$mins"
  else
    printf "%ds" "$secs"
  fi
}

# tmux session listing. Delegates to ~/Setup/agents_dashboard, which adds each
# Claude session's title, whether it is blocked on you and for how long, and its
# task progress — see docs/plans/2026-08-04-tls-terminal-view-design.md.
# Defined as a function rather than left to PATH so `tls` keeps working in shells
# whose PATH has not picked up ~/.local/bin yet.
tls() {
  command tls "$@"
}

# The previous pure-zsh implementation, kept for one release as an escape hatch.
# Delete this once the new tls has earned its keep.
tls-legacy() {
  tmux list-sessions &>/dev/null || { echo "No tmux sessions"; return 1; }
  echo ""
  local sorted_sessions=(${(f)"$(tmux list-sessions -F '#{session_activity}|#{session_name}' | sort -n | cut -d'|' -f2)"})
  local s attached label line idx cmd wdir activity idle
  for s in "${sorted_sessions[@]}"; do
    attached=$(tmux display-message -t "$s" -p '#{session_attached}')
    if (( attached > 0 )); then
      label="\033[32m(attached)\033[0m"
    else
      label="\033[33m(detached)\033[0m"
    fi
    printf "\033[1;36m%s\033[0m %b\n" "$s" "$label"
    for line in ${(f)"$(tmux list-windows -t "$s" -F '#{window_index}|#{pane_current_command}|#{pane_current_path}|#{window_activity}')"}; do
      idx="${line%%|*}"; line="${line#*|}"
      cmd="${line%%|*}"; line="${line#*|}"
      wdir="${line%%|*}"; line="${line#*|}"
      activity="$line"
      idle=$(_tmux_idle_fmt "$activity")
      printf "  %s: %s @ %s \033[2m(%s ago)\033[0m\n" "$idx" "$cmd" "$wdir" "$idle"
    done
  done
}

# tmux cleanup: detect phantom windows and stale sessions
tclean() {
  local stale=1 threshold_hours=72 dry_run=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --stale|-s) stale=1; shift ;;
      --hours|-h) threshold_hours="$2"; shift 2 ;;
      --dry-run|-n) dry_run=1; shift ;;
      --help)
        cat <<'USAGE'
Usage: tclean [--stale|-s] [--hours N|-h N] [--dry-run|-n] [--help]

  (default)       Detect phantom windows (opened but never used)
  --stale, -s     Also detect stale detached sessions (idle beyond threshold)
  --hours N, -h N Set stale threshold in hours (default: 72)
  --dry-run, -n   Show candidates without killing anything
  --help          Show this help message
USAGE
        return 0
        ;;
      *) echo "Unknown option: $1"; return 1 ;;
    esac
  done

  tmux list-sessions &>/dev/null || { echo "No tmux sessions"; return 1; }

  # Detect current session:window to never list it
  local current_session="" current_window=""
  if [[ -n "$TMUX" ]]; then
    current_session=$(tmux display-message -p '#{session_name}')
    current_window=$(tmux display-message -p '#{window_index}')
  fi

  local -a targets reasons previews
  local -A targeted
  local count=0

  # --- Phantom detection: windows where nothing was ever typed ---
  local pline sess widx cmd hist cury
  for pline in ${(f)"$(tmux list-windows -a -F '#{session_name}|#{window_index}|#{pane_current_command}|#{history_size}|#{cursor_y}')"}; do
    sess="${pline%%|*}"; pline="${pline#*|}"
    widx="${pline%%|*}"; pline="${pline#*|}"
    cmd="${pline%%|*}"; pline="${pline#*|}"
    hist="${pline%%|*}"; pline="${pline#*|}"
    cury="$pline"

    [[ "$sess" == "$current_session" && "$widx" == "$current_window" ]] && continue
    [[ "$cmd" == zsh || "$cmd" == bash || "$cmd" == sh ]] || continue
    if (( hist == 0 && cury <= 5 )); then
      count=$((count + 1))
      targets+=("${sess}:${widx}")
      reasons+=("phantom: shell with no history, cursor at line ${cury}")
      previews+=("$(tmux capture-pane -t "${sess}:${widx}" -p 2>/dev/null | sed '/^$/d' | tail -10)")
      targeted["${sess}:${widx}"]=1
    fi
  done

  # --- Stale detection: detached sessions idle beyond threshold ---
  if (( stale )); then
    local -A session_total session_phantom
    local aline
    for aline in ${(f)"$(tmux list-windows -a -F '#{session_name}|#{window_index}|#{pane_current_command}|#{history_size}|#{cursor_y}')"}; do
      sess="${aline%%|*}"; aline="${aline#*|}"
      widx="${aline%%|*}"; aline="${aline#*|}"
      cmd="${aline%%|*}"; aline="${aline#*|}"
      hist="${aline%%|*}"; aline="${aline#*|}"
      cury="$aline"
      session_total[$sess]=$(( ${session_total[$sess]:-0} + 1 ))
      if [[ "$cmd" == zsh || "$cmd" == bash || "$cmd" == sh ]] && (( hist == 0 && cury <= 5 )); then
        session_phantom[$sess]=$(( ${session_phantom[$sess]:-0} + 1 ))
      fi
    done

    local now=$(date +%s)
    local threshold_secs=$(( threshold_hours * 3600 ))
    local sline attached activity idle_secs all_shell wcmd idle_str windex

    for sline in ${(f)"$(tmux list-sessions -F '#{session_name}|#{session_attached}|#{session_activity}')"}; do
      sess="${sline%%|*}"; sline="${sline#*|}"
      attached="${sline%%|*}"; sline="${sline#*|}"
      activity="$sline"

      (( attached > 0 )) && continue
      [[ "$sess" == "$current_session" ]] && continue
      # Skip sessions fully caught by phantom detection
      (( ${session_phantom[$sess]:-0} > 0 && session_phantom[$sess] == session_total[$sess] )) && continue

      idle_secs=$(( now - activity ))
      (( idle_secs < threshold_secs )) && continue

      # Check all windows are shell-only
      all_shell=1
      for wcmd in ${(f)"$(tmux list-windows -t "$sess" -F '#{pane_current_command}')"}; do
        [[ "$wcmd" == zsh || "$wcmd" == bash || "$wcmd" == sh ]] || { all_shell=0; break; }
      done
      (( all_shell )) || continue

      idle_str=$(_tmux_idle_fmt "$activity")
      for windex in ${(f)"$(tmux list-windows -t "$sess" -F '#{window_index}')"}; do
        [[ -n "${targeted[${sess}:${windex}]}" ]] && continue
        count=$((count + 1))
        targets+=("${sess}:${windex}")
        reasons+=("stale: session detached, idle ${idle_str}, all windows shell-only")
        previews+=("$(tmux capture-pane -t "${sess}:${windex}" -p 2>/dev/null | sed '/^$/d' | tail -10)")
        targeted["${sess}:${windex}"]=1
      done
    done
  fi

  if (( count == 0 )); then
    echo "No phantom${stale:+/stale} windows found."
    return 0
  fi

  # Display candidates
  echo "Found $count candidate(s):"
  echo ""
  local i
  for i in {1..$count}; do
    printf "\033[1;33m%3d)\033[0m \033[1m%s\033[0m — %s\n" "$i" "${targets[$i]}" "${reasons[$i]}"
    if [[ -n "${previews[$i]}" ]]; then
      printf "     \033[2m┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\033[0m\n"
      while IFS= read -r pvline; do
        printf "     \033[2m│\033[0m %s\n" "$pvline"
      done <<< "${previews[$i]}"
      printf "     \033[2m┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\033[0m\n"
    fi
    echo ""
  done

  if (( dry_run )); then
    echo "(dry-run: no windows killed)"
    return 0
  fi

  # Interactive prompt
  printf "Kill? [y/N/numbers]: "
  local reply
  read -r reply

  local -a to_kill
  if [[ "$reply" == "y" || "$reply" == "Y" ]]; then
    to_kill=({1..$count})
  elif [[ "$reply" =~ ^[0-9] ]]; then
    to_kill=(${=reply})
  else
    echo "Aborted."
    return 0
  fi

  # Determine full-session kills vs individual window kills
  local -A sess_kill_n sess_win_n
  local t s
  for i in "${to_kill[@]}"; do
    (( i >= 1 && i <= count )) || continue
    t="${targets[$i]}"
    s="${t%%:*}"
    sess_kill_n[$s]=$(( ${sess_kill_n[$s]:-0} + 1 ))
  done
  for s in "${(@k)sess_kill_n}"; do
    sess_win_n[$s]=$(tmux list-windows -t "$s" 2>/dev/null | wc -l | tr -d ' ')
  done

  local killed=0
  local -A already_killed
  for i in "${to_kill[@]}"; do
    (( i >= 1 && i <= count )) || continue
    t="${targets[$i]}"
    s="${t%%:*}"
    if (( sess_kill_n[$s] >= sess_win_n[$s] )); then
      # All windows of this session selected — kill entire session
      if (( ! ${already_killed[$s]:-0} )); then
        tmux kill-session -t "$s" 2>/dev/null && {
          printf "  Killed session \033[1m%s\033[0m\n" "$s"
          killed=$((killed + 1))
        }
        already_killed[$s]=1
      fi
    else
      tmux kill-window -t "$t" 2>/dev/null && {
        printf "  Killed window \033[1m%s\033[0m\n" "$t"
        killed=$((killed + 1))
      }
    fi
  done
  echo "Killed $killed target(s)."
}

# tmux helpers: create, attach
# tmux new session: if no arg given, name from $PWD basename + timestamp via shared script
tn() {
  local name
  if [[ -n "${1:-}" ]]; then
    name="$1"
  else
    name=$("$PATH_SETUP_DIR/scripts/tmux-rename-sessions.sh" --gen-name "$PWD")
  fi
  tmux new-session -s "$name"
}
ta() {
  if [[ -n "$1" ]]; then
    # Try switch-client first (avoids nesting when already inside tmux)
    if [[ -n "$TMUX" ]] && tmux switch-client -t "$1" 2>/dev/null; then
      return 0
    fi
    # Fall back to attach-session (outside tmux, or switch-client failed)
    tmux attach-session -t "$1"
    # If we get here, inner tmux was detached/killed.
    # Signal outer tmux to auto-disable passthrough via pane title escape sequence.
    # No $TMUX guard needed: the signal is harmless outside tmux, and when called
    # over SSH, $TMUX isn't set even though the escape reaches the outer tmux.
    printf '\e]2;__PASSTHROUGH_OFF__\a'
  else
    if [[ -n "$TMUX" ]]; then
      tmux switch-client -l 2>/dev/null || tls
    else
      tmux attach-session
    fi
  fi
}

# tmux switch session (must be inside tmux)
# - with arg: switch to that session
# - no arg: spawn a fresh session in $PWD, switch to it, kill the previous one
ts() {
  if [[ -z "$TMUX" ]]; then
    echo "ts: not inside tmux (use ta to attach)" >&2
    return 1
  fi
  if [[ -n "$1" ]]; then
    tmux switch-client -t "$1"
    return
  fi
  local prev name
  prev=$(tmux display-message -p '#S')
  name=$("$PATH_SETUP_DIR/scripts/tmux-rename-sessions.sh" --gen-name "$PWD")
  tmux new-session -d -s "$name" -c "$PWD" || return
  tmux switch-client -t "$name" || { tmux kill-session -t "$name"; return 1; }
  tmux kill-session -t "$prev"
}

# tab-completion for ta/ts: complete with tmux session names
_ta() {
  local sessions=(${(f)"$(tmux list-sessions -F '#{session_name}' 2>/dev/null)"})
  _describe 'tmux session' sessions
}
compdef _ta ta
compdef _ta ts

# tmux session save/restore (for reboots)
tsave()    { "$PATH_SETUP_DIR/scripts/tmux-save.sh" "$@"; }
trestore() { "$PATH_SETUP_DIR/scripts/tmux-restore.sh" "$@"; }
tsnaps()   { "$PATH_SETUP_DIR/scripts/tmux-snapshots.sh" "$@"; }   # browse/pick snapshots; trestore -p to restore one
cresume()  { PYTHONPATH="$PATH_SETUP_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m claude_resume "$@"; }  # Claude resume table; trestore runs this itself
trename()  { "$PATH_SETUP_DIR/scripts/tmux-rename-sessions.sh" "$@"; }

# glow: render markdown filling the terminal minus a ~5-col gutter each side.
# Left gutter = the theme's document.margin (~/.config/glow/tokyo-night.json);
# right gutter = this wrap width. glow can't see our width reliably through
# tmux/pipes, so we pass -w explicitly. Acts only on a TTY and only when the
# caller didn't set their own -w/-s; passes through untouched when piped.
# tput keeps the width correct under tmux and on macOS.
glow() {
  emulate -L zsh
  local a haswidth= hasstyle=
  for a in "$@"; do
    case $a in
      -w*|--width*) haswidth=1 ;;
      -s*|--style*) hasstyle=1 ;;
    esac
  done
  local -a extra
  if [[ -t 1 && -z $haswidth ]]; then
    local cols=${COLUMNS:-0}
    (( cols < 20 )) && cols=$(tput cols 2>/dev/null || printf 80)
    extra+=(-w $(( cols > 15 ? cols - 5 : cols )))
  fi
  local theme="${XDG_CONFIG_HOME:-$HOME/.config}/glow/tokyo-night.json"
  [[ -z $hasstyle && -r $theme ]] && extra+=(-s "$theme")
  command glow "${extra[@]}" "$@"
}

# WezTerm without tmux (overrides default_prog)
alias wez='wezterm start -- /bin/zsh &'

# ---------------------------------------------------------------------------- #
#                                     PATH                                     #
# ---------------------------------------------------------------------------- #

path_prepend "/usr/local/cuda-12.2/bin"

# Snap bindir. Ubuntu injects /snap/bin only via /etc/profile.d/apps-bin-path.sh,
# which runs for login shells. WezTerm starts tmux with `zsh -c` (non-login), so
# that injector never fires and /snap/bin drops off — snaps like glow become
# unreachable. Add it back explicitly; path_append's -d guard makes it a no-op
# on machines without snap (e.g. macOS).
path_append "/snap/bin"

# ---------------------------------------------------------------------------- #
#                                   GPU Cuda                                   #
# ---------------------------------------------------------------------------- #

# Append CUDA library paths if available
for cuda_lib in /usr/lib/cuda/lib64 /usr/lib/cuda/include /usr/local/cuda-12.2/lib64; do
  [[ -d $cuda_lib ]] && export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:$cuda_lib"
done

# ---------------------------------------------------------------------------- #
#                                     START                                    #
# ---------------------------------------------------------------------------- #

# Machine-specific PATH tweaks
# ZSHRC_LOCAL_START_PATH_APPEND (array, set in ~/.zshrc.local) covers boxes
# whose extra PATH entries would otherwise name the machine here.
if (( ${#ZSHRC_LOCAL_START_PATH_APPEND[@]} )); then
  path_append "${ZSHRC_LOCAL_START_PATH_APPEND[@]}"
fi
case $WHICH_COMPUTER in
  MacBook|MacBook_Heuritech)
    path_prepend "/opt/homebrew/opt/libpq/bin"
    path_prepend "/opt/homebrew/opt/coreutils/libexec/gnubin"
    path_append  "/Applications/Docker.app/Contents/Resources/bin" ;;
esac

if [[ $WHICH_COMPUTER == "MacBook" ]] || [[ $WHICH_COMPUTER == "MacBook_Heuritech" ]]; then
    path_prepend "/opt/homebrew/opt/grep/libexec/gnubin"
fi


# ZSHRC_LOCAL_CUDA_TOOLCHAIN (set in ~/.zshrc.local) opts a machine into the
# rust/system-python/dart toolchain setup below.
if [[ -n "${ZSHRC_LOCAL_CUDA_TOOLCHAIN:-}" ]]; then
    . "$HOME/.cargo/env"
    export PYTHON_INSTALLED="/usr/bin/python3"
    export SYSTEM_PYTHON="/usr/bin/python3"
    export PATH="$PATH:/usr/lib/dart/bin"
fi

if [[ $WHICH_COMPUTER == "MacBook" ]] || [[ $WHICH_COMPUTER == "MacBook_Heuritech" ]]; then
    # From: https://superuser.com/questions/399594/color-scheme-not-applied-in-iterm2
    # Set CLICOLOR if you want Ansi Colors in iTerm2 
    export CLICOLOR=1
    # Set colors to match iTerm2 Terminal Colors
    export TERM=xterm-256color
    . "$HOME/.local/bin/env"

    # The following lines have been added by Docker Desktop to enable Docker CLI completions.
    fpath=(/Users/ezalos/.docker/completions $fpath)
    autoload -Uz compinit
    compinit
    # End of Docker CLI completions
fi


if [[ $WHICH_COMPUTER == "MacBook_Heuritech" ]]; then
    # rosetta terminal setup
    if [ $(arch) = "i386" ]; then
        alias brew86="/usr/local/bin/brew"
        alias pyenv86="arch -x86_64 pyenv"
    fi
fi

# This is zsh-specific syntax for rebuilding the PATH environment variable from an array.
export PATH=${(j.:.)path}
# zprof

# Lazy-load NVM to avoid ~1.5 s startup penalty on every shell
export NVM_DIR="$HOME/.nvm"
lazy_load_nvm() {
  unset -f nvm node npm npx
  [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
  [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
}
nvm()  { lazy_load_nvm && nvm  "$@"; }
node() { lazy_load_nvm && node "$@"; }
npm()  { lazy_load_nvm && npm  "$@"; }
npx()  { lazy_load_nvm && npx  "$@"; }

# ZSHRC_LOCAL_NVM_EXTRA_PATH (set in ~/.zshrc.local) + MacBook: add nvm node
# binaries to PATH so non-interactive tools (make, /bin/sh) can find globally
# installed packages like marp without triggering lazy-load
if [[ -n "${ZSHRC_LOCAL_NVM_EXTRA_PATH:-}" || $WHICH_COMPUTER == "MacBook" ]]; then
    local _nvm_node_dirs=("$NVM_DIR"/versions/node/*(On))
    if (( ${#_nvm_node_dirs} )); then
        path_append "${_nvm_node_dirs[1]}/bin"
    fi
fi

# Claude Code
claude() { command claude --enable-auto-mode --rc "$@"; }
# claudetg disabled 2026-05-28: telegram channel leaked 100%-CPU `bun server.ts` orphans on session crash. Plugin also off in claude_settings; notify-louis (outbound) unaffected.
# claudetg() { command claude --enable-auto-mode --channels plugin:telegram@claude-plugins-official --rc "$@"; }

# bun completions
[ -s "/home/ezalos/.bun/_bun" ] && source "/home/ezalos/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

# ---------- setup_notices ----------
# Per-machine first-time-setup reminders. See: ~/Setup/setup_notices/README.md

SETUP_NOTICES_DIR="$HOME/Setup/setup_notices"
SETUP_NOTICES_ACK_DIR="$HOME/.cache/setup_notices"

# Prints each pending notice ID and its notice file path, tab-separated,
# one per line. Silent if none pending.
_setup_notices_list_pending() {
  [[ ! -d "$SETUP_NOTICES_DIR" ]] && return 0
  mkdir -p "$SETUP_NOTICES_ACK_DIR"
  local f id
  for f in "$SETUP_NOTICES_DIR"/20*.md(N); do
    id="$(awk -F': *' '/^id:/ {print $2; exit}' "$f")"
    [[ -z "$id" ]] && continue
    [[ -f "$SETUP_NOTICES_ACK_DIR/$id.acked" ]] && continue
    printf '%s\t%s\n' "$id" "$f"
  done
}

# Counts pending notices.
_setup_notices_count_pending() {
  _setup_notices_list_pending | grep -c .
}

setup_notices() {
  local cmd="${1:-list}"
  shift 2>/dev/null

  case "$cmd" in
    list|'')
      local pending
      pending=$(_setup_notices_list_pending)
      if [[ -z "$pending" ]]; then
        echo "No pending setup notices."
        return 0
      fi
      echo "Pending setup notices:"
      local id f summary
      while IFS=$'\t' read -r id f; do
        summary="$(awk -F': *' '/^summary:/ {print $2; exit}' "$f")"
        printf '\n  [%s] %s\n' "$id" "$summary"
        printf '    Doc:  %s\n' "$f"
        printf '    Run:  setup_notices run %s\n' "$id"
        printf '    Or:   setup_notices show %s  (then ack manually)\n' "$id"
      done <<< "$pending"
      ;;

    all)
      local f id summary acked marker
      for f in "$SETUP_NOTICES_DIR"/20*.md(N); do
        id="$(awk -F': *' '/^id:/ {print $2; exit}' "$f")"
        summary="$(awk -F': *' '/^summary:/ {print $2; exit}' "$f")"
        if [[ -f "$SETUP_NOTICES_ACK_DIR/$id.acked" ]]; then
          marker='[x]'
        else
          marker='[ ]'
        fi
        printf '%s %s — %s\n' "$marker" "$id" "$summary"
      done
      ;;

    show)
      local id="$1"
      [[ -z "$id" ]] && { echo "Usage: setup_notices show <id>" >&2; return 1 }
      local f
      f="$(_setup_notices_find "$id")" || {
        echo "setup_notices: no notice with id '$id'" >&2; return 1
      }
      ${PAGER:-less} "$f"
      ;;

    run)
      local id="$1"
      [[ -z "$id" ]] && { echo "Usage: setup_notices run <id>" >&2; return 1 }
      local f
      f="$(_setup_notices_find "$id")" || {
        echo "setup_notices: no notice with id '$id'" >&2; return 1
      }
      local ack_cmd
      ack_cmd="$(awk -F': *' '/^ack_cmd:/ {sub(/^ *ack_cmd: */,""); print; exit}' "$f")"
      if [[ -z "$ack_cmd" ]]; then
        echo "setup_notices: notice '$id' has no ack_cmd; use 'setup_notices ack $id' after you finish manually" >&2
        return 1
      fi
      # Expand ~ in the command
      ack_cmd="${ack_cmd/#\~/$HOME}"
      echo "setup_notices: running '$ack_cmd'"
      if eval "$ack_cmd"; then
        mkdir -p "$SETUP_NOTICES_ACK_DIR"
        touch "$SETUP_NOTICES_ACK_DIR/$id.acked"
        echo "setup_notices: acked '$id'"
      else
        echo "setup_notices: ack_cmd failed, notice NOT acked" >&2
        return 1
      fi
      ;;

    ack)
      local id="$1"
      [[ -z "$id" ]] && { echo "Usage: setup_notices ack <id>" >&2; return 1 }
      _setup_notices_find "$id" >/dev/null || {
        echo "setup_notices: no notice with id '$id'" >&2; return 1
      }
      mkdir -p "$SETUP_NOTICES_ACK_DIR"
      touch "$SETUP_NOTICES_ACK_DIR/$id.acked"
      echo "setup_notices: acked '$id'"
      ;;

    unack)
      local id="$1"
      [[ -z "$id" ]] && { echo "Usage: setup_notices unack <id>" >&2; return 1 }
      rm -f "$SETUP_NOTICES_ACK_DIR/$id.acked"
      echo "setup_notices: unacked '$id'"
      ;;

    *)
      echo "Usage: setup_notices [list|all|show <id>|run <id>|ack <id>|unack <id>]" >&2
      return 1
      ;;
  esac
}

# Find the notice file for an id; print its path or return non-zero.
_setup_notices_find() {
  local id="$1"
  local f
  for f in "$SETUP_NOTICES_DIR"/20*.md(N); do
    local this_id
    this_id="$(awk -F': *' '/^id:/ {print $2; exit}' "$f")"
    if [[ "$this_id" == "$id" ]]; then
      echo "$f"
      return 0
    fi
  done
  return 1
}


# ---------- grab: reverse file fetch over SSH ----------
# See: ~/Setup/plans/2026_04_12-spec_grab_reverse_fetch.md

GRAB_ENABLED_HOSTS=(
  rnd1 rnd2 rnd3 rnd4
  smic
)
# ZSHRC_LOCAL_GRAB_HOSTS_EXTRA (array, set in ~/.zshrc.local) adds this box's
# own named hosts without naming them in the public file.
(( ${#ZSHRC_LOCAL_GRAB_HOSTS_EXTRA[@]} )) && GRAB_ENABLED_HOSTS+=("${ZSHRC_LOCAL_GRAB_HOSTS_EXTRA[@]}")
# MacBook and MacBook_Heuritech are excluded — they currently accept no
# inbound SSH. Add them here if that ever changes.

GRAB_RECEIVER_PORT=19923

_grab_receiver_ensure() {
  local pid_file="$HOME/.cache/grab-receiver.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
    return 0
  fi
  mkdir -p "$HOME/.cache" "$HOME/Downloads/grab"
  nohup python3 "$HOME/Setup/scripts/grab-receiver.py" \
    --port "$GRAB_RECEIVER_PORT" \
    >"$HOME/.cache/grab-receiver.log" 2>&1 &!
  # Give it a moment to bind the port
  local i
  for i in 1 2 3 4 5; do
    [[ -f "$pid_file" ]] && return 0
    sleep 0.1
  done
  return 0  # don't block ssh if receiver is slow; `grab` will fail cleanly
}

# Parses ssh argv. On interactive invocations (no trailing remote command
# and no explicit non-interactive flag), prints the destination host alias
# and returns 0. Otherwise returns non-zero and prints nothing.
#
# Handles OpenSSH single-letter flags that take an argument (`-p 2222`,
# `-i ~/key`, `-L 8080:host:80`, `-o Key=val`, etc.), including the
# attached form (`-p2222`).
#
# Flags that imply "not an interactive shell" cause a non-zero return:
#   -T (disable pseudo-tty), -N (no command at all), -f (background),
#   -G (print config), -W (stdio forward).
_ssh_parse() {
  # Flags that expect an argument (OpenSSH man page summary).
  local argflags="BbcDEeFIiJLlmOopQRSWw"
  local expect_arg=0
  local host=""
  local arg c
  for arg in "$@"; do
    if (( expect_arg )); then
      expect_arg=0
      continue
    fi
    case "$arg" in
      -T|-N|-f|-G|-W) return 1 ;;
      --) continue ;;  # explicit end-of-options marker
    esac
    if [[ "$arg" == -* && "$arg" != "-" ]]; then
      c="${arg[2]}"  # second character (zsh indexing is 1-based)
      if [[ -n "$c" && "$argflags" == *"$c"* ]]; then
        if (( ${#arg} == 2 )); then
          expect_arg=1
        fi
        # else: attached value (`-p2222`), no expect_arg
      fi
      continue
    fi
    # Bare argument
    if [[ -z "$host" ]]; then
      host="$arg"
    else
      # Second bare arg = remote command → not interactive
      return 1
    fi
  done
  [[ -z "$host" ]] && return 1
  # Strip user@ prefix if present
  host="${host##*@}"
  echo "$host"
  return 0
}

# ssh wrapper — for interactive invocations to hosts in GRAB_ENABLED_HOSTS,
# set up a per-session reverse Unix-socket tunnel and inject GRAB_SOCK into
# the remote shell so `grab` works there. Otherwise pass through unchanged.
ssh() {
  # Fast path 1: stdin/stdout not a TTY → scripted use, don't wrap.
  if [[ ! -t 0 || ! -t 1 ]]; then
    command ssh "$@"
    return
  fi
  local host
  if ! host="$(_ssh_parse "$@")"; then
    command ssh "$@"
    return
  fi
  # Opt-in allowlist check (zsh [(Ie)…] lookup returns 0 if not found).
  if (( ! ${GRAB_ENABLED_HOSTS[(Ie)$host]} )); then
    command ssh "$@"
    return
  fi
  _grab_receiver_ensure
  local sock="/tmp/grab-${USER}-$$-${RANDOM}.sock"
  # Clean up any leftover with the same name (extremely unlikely, but safe).
  rm -f "$sock"
  command ssh \
    -R "$sock:127.0.0.1:$GRAB_RECEIVER_PORT" \
    -t "$@" \
    "export GRAB_SOCK='$sock'; exec \$SHELL -l"
}

grab() {
  if [[ -z "$GRAB_SOCK" ]]; then
    echo "grab: not in a grab-enabled ssh session (GRAB_SOCK unset)" >&2
    echo "      re-ssh using your zshrc's ssh wrapper, and ensure the host" >&2
    echo "      is in GRAB_ENABLED_HOSTS on the local side." >&2
    return 1
  fi
  if (( $# == 0 )); then
    echo "Usage: grab <file-or-dir> [...]" >&2
    return 1
  fi
  if ! command -v socat >/dev/null 2>&1; then
    echo "grab: socat not found on this machine — install it (setup_notices show grab_setup)" >&2
    return 1
  fi
  local target
  for target in "$@"; do
    if [[ ! -e "$target" ]]; then
      echo "grab: $target: no such file or directory" >&2
      return 1
    fi
  done
  local dir base
  for target in "$@"; do
    dir="$(dirname -- "$target")"
    base="$(basename -- "$target")"
    # `ch` = create + dereference symlinks. Many files in Louis's setup are
    # symlinks managed by src_dotfiles; sending their content (not the link)
    # is what's actually useful on the receiving machine.
    if ! tar ch -C "$dir" -- "$base" | socat - UNIX-CONNECT:"$GRAB_SOCK"; then
      echo "grab: transfer failed for $target" >&2
      return 1
    fi
    echo "grab: sent $target"
  done
}

_setup_notices_check() {
  local n
  n="$(_setup_notices_count_pending)"
  if (( n > 0 )); then
    echo "⚠️  $n pending setup notice(s): run \`setup_notices\` to view"
  fi
}
_setup_notices_check

# Social content repo (used by /post, /log, /audit skills)
export SOCIAL_HOME="$HOME/42/social"

# rip2: same-filesystem graveyard (default /tmp graveyard is cross-fs from /home)
export GRAVEYARD="$HOME/.local/share/graveyard"

-- ABOUTME: WezTerm terminal configuration with Nerd Font and Shift+Enter support.
-- ABOUTME: Optimized for Claude Code CLI usage.

local wezterm = require 'wezterm'
local config = wezterm.config_builder()

-- Font: MesloLGS NF (Nerd Font) with symbol and emoji fallbacks
config.font = wezterm.font_with_fallback({
  'MesloLGS NF',
  'Noto Sans Symbols2',
  'Noto Color Emoji',
})
config.font_size = 12.0

-- Dark purple background, matching iTerm "Dark" profile:
--   color   = Display P3 0.1793,0.1194,0.3666 → sRGB #301E61
--   opacity = 0.90 (iTerm reports ~0.899, rounded)
--   blur    = iTerm Blur Radius 10 (macOS only)
config.window_background_opacity = 0.90
config.macos_window_background_blur = 10
config.colors = {
  background = '#301E61',
}

-- Window
config.enable_tab_bar = false
config.window_padding = {
  left = 8,
  right = 8,
  top = 8,
  bottom = 8,
}

-- Scroll
config.scrollback_lines = 10000

-- Auto-tmux: each WezTerm window opens its own tmux session (w-MMDD-HHhMM)
-- Strips Cursor IDE AppImage paths from LD_LIBRARY_PATH before starting tmux,
-- because the tmux server inherits the environment of whoever started it first.
-- Cursor's bundled glibc breaks rustup's argv[0] detection (and possibly other tools).
config.default_prog = { '/bin/zsh', '-c', [[
  # GUI launches (macOS app icon, some Linux .desktop entries) hand zsh a
  # minimal PATH and `zsh -c` does not source .zprofile/.zshrc, so tools
  # like tmux can be unreachable and the exec below would fail with 127.
  # Prepend common package-manager bindirs that actually exist on this
  # machine; the case-guard de-dupes when they're already present, and
  # the directory test makes this a no-op on machines where they aren't.
  for p in /opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin /usr/local/sbin; do
    case ":$PATH:" in
      *":$p:"*) ;;
      *) [ -d "$p" ] && PATH="$p:$PATH" ;;
    esac
  done
  export PATH

  # Sanitize LD_LIBRARY_PATH: strip Cursor AppImage mount paths
  if [ -n "$LD_LIBRARY_PATH" ]; then
    cleaned=$(printf '%s' "$LD_LIBRARY_PATH" | tr ':' '\n' | grep -v '^$' | grep -v '\.mount_cursor' | paste -sd ':')
    [ -n "$cleaned" ] && export LD_LIBRARY_PATH="$cleaned" || unset LD_LIBRARY_PATH
  fi

  # If tmux server already exists, clean its global environment too
  if tmux has-session 2>/dev/null; then
    tmux set-environment -gu LD_LIBRARY_PATH 2>/dev/null
  fi

  sn="w-$(date +%m%d-%Hh%M)"
  if tmux has-session -t "$sn" 2>/dev/null; then
    i=2
    while tmux has-session -t "${sn}-${i}" 2>/dev/null; do ((i++)); done
    sn="${sn}-${i}"
  fi
  # No exec: if tmux can't start or attach (e.g. client/server version mismatch right
  # after a tmux upgrade), keep the window alive with a visible error + plain shell
  # instead of closing it the instant it opens.
  tmux new-session -s "$sn"
  rc=$?
  if [ $rc -ne 0 ]; then
    print "tmux new-session exited $rc (client: $(tmux -V 2>/dev/null), server: $(tmux display-message -p 'tmux #{version}' 2>/dev/null || print unreachable))"
    print "dropping to a plain zsh - fix tmux, then open a new window"
    exec /bin/zsh -l
  fi
]] }

-- Key bindings: Shift+Enter sends CSI u sequence for Claude Code newline support
config.keys = {
  {
    key = 'Enter',
    mods = 'SHIFT',
    action = wezterm.action.SendString('\x1b[13;2u'),
  },
}

-- macOS only: Option+Arrow → shell word-nav (readline \eb/\ef), which tmux passes
-- through untouched. fn+Option+Arrow already arrives as Shift+Alt+Arrow (\e[1;4X),
-- handled by the tmux.conf S-M-Arrow pane-switch bindings.
if wezterm.target_triple:find('darwin') then
  table.insert(config.keys, {
    key = 'LeftArrow',
    mods = 'OPT',
    action = wezterm.action.SendString('\x1bb'),
  })
  table.insert(config.keys, {
    key = 'RightArrow',
    mods = 'OPT',
    action = wezterm.action.SendString('\x1bf'),
  })
end

return config

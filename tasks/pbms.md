# Problems / out-of-scope findings

## 2026-04-17 — wezterm.lua still uses old session-naming scheme

**Context:** While working on the Option+Arrow key binding fix in `dotfiles/wezterm.lua`, noticed that `default_prog` at `dotfiles/wezterm.lua:48` still generates tmux session names with the legacy pattern `w-$(date +%m%d-%Hh%M)`. This was superseded by the new `{basename}@YYYY-MM-DD-HHhMM` scheme shipped in commits `f3d2ee0..866a0f6`.

**Observation:** `dotfiles/wezterm.lua:48` — `sn="w-$(date +%m%d-%Hh%M)"`

**Status:** Open. Local WezTerm launches (not SSH-ed into) will still create old-pattern names; `trename` sweeps them up, but it's an extra step that wouldn't be needed if this path generated the new scheme directly (e.g., by calling `scripts/tmux-rename-sessions.sh --gen-name "$PWD"`).

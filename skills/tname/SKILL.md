---
name: tname
description: Use when Louis types /tname, or asks to rename, label or retitle this session's tmux tab or window.
---

# tname: rename this session's tmux tab

1. **The name.** If Louis gave one, use it as given. Otherwise make one from what this
   session is working on (its goal or lane, not the repo or folder name): 1-2 words,
   12 characters at most, lowercase, words joined by `-`. Example: `val-queue`.
2. **Rename the window that holds this session's pane**, never the one Louis is looking
   at:

   ```bash
   tmux rename-window -t "$TMUX_PANE" "<name>"
   ```

   Always pass `-t "$TMUX_PANE"`. Without it tmux renames the client's current window,
   which may be another session's tab. If `$TMUX_PANE` is empty, this session is not in
   tmux: say so and stop.
3. **Check it** and report in one line, `tab <index> renamed to <name>`:

   ```bash
   tmux display-message -p -t "$TMUX_PANE" '#{window_index} #W'
   ```

The rename also turns automatic renaming off for that window, so the name stays.

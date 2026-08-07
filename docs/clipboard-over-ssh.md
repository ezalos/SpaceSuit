<!-- ABOUTME: How clipboard-over-SSH works here (OSC 52 + tmux forwarding) and how to make it reliable. -->
<!-- ABOUTME: Written after verifying the mechanism against tmux 3.2a vs 3.5a on 2026-07-06. -->

# Copy over SSH (OSC 52 through nested tmux)

`copy` (a shell function in `.zshrc`) sends clipboard data via an **OSC 52** terminal
escape. Over SSH there is no local X server, so `xclip`/`pbcopy` on the remote box can't
reach your laptop — but an OSC 52 escape rides the terminal stream back out to whatever
terminal you're actually looking at, which sets the real clipboard. tmux sits in the
middle and must **forward** that escape outward.

## The catch: tmux must be >= 3.3, on every layer

Forwarding an application's OSC 52 to the outer terminal was added in **tmux 3.3**.
Verified empirically (2026-07-06, pty harness, app emits OSC 52, measured what the outer
terminal receives):

| tmux | captures into its own buffer | **forwards to outer terminal** |
|---|---|---|
| 3.2a | yes | **no — never** (no config changes this) |
| 3.5a | yes | **yes** (chains through 2 nested layers; bare `set-clipboard on` is enough) |

Your setup is **nested**: WezTerm → Mac tmux → ssh → TheBeast tmux. `copy` runs on
TheBeast (innermost), so **every** tmux layer between it and WezTerm must be >= 3.3 or the
escape is dropped. As of 2026-07-06, TheBeast ran tmux **3.2a** — that was the whole
reason `copy` silently failed.

## Config (already in `.tmux.conf`, deployed everywhere)

```
set -g set-clipboard on
set -as terminal-features ',*:clipboard'
```

`set-clipboard on` is the essential bit; the `terminal-features` line is belt-and-suspenders
so the last hop (outer tmux → WezTerm) forwards even if the terminal isn't auto-detected.

## Making it reliable (do this once per machine in the chain)

1. **Upgrade tmux to >= 3.3 on every machine in the chain** (TheBeast *and* the Mac):
   - Run `scripts/install-tmux.sh` (macOS → Homebrew; Linux → builds 3.5a into `~/.local`,
     leaving the system tmux untouched). Needs `libevent-dev` on Linux (one `sudo apt`).
   - `~/.local/bin` is already ahead of `/usr/bin` in PATH, so the new tmux wins.
2. **Restart each tmux server** to adopt the new binary (a running server keeps the old one).
   This ends that machine's sessions — save with tmux-resurrect first if you use it, then
   `tmux kill-server` and start fresh. On the Mac, also `git pull` in Setup first so its
   `.tmux.conf` has the clipboard settings.
3. **Verify** on each: `tmux -V` (>= 3.3) and `tmux show -gv set-clipboard` (`on`).

## Test end-to-end

In your SSH session on TheBeast, after `sc` (reloads the `copy` function):
```
printf 'MARKER_7f3a' | copy
```
Paste on your Mac. If `MARKER_7f3a` appears, the whole chain works. If it pastes only
inside tmux but not on the Mac, a tmux layer is still old/unforwarding. If nothing at all,
WezTerm is refusing OSC 52 writes (enable it in `wezterm.lua`).

## Immediate fallback (no tmux upgrade)

To copy a remote file to the Mac clipboard right now, run **on the Mac**:
```
ssh <thebeast-host> 'cat /path/on/thebeast' | pbcopy
```
This uses the Mac's own clipboard directly and needs none of the tmux machinery.

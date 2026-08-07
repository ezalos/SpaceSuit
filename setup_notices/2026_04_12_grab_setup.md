---
id: grab_setup
summary: grab command — install runtime dependencies on this machine
ack_cmd: ~/Setup/scripts/grab-check-deps.sh
---

# grab_setup

The `grab` command pulls files from a remote SSH session back to
`~/Downloads/grab/` on the local machine. It needs two runtime
dependencies on this machine: `socat` and `python3`.

## One-shot install (copy-paste block)

Linux (Debian/Ubuntu):

```bash
sudo apt install -y socat python3 && setup_notices ack grab_setup
```

macOS:

```bash
brew install socat && setup_notices ack grab_setup
# (python3 is usually already available; confirm with `python3 --version`)
```

Already have the deps? Verify + ack in one step:

```bash
setup_notices run grab_setup
```

## What this does

- `socat` — used by the `grab` function (remote) to pipe a tar stream to
  the Unix-socket endpoint of the reverse tunnel.
- `python3` — runs the local receiver daemon (`scripts/grab-receiver.py`),
  which the `ssh` wrapper auto-starts on demand.

No SSH config changes are needed; the per-session reverse tunnel is set
up dynamically by the `ssh` wrapper in zshrc.

## Why

See spec: `~/Setup/plans/2026_04_12-spec_grab_reverse_fetch.md`.
Plan:      `~/Setup/plans/2026_04_12-plan_grab_reverse_fetch.md`.

## See also

- `dotfiles/.zshrc` — `ssh` wrapper, `grab` function, `GRAB_ENABLED_HOSTS`
- `scripts/grab-receiver.py` — local daemon (auto-started by the wrapper)
- `scripts/grab-check-deps.sh` — this notice's ack command

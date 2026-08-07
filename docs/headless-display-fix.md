<!-- ABOUTME: Remediation for TheBeast having no connected display output, which stops -->
<!-- ABOUTME: GPU apps (games, Sunshine capture) from starting. Needs sudo: Louis-only. -->

# TheBeast: no connected display output (2026-08-03)

## Symptom

After the 2026-08-03 reboot, nothing GPU-visual can start. Teardown's own log:

```
Display resolution: 0x0
ERROR [Init] Display settings are invalid and will be reset to default
```

The process starts, allocates a few 1x1 helper windows, and never maps a real one.

## Diagnosis

No output is connected to the GPU:

```
$ xrandr | grep -E "connected"
HDMI-0 disconnected
DP-0 disconnected primary
DP-1 disconnected
$ nvidia-smi --query-gpu=display_active,display_mode --format=csv,noheader
Disabled, Disabled
```

X still serves a framebuffer (currently 1920x1080), so **screenshots succeed and every
process-level check reports healthy** — the framebuffer is a phantom with no output
behind it. That is what makes this fail silently rather than loudly.

This also affects the Sunshine bridge, which needs a real display to capture.

## What was already tried WITHOUT sudo (all failed)

- `xrandr --setmonitor VIRTUAL-1 1920/520x1080/290+0+0 none` — creates a monitor entry,
  but the game still reads 0x0.
- `xrandr --newmode` + `--addmode DP-0` — `BadMatch`. The NVIDIA driver refuses to add a
  mode to a disconnected output without the X option below.
- `xrandr --fb 1920x1080` — resizes the phantom framebuffer only.

## Fix (needs sudo, so Louis runs it)

Cheapest if you ever regain physical access: **power the monitor on / reseat the cable**,
or fit a **dummy HDMI/DP plug** (~5 EUR, permanent fix, survives reboots).

Otherwise force a virtual output. Create the drop-in:

```bash
sudo tee /etc/X11/xorg.conf.d/10-headless-nvidia.conf >/dev/null <<'CONF'
Section "Device"
    Identifier     "nvidia-headless"
    Driver         "nvidia"
    Option         "AllowEmptyInitialConfiguration" "true"
    Option         "ConnectedMonitor" "DFP-0"
EndSection
CONF
```

Then restart the graphical session:

```bash
sudo systemctl restart gdm3
```

GDM autologin brings X and Sunshine back unattended (that is why autologin was enabled).
Expect the Moonlight stream to drop and return.

## Verify

```bash
DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority xrandr | grep connected   # want a CONNECTED output
nvidia-smi --query-gpu=display_active --format=csv,noheader                   # want Enabled
~/42/TheHarness/monitoring/thebeast/scripts/check-streaming-stack.sh
```

Note the X display number moves between `:0` and `:1` depending on greeter vs autologin —
check `ls /tmp/.X11-unix/` and match the Sunshine override to it.

## Why this matters beyond gaming

The Teardown RL harness (`~/Work/Teardown`) is otherwise ready for unattended runs:
process supervision, checkpointing, crash recovery, display detection, visibility
guards. It is blocked solely on a real display output.

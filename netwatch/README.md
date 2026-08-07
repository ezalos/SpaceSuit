# netwatch

An append-only black box for a home network. It answers two questions that are
otherwise impossible to settle after the fact:

1. **"Did the internet actually go down, and whose fault was it?"**
2. **"Is my server ruining the network for everyone else in the house?"**

Nothing here is specific to one ISP or router. Router-restart detection uses
NAT-PMP epoch (RFC 6886) and SSDP `BOOTID.UPNP.ORG`, which essentially every
consumer gateway speaks. A vendor probe (currently SFR/neufbox) is optional
enrichment and never required.

Stdlib Python only. No sudo, no packet capture, no dependencies.

## Use

```sh
netwatch probe                 # one-shot: what do we see right now?
netwatch report --days 7       # the outage + router-restart ledger
netwatch impact --days 7       # is THIS host degrading everyone else's latency?
netwatch mark "before move"    # annotate the ledger around a change
netwatch bufferbloat           # measure the uplink and grade our effect on it
netwatch status                # the same numbers as `report`, as JSON
netwatch publish               # push this host's summary to the dashboard host
```

`status` and `publish` feed the dashboard at **https://network.develle.fr**
(Google SSO). `summarize()` backs both the terminal report and the page, so the
two can't drift apart. `publish` writes `<hostname>.json` into the web root —
over ssh for `host:/path`, or directly when `--remote` is a bare local path (the
Pi both serves the page and publishes into it). A systemd timer runs it every
60s on each host; the page flags a host as stale after 5 minutes.

`bufferbloat` is the one to run **before** putting this machine on someone
else's network. It measures the uplink, saturates it while sampling latency, and
grades the damage in absolute added milliseconds — then prints a correctly sized
`tc ... cake` command, but only when shaping is actually warranted. It refuses to
grade at all if the load generator failed, because a measurement that silently
uploaded nothing would otherwise score a perfect A.

Bufferbloat is set by the **uplink**, not by the machine: a 20–50 Mb/s stream is
nothing on 600 Mb/s fibre and is a household-wrecking flood on VDSL. So the
verdict never transfers between networks — re-run it on each one.

Running as a service (already installed on TheBeast):

```sh
systemctl --user status netwatch
systemctl --user restart netwatch
```

## What it records

Every 5 seconds, to `~/.local/state/netwatch/`:

| field | why it's there |
|---|---|
| carrier / operstate | distinguishes a dead cable from a dead internet |
| gateway RTT + loss | is the router itself alive? |
| WAN RTT + loss (2 targets) | is the internet alive? two targets so one dead host isn't an "outage" |
| DNS resolve time | catches the failure where packets flow but nothing resolves |
| NAT-PMP epoch | **router uptime, from the router's own counter** |
| SSDP BOOTID | second, independent restart detector |
| this host's rx/tx bps | the "was it me?" column |
| conntrack count | connection-table pressure this host is generating |

Two files:

- `events.jsonl` — transitions only. Small, permanent. **This is the evidence.**
- `samples.jsonl` — raw detail, rotated daily and gzipped, 14 days kept.

## How it can claim a guarantee

"No outages recorded" and "the recorder was dead" produce identical ledgers. So
netwatch never asserts health from silence. Three things close that hole:

1. **Coverage accounting.** `report` leads with the fraction of the window the
   recorder was demonstrably awake, enumerates every blind period, and refuses a
   clean bill below 99%. Proof of life is pooled from heartbeats, events and raw
   samples, so periods recorded before heartbeats existed still count.
2. **Sub-interval flap detection.** Sampling at 5s cannot see a 3–4s link flap —
   but the kernel's `carrier_changes` counter still moved. A delta proves the
   link bounced even when we never observed `carrier=0`.
3. **A dead-man's switch.** Every 5 minutes netwatch pings an external sink
   (healthchecks.io) *from inside its own loop*, so pings stop if the recorder
   wedges, not merely if the host dies. Missed pings alert externally. This is
   what carries the guarantee — by design there is no routine all-clear digest,
   because a daily "everything is fine" message gets ignored within a week.

## Alerts

Alerts fire only when something is wrong. There is no digest.

| trigger | why it earns an interrupt |
|---|---|
| outage ≥15s | the network actually broke |
| router restart | the box rebooted; NAT-PMP epoch proves it |
| bad neighbour | **our own** load is degrading everyone else's latency |
| missed heartbeat | via healthchecks.io — the recorder or the host is gone |
| public IP change | anything pinned to the old address just broke |

The public-IP check runs hourly (`NETWATCH_TRACK_PUBLIC_IP=1`) against several
providers, so one provider being unreachable cannot masquerade as a change. The
last known address is persisted, so a restart neither re-alerts nor loses the
baseline, and a change that happened while the recorder was down is still caught
on the next check. On a change it re-runs `scripts/check-home-ip.sh` immediately,
so the new address enters the git content-scrub block list right away instead of
waiting for that script's own 6-hourly cron.

The bad-neighbour alert exists because bufferbloat is invisible to the person
causing it: their transfer runs at full speed while everyone else's calls
stutter. In a shared home, the person suffering will not file a bug report.

Bursts coalesce: at most 2 messages per 15 minutes, then a count. A router
crash-looping three times in an hour must not train you to ignore the alerts.

## Deployment

Two hosts run it, which is what makes an event corroborated rather than
asserted. Per-host settings live in `~/.config/netwatch/env` (mode 0600, outside
git — the ping URL is a capability that can forge liveness):

```sh
NETWATCH_HEALTHCHECK_URL=https://hc-ping.com/<uuid>
NETWATCH_PEER=<host>.<your-tailnet>.ts.net
NETWATCH_PUBLISH_REMOTE=TinyButMighty_ts:/srv/network
NETWATCH_ARGS=--no-vendor
NETWATCH_TRACK_PUBLIC_IP=1
```

**Use tailnet names, not LAN addresses**, for both the peer and the publish
target. The two recorders are meant to end up in different houses, and a
`192.168.1.x` peer stops resolving the moment one of them moves — which is
exactly when an independent witness becomes worth having. Tailscale still takes
the direct LAN hop while they share a network, so it costs about 0.8 ms.

`systemd` word-splits `$NETWATCH_ARGS`, so one unit file serves every host.
TinyButMighty runs `--no-vendor`: two machines polling a fragile router's admin
API doubles a load already suspected of aggravating its crashes.

## Classification

State is reduced to one label, ordered most-local-cause-first, so the blame
column can't be gamed by a downstream symptom:

| state | means |
|---|---|
| `link_down` | cable/switch port or router LAN side |
| `no_route` | local config — no default route |
| `gateway_down` | the router itself |
| `wan_down` | ISP / upstream (router alive, internet not) |
| `wan_degraded` | partial upstream loss |
| `dns_down` | resolver only — packets still flowed |

## Why NAT-PMP epoch is the good detector

Everything else you can observe from a LAN device is ambiguous: a link flap
looks the same whether the cable moved, the switch port reset, or the router
rebooted. The NAT-PMP epoch is the *router's own* seconds-since-boot counter.
When it jumps backwards, the router restarted. That is not an inference, and it
holds up as evidence in a support conversation.

It is also how this tool caught an SFR NB6VAC restarting 3x in one hour while
its owner assumed the problem was WiFi on a laptop.

## Being a good neighbour

The monitor must never be the reason a household network misbehaves. Consumer
gateways can be fragile — the NB6VAC in question appears able to crash under a
burst of HTTP calls to its own admin API. So:

- The only frequent router probe is NAT-PMP: one 2-byte UDP datagram, every 30s.
- Anything touching the router's **web stack** runs at 5-minute granularity.
- `--no-vendor` disables router HTTP entirely. **Use it on someone else's
  router**, where you have no mandate to poke the management interface.
- The service runs at `Nice=10` with idle I/O scheduling.

## Tests

```sh
python3 test_netwatch.py
```

Covers classification ordering, the outage ledger and its durations, faults
present at startup, and both restart detectors — including the cases that must
stay *silent* (a rising epoch, a failed probe), because a detector that fires on
everything proves nothing.

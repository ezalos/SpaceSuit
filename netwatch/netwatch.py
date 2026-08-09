#!/usr/bin/env python3
# ABOUTME: Router-agnostic network health recorder — proves when/where connectivity broke.
# ABOUTME: Samples link, gateway, WAN, DNS, router uptime and local load; classifies outages.
"""netwatch — an append-only black box for a home network.

Design goals
------------
* **Portable.** Nothing here is specific to one ISP or router. Router-reboot
  detection uses NAT-PMP epoch (RFC 6886) and SSDP BOOTID (UPnP), both of which
  any consumer gateway speaks. A vendor probe is a bonus, never a requirement.
* **Attributable.** Every sample also records *this host's* egress load and
  conntrack pressure, so "the network broke" can be separated from "I broke the
  network" after the fact.
* **Unprivileged.** No sudo, no raw sockets, no packet capture. Reads /sys and
  /proc, sends UDP/ICMP as a normal user.
* **Cheap.** One ping per target per cycle. The monitor must never itself be
  the reason a household network feels slow.

Subcommands: run | probe | report | mark
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

STATE_DIR = Path(os.environ.get("NETWATCH_STATE", Path.home() / ".local/state/netwatch"))
EVENTS = STATE_DIR / "events.jsonl"      # transitions only — the permanent ledger
SAMPLES = STATE_DIR / "samples.jsonl"    # raw detail — rotated daily, kept N days

DEFAULT_WAN_TARGETS = ["1.1.1.1", "9.9.9.9"]
DEFAULT_DNS_NAME = "cloudflare.com"
KEEP_DAYS = 14

# How often (in cycles) to run the more expensive probes.
#
# Deliberately conservative. netwatch must never be the reason a household
# network misbehaves, and consumer gateways can be fragile: an SFR NB6VAC in
# this very household appears able to crash under a burst of HTTP calls to its
# own management API. So the only frequent router probe is NAT-PMP, which is a
# single 2-byte UDP datagram. Everything that touches the router's *web stack*
# runs at 5-minute granularity, and can be switched off entirely.
DNS_EVERY = 6        # ~30s at a 5s interval  — costs the router nothing
ROUTER_EVERY = 6     # ~30s  — NAT-PMP only, 2 bytes of UDP
SSDP_EVERY = 60      # ~5min — multicast M-SEARCH, corroboration only
VENDOR_EVERY = 60    # ~5min — HTTP to the router's admin API; --no-vendor to disable
PEER_EVERY = 12      # ~60s  — is the other recorder alive?
PUBLIC_IP_EVERY = 720  # ~1h  — has our public IP changed?

# Providers for the authoritative public-IP lookup. Several, because a single
# one being down must not look like an IP change. Plain-text responses only.
PUBLIC_IP_PROVIDERS = [
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
]
PUBLIC_IP_STATE = "public_ip.json"

# Dead-man's switch. netwatch pings an external sink from inside its own loop,
# so the ping stops if the *recorder* wedges, not merely if the host dies.
# Missing pings are what proves "nothing was wrong" was actually observed —
# there is no routine all-clear digest by design, so this carries the guarantee.
HC_INTERVAL_S = 300

# Alert coalescing window. Within it, at most ALERT_MAX messages go out; the
# rest are counted and summarised when the window closes.
ALERT_WINDOW_S = 900
ALERT_MAX = 2

# Bad-neighbour detection. Nobody else will report this: at a family home the
# person suffering from our bufferbloat is not the person who reads the alerts.
IMPACT_WINDOW = 720          # samples retained (~1h at 5s)
IMPACT_MIN_PER_SIDE = 60     # need this many idle and this many loaded
IMPACT_RATIO = 3.0           # loaded p95 over idle p95 that counts as harm
IMPACT_COOLDOWN_S = 21600    # at most one such alert per 6h


# ---------------------------------------------------------------- utilities

def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def human_dur(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    h, rem = divmod(seconds, 3600)
    if h < 24:
        return f"{h}h{rem // 60:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h:02d}h"


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


def read_jsonl(path: Path):
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ------------------------------------------------------------ local probes

def default_route() -> tuple[str | None, str | None]:
    """Return (iface, gateway_ip) for the current IPv4 default route."""
    try:
        out = subprocess.run(
            ["ip", "-4", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None, None
    m = re.search(r"default via (\S+) dev (\S+)", out)
    if m:
        return m.group(2), m.group(1)
    return None, None


def link_state(iface: str | None) -> dict:
    if not iface:
        return {"carrier": None, "operstate": None, "speed": None}
    base = Path("/sys/class/net") / iface
    def rd(name):
        try:
            return (base / name).read_text().strip()
        except Exception:
            return None
    carrier = rd("carrier")
    changes = rd("carrier_changes")
    return {
        "carrier": int(carrier) if carrier in ("0", "1") else None,
        "operstate": rd("operstate"),
        "speed": rd("speed"),
        # Monotonic kernel counter of carrier transitions. Polling at 5s cannot
        # see a 3-4s flap — but the counter still moved. This is what makes
        # "no flaps" an observation rather than an assumption.
        "carrier_changes": int(changes) if changes and changes.isdigit() else None,
    }


def iface_bytes(iface: str | None) -> tuple[int | None, int | None]:
    if not iface:
        return None, None
    base = Path("/sys/class/net") / iface / "statistics"
    def rd(name):
        try:
            return int((base / name).read_text().strip())
        except Exception:
            return None
    return rd("rx_bytes"), rd("tx_bytes")


def conntrack() -> dict:
    def rd(p):
        try:
            return int(Path(p).read_text().strip())
        except Exception:
            return None
    return {
        "count": rd("/proc/sys/net/netfilter/nf_conntrack_count"),
        "max": rd("/proc/sys/net/netfilter/nf_conntrack_max"),
    }


def ping(host: str, timeout: int = 1, count: int = 1) -> dict:
    """One-shot ICMP probe. Returns rtt_ms (None if lost) and loss fraction."""
    try:
        proc = subprocess.run(
            ["ping", "-n", "-q", "-c", str(count), "-W", str(timeout), host],
            capture_output=True, text=True, timeout=timeout * count + 3,
        )
    except Exception as exc:
        return {"up": False, "rtt_ms": None, "loss": 1.0, "err": repr(exc)}
    out = proc.stdout
    loss_m = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
    rtt_m = re.search(r"=\s*[\d.]+/([\d.]+)/", out)
    loss = float(loss_m.group(1)) / 100.0 if loss_m else 1.0
    rtt = float(rtt_m.group(1)) if rtt_m else None
    return {"up": proc.returncode == 0 and rtt is not None, "rtt_ms": rtt, "loss": loss}


def dns_probe(name: str, timeout: float = 3.0) -> dict:
    t0 = time.monotonic()
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
        return {"ok": True, "ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as exc:
        return {"ok": False, "ms": None, "err": type(exc).__name__}
    finally:
        socket.setdefaulttimeout(old)


# ----------------------------------------------------- router-side probes
# These are the portable ones: they work on essentially any consumer gateway
# and are what lets us say "the ROUTER restarted" rather than "the net blipped".

def natpmp_epoch(gateway: str, timeout: float = 2.0) -> dict:
    """NAT-PMP (RFC 6886) public-address request.

    The `epoch` field is seconds since the gateway's mapping table was built,
    i.e. effectively router uptime. When it goes *backwards*, the router
    restarted. This is the single most reliable portable reboot detector.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(b"\x00\x00", (gateway, 5351))
        data, _ = s.recvfrom(64)
        if len(data) < 12:
            return {"ok": False, "err": "short response"}
        _ver, _op, result, epoch = struct.unpack("!BBHI", data[:8])
        return {
            "ok": result == 0,
            "epoch_s": epoch,
            "ext_ip": socket.inet_ntoa(data[8:12]),
        }
    except Exception as exc:
        return {"ok": False, "err": type(exc).__name__}
    finally:
        s.close()


def ssdp_bootid(timeout: float = 2.5) -> dict:
    """UPnP SSDP M-SEARCH; BOOTID.UPNP.ORG increments on gateway restart."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST:239.255.255.250:1900\r\n"
        'MAN:"ssdp:discover"\r\n'
        "MX:2\r\n"
        "ST:urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n"
    ).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        data, addr = s.recvfrom(4096)
        text = data.decode(errors="replace")
        out = {"ok": True, "from": addr[0]}
        for line in text.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().upper()
            if k == "BOOTID.UPNP.ORG":
                out["bootid"] = v.strip()
            elif k == "SERVER":
                out["server"] = v.strip()
        return out
    except Exception as exc:
        return {"ok": False, "err": type(exc).__name__}
    finally:
        s.close()


# Vendor probe. Optional enrichment only — absence must never break a sample.
# Currently knows the SFR/neufbox unauthenticated API (NB6VAC and relatives).
_VENDOR_ATTRS = re.compile(r'(\w+)="([^"]*)"')


def vendor_probe(gateway: str, timeout: float = 3.0) -> dict:
    def call(method):
        url = f"http://{gateway}/api/1.0/?method={method}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode(errors="replace")
        except Exception:
            return None
        if "stat=\"ok\"" not in body.replace("'", '"'):
            return None
        return dict(_VENDOR_ATTRS.findall(body))

    sysinfo = call("system.getInfo")
    if not sysinfo:
        return {}
    out = {"vendor": "sfr-neufbox", "model": sysinfo.get("product_id")}
    for src, dst, cast in (
        ("uptime", "uptime_s", int),
        ("temperature", "temp_mc", int),
        ("alimvoltage", "volt_mv", int),
        ("version_mainfirmware", "firmware", str),
        ("net_infra", "infra", str),
    ):
        val = sysinfo.get(src)
        if val is None:
            continue
        try:
            out[dst] = cast(val)
        except ValueError:
            pass
    wan = call("wan.getInfo")
    if wan:
        out["wan_status"] = wan.get("status")
        out["wan_ip"] = wan.get("ip_addr")
        try:
            out["wan_uptime_s"] = int(wan.get("uptime") or 0)
        except ValueError:
            pass
    return out


_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def public_ip_probe(providers=None, timeout: float = 8.0) -> str | None:
    """Our address as the rest of the internet sees it.

    Asks external providers rather than trusting the router, so it stays correct
    behind CGNAT. Tries several: one provider being unreachable must not be
    mistaken for an IP change, which would fire a false alert.
    """
    for url in (providers or PUBLIC_IP_PROVIDERS):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                ip = resp.read().decode(errors="replace").strip()
            if _IPV4.match(ip) and all(0 <= int(o) <= 255 for o in ip.split(".")):
                return ip
        except Exception:
            continue
    return None


# ------------------------------------------------------------ classification

def classify(sample: dict) -> str:
    """Reduce a sample to one state. Ordered most-local-cause first."""
    link = sample.get("link", {})
    if link.get("carrier") == 0 or link.get("operstate") == "down":
        return "link_down"
    if sample.get("gateway") is None:
        return "no_route"
    gw = sample.get("gw", {})
    gw_silent = bool(gw) and not gw.get("up")
    wan = sample.get("wan", {})
    reachable = [t for t, r in wan.items() if r.get("up")] if wan else []

    # A gateway that stops answering ICMP *while the internet still works* is
    # not an outage. Consumer routers deprioritise or drop pings addressed to
    # themselves when their management plane is busy — Tailscale's UPnP/NAT-PMP
    # discovery provokes it reliably on the Livebox — and forward traffic
    # perfectly throughout. Measured 2026-08-06: a burst of such "outages" on
    # TinyButMighty, with cloudflared logging no lost connections and the
    # carrier counter never moving.
    #
    # Only conclude that when the WAN says so. With no WAN evidence there is
    # nothing to distinguish this from a genuinely dead router, so keep the
    # old, conservative verdict.
    if gw_silent and not reachable:
        return "gateway_down"

    if wan:
        if not reachable:
            return "wan_down"
        if len(reachable) < len(wan):
            return "wan_degraded"
    dns = sample.get("dns")
    if dns is not None and not dns.get("ok"):
        return "dns_down"

    # Recorded rather than discarded: it stays visible in the ledger and in
    # `report`, it just does not wake Louis at 23:14.
    if gw_silent:
        return "gateway_silent"
    return "ok"


# gateway_silent is deliberately absent: it is recorded, never alerted on.
BAD_STATES = {"link_down", "no_route", "gateway_down", "wan_down", "dns_down"}

BLAME = {
    "link_down":    "cable/switch port or router LAN side",
    "no_route":     "local network config (no default route)",
    "gateway_down": "the router itself",
    "gateway_silent": "router not answering pings to itself; traffic unaffected",
    "wan_down":     "ISP / upstream (router alive, internet not)",
    "wan_degraded": "partial upstream loss",
    "dns_down":     "DNS resolver only (packets still flowed)",
}


# ------------------------------------------------------------------ telegram

def telegram_send(text: str) -> bool:
    token = None
    env = Path.home() / ".claude/channels/telegram/.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
    chat_id = os.environ.get("NETWATCH_TELEGRAM_CHAT")
    if not chat_id:
        access = Path.home() / ".claude/channels/telegram/access.json"
        if access.exists():
            try:
                data = json.loads(access.read_text())
                allow = data.get("allowFrom") or []
                if allow:
                    first = allow[0]
                    chat_id = str(first.get("id") if isinstance(first, dict) else first)
            except Exception:
                pass
    if not token or not chat_id:
        return False
    try:
        # Message convention (GroundControl docs/naming.md, "Telegram messages"):
        # netwatch ships with SpaceSuit, so its agent emoji is the suit.
        payload = json.dumps({"chat_id": chat_id, "text": f"🧑‍🚀 [netwatch] {text}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ rotation

def rotate_samples(keep_days: int = KEEP_DAYS) -> None:
    if not SAMPLES.exists():
        return
    stamp = time.strftime("%Y%m%d", time.localtime(SAMPLES.stat().st_mtime))
    if stamp == time.strftime("%Y%m%d"):
        return
    archive = SAMPLES.with_name(f"samples-{stamp}.jsonl.gz")
    try:
        with SAMPLES.open("rb") as src, gzip.open(archive, "wb") as dst:
            shutil.copyfileobj(src, dst)
        SAMPLES.unlink()
    except Exception:
        return
    cutoff = time.time() - keep_days * 86400
    for old in SAMPLES.parent.glob("samples-*.jsonl.gz"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)


# ---------------------------------------------------------------- the daemon

class Runner:
    def __init__(self, args):
        self.args = args
        self.stop = False
        self.cycle = 0
        self.state = None
        self.state_since = now()
        self.bad_snapshot = None
        self.prev_epoch = None
        self.prev_bootid = None
        self.prev_bytes = None
        self.prev_bytes_t = None
        self.prev_carrier_changes = None
        # Alert coalescing: a crash-looping router produced three restarts in
        # fifty minutes. That must arrive as one message with a count, not as
        # three pings, or the alerts train Louis to ignore them.
        self.alert_window_start = None
        self.alert_sent = 0
        self.alert_suppressed = 0
        # These are "never happened yet" sentinels, deliberately None rather
        # than 0.0: time.monotonic() starts near zero, so a 0.0 baseline makes
        # every cooldown look freshly-expired-not for the first hours of uptime
        # and silently swallows the very first alert.
        self.last_hc_ping = None
        # Rolling latency samples for the bad-neighbour check: (tx_bps, rtt_ms)
        self.impact_window = []
        self.last_impact_alert = None
        # Recovery timing for the sequence link -> gateway -> WAN -> DNS
        self.recovery = None
        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT, self._sig)

    def _sig(self, *_):
        self.stop = True

    # -- one full sample -------------------------------------------------
    def sample(self) -> dict:
        iface, gateway = default_route()
        s = {
            "ts": iso(now()),
            "iface": iface,
            "gateway": gateway,
            "link": link_state(iface),
        }

        if gateway:
            s["gw"] = ping(gateway, timeout=1)
        s["wan"] = {t: ping(t, timeout=self.args.ping_timeout)
                    for t in self.args.wan_targets}

        if self.cycle % DNS_EVERY == 0:
            s["dns"] = dns_probe(self.args.dns_name)

        if gateway and self.cycle % ROUTER_EVERY == 0:
            s["router"] = {"natpmp": natpmp_epoch(gateway)}
            if self.cycle % SSDP_EVERY == 0:
                s["router"]["ssdp"] = ssdp_bootid()

        if gateway and self.args.vendor and self.cycle % VENDOR_EVERY == 0:
            v = vendor_probe(gateway)
            if v:
                s["vendor"] = v

        # local load — the "was it me?" column
        rx, tx = iface_bytes(iface)
        host = {"conntrack": conntrack()}
        t = time.monotonic()
        if rx is not None and self.prev_bytes is not None:
            dt = t - self.prev_bytes_t
            if dt > 0:
                host["rx_bps"] = int(max(0, rx - self.prev_bytes[0]) * 8 / dt)
                host["tx_bps"] = int(max(0, tx - self.prev_bytes[1]) * 8 / dt)
        if rx is not None:
            self.prev_bytes, self.prev_bytes_t = (rx, tx), t
        s["host"] = host

        # The other recorder. On one LAN this is corroboration; once the two
        # machines live in different houses it is a genuinely independent
        # witness, which is the whole point of running two.
        if self.args.peer and self.cycle % PEER_EVERY == 0:
            s["peer"] = ping(self.args.peer, timeout=2)

        s["state"] = classify(s)
        return s

    # -- sub-interval link flaps ----------------------------------------
    def check_flaps(self, s: dict) -> None:
        """Catch carrier flaps shorter than the sampling interval.

        The kernel counts every carrier transition. Polling at 5s cannot see a
        3-4s flap directly, but the counter still moved — so a delta is proof
        that the link bounced even though we never observed carrier=0.
        """
        n = (s.get("link") or {}).get("carrier_changes")
        if n is None:
            return
        prev = self.prev_carrier_changes
        self.prev_carrier_changes = n
        if prev is None or n == prev:
            return
        if n < prev:
            # Counter reset (device re-registered). Not a flap — but say so
            # rather than silently swallowing it.
            self.emit_event({"type": "carrier_counter_reset", "ts": s["ts"],
                             "prev": prev, "new": n})
            return
        # Only report flaps the sampler did not already see as carrier=0; those
        # are reported through the normal outage path.
        if (s.get("link") or {}).get("carrier") == 1:
            self.emit_event({
                "type": "link_flap",
                "ts": s["ts"],
                "transitions": n - prev,
                "note": "link bounced between samples — too brief for polling to see",
            })

    # -- router restart detection ---------------------------------------
    def check_router_restart(self, s: dict) -> None:
        router = s.get("router")
        if not router:
            return
        pm = router.get("natpmp") or {}
        epoch = pm.get("epoch_s") if pm.get("ok") else None
        if epoch is not None:
            if self.prev_epoch is not None and epoch < self.prev_epoch:
                boot = now().timestamp() - epoch
                self.emit_event({
                    "type": "router_restart",
                    "ts": s["ts"],
                    "detector": "natpmp_epoch",
                    "prev_epoch_s": self.prev_epoch,
                    "new_epoch_s": epoch,
                    "router_booted_at": iso(datetime.fromtimestamp(boot).astimezone()),
                    "vendor": s.get("vendor", {}),
                })
                self.notify(
                    f"⚠️ Router RESTARTED at "
                    f"{datetime.fromtimestamp(boot).astimezone():%H:%M:%S}\n"
                    f"Previous uptime: {human_dur(self.prev_epoch)}\n"
                    f"Detected by: NAT-PMP epoch reset"
                )
            self.prev_epoch = epoch

        ss = router.get("ssdp") or {}
        bootid = ss.get("bootid")
        if bootid and self.prev_bootid and bootid != self.prev_bootid:
            self.emit_event({
                "type": "router_restart",
                "ts": s["ts"],
                "detector": "ssdp_bootid",
                "prev_bootid": self.prev_bootid,
                "new_bootid": bootid,
            })
        if bootid:
            self.prev_bootid = bootid

    # -- state transitions ----------------------------------------------
    def check_transition(self, s: dict) -> None:
        st = s["state"]
        if self.state is None:
            # First sample. If we come up *into* a fault (machine rebooted
            # mid-outage, or the service was restarted while things were
            # broken) that must still land in the ledger — otherwise an
            # outage spanning a restart leaves no trace at all.
            self.state = st
            self.state_since = now()
            if st in BAD_STATES:
                self.bad_snapshot = {"host": s.get("host"), "vendor": s.get("vendor"),
                                     "link": s.get("link")}
                self.emit_event({"type": "degraded_start", "state": st, "ts": s["ts"],
                                 "blame": BLAME.get(st, "unknown"), "at_startup": True})
            return
        if st == self.state:
            return

        began, ended = self.state, st
        duration = (now() - self.state_since).total_seconds()

        if began in BAD_STATES:
            ev = {
                "type": "outage",
                "state": began,
                "blame": BLAME.get(began, "unknown"),
                "start": iso(self.state_since),
                "end": s["ts"],
                "duration_s": round(duration, 1),
                "recovered_to": ended,
                "snapshot": self.bad_snapshot,
            }
            self.emit_event(ev)
            if duration >= self.args.alert_after:
                self.notify(self.format_outage(ev))
        elif ended in BAD_STATES:
            self.bad_snapshot = {
                "host": s.get("host"),
                "vendor": s.get("vendor"),
                "link": s.get("link"),
            }
            self.emit_event({
                "type": "degraded_start",
                "state": ended,
                "ts": s["ts"],
                "after_ok_for_s": round(duration, 1),
            })

        self.state = st
        self.state_since = now()

    def format_outage(self, ev: dict) -> str:
        snap = ev.get("snapshot") or {}
        host = snap.get("host") or {}
        lines = [
            f"🔌 Network outage — {human_dur(ev['duration_s'])}",
            f"Type: {ev['state']}  →  likely {ev['blame']}",
            f"From {ev['start'][11:19]} to {ev['end'][11:19]}",
        ]
        tx = host.get("tx_bps")
        rx = host.get("rx_bps")
        if tx is not None:
            lines.append(f"This host at onset: ↑{tx/1e6:.1f} Mb/s ↓{rx/1e6:.1f} Mb/s")
        ct = (host.get("conntrack") or {}).get("count")
        if ct is not None:
            lines.append(f"Conntrack entries: {ct}")
        v = snap.get("vendor") or {}
        if v.get("uptime_s") is not None:
            lines.append(f"Router uptime at onset: {human_dur(v['uptime_s'])}")
        return "\n".join(lines)

    def emit_event(self, ev: dict) -> None:
        ev.setdefault("host", socket.gethostname())
        ev.setdefault("ts", iso(now()))
        append_jsonl(EVENTS, ev)
        if not self.args.quiet:
            print(json.dumps(ev), flush=True)

    def notify(self, text: str, force: bool = False) -> None:
        """Send an alert, coalescing bursts.

        Three router restarts in fifty minutes must not become three pings.
        Past ALERT_MAX inside a window we count instead of sending, and emit a
        single summary when the window closes — so nothing is lost, but the
        alerts stay worth reading.
        """
        if self.args.no_telegram:
            return
        t = time.monotonic()
        if self.alert_window_start is None or t - self.alert_window_start > ALERT_WINDOW_S:
            if self.alert_suppressed:
                self.deliver(
                    f"[netwatch@{socket.gethostname()}]\n"
                    f"…and {self.alert_suppressed} further alert(s) in the last "
                    f"{human_dur(ALERT_WINDOW_S)}. Run `netwatch report` for the ledger."
                )
            self.alert_window_start = t
            self.alert_sent = 0
            self.alert_suppressed = 0
        if not force and self.alert_sent >= ALERT_MAX:
            self.alert_suppressed += 1
            return
        self.alert_sent += 1
        self.deliver(f"[netwatch@{socket.gethostname()}]\n{text}")

    def deliver(self, body: str) -> None:
        """Send one alert, and refuse to let a failed send pass unnoticed.

        telegram_send() reports failure by return value, and that value used to
        be dropped on the floor: a revoked bot, an expired token or a rotated
        chat id looked exactly like a quiet week. The whole point of an alert
        is that silence means "nothing happened" — so an undeliverable alert
        must not be able to counterfeit silence.

        A broken channel cannot announce its own breakage, so this does not try
        to. It writes the alert to the permanent ledger (nothing Louis should
        have been told is ever lost) and then deliberately fails the dead-man's
        switch, which reaches him through healthchecks.io — a different
        network path, a different provider, a different inbox.
        """
        if telegram_send(body):
            return
        self.emit_event({"type": "alert_undelivered", "text": body})
        self.fail_healthcheck("telegram send failed")

    def fail_healthcheck(self, reason: str) -> None:
        """Trip the external dead-man's switch on purpose.

        Best-effort by design: if this ping cannot get out either, the switch
        alarms on its own from the missing heartbeat, which is the same
        outcome by a slower route.
        """
        url = self.args.healthcheck_url
        if not url:
            return
        try:
            req = urllib.request.Request(url.rstrip("/") + "/fail",
                                         data=reason.encode())
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass

    # -- dead-man's switch ------------------------------------------------
    def heartbeat(self, s: dict) -> None:
        """Stamp the ledger, then ping the external sink.

        The ledger stamp is what makes coverage computable from `events.jsonl`
        alone, long after the raw samples have rotated away — so "nothing was
        wrong then" stays provable rather than merely unrecorded.

        The outbound ping is the dead-man's switch: it originates inside this
        loop, so it stops if the *recorder* wedges, not only if the host dies.
        """
        t = time.monotonic()
        if self.last_hc_ping is not None and t - self.last_hc_ping < HC_INTERVAL_S:
            return
        self.last_hc_ping = t
        append_jsonl(EVENTS, {"type": "heartbeat", "ts": s["ts"],
                              "host": socket.gethostname(), "state": s["state"]})
        url = self.args.healthcheck_url
        if not url:
            return
        try:
            urllib.request.urlopen(url, timeout=10).read()
        except Exception:
            # A failed heartbeat usually *is* the network being down, which the
            # sink notices by itself. Never let it kill the recorder.
            pass

    # -- public IP changes -------------------------------------------------
    def check_public_ip(self, s: dict, force: bool = False) -> None:
        """Alert when our address on the public internet changes.

        Persisted to disk, so a service restart neither re-alerts nor loses the
        baseline, and a change that happened while we were down is still caught
        on the next check.

        A change also invalidates anything pinned to the old address — DNS
        records, tunnels, allow-lists — and it means the *new* address is not
        yet in the git content-scrub block list, so we re-register it
        immediately rather than waiting for that script's own 6-hourly cron.
        """
        if not self.args.track_public_ip:
            return
        if not force and self.cycle % PUBLIC_IP_EVERY != 0:
            return

        ip = public_ip_probe()
        if not ip:
            return  # every provider unreachable: almost certainly an outage

        path = STATE_DIR / PUBLIC_IP_STATE
        prev = None
        try:
            prev = json.loads(path.read_text()).get("ip")
        except Exception:
            pass
        if ip == prev:
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ip": ip, "since": iso(now())}))

        if prev is None:
            # First ever observation is a baseline, not a change.
            self.emit_event({"type": "public_ip_baseline", "ts": s["ts"], "ip": ip})
            return

        self.emit_event({"type": "public_ip_change", "ts": s["ts"],
                         "previous": prev, "current": ip})
        self.notify(
            f"🌐 Public IP changed\n{prev}  →  {ip}\n"
            f"Anything pinned to the old address (DNS records, tunnels, "
            f"allow-lists) needs checking.",
            force=True,
        )
        self.register_protected_ip()

    def register_protected_ip(self) -> None:
        """Best-effort: add the new address to the git content-scrub block list.

        Louis's hard rule is that the home public IP never reaches a repo. That
        guard is a block list which the new address is not yet in, so close the
        window now instead of leaving it until the next cron run.
        """
        script = Path.home() / "Setup/scripts/check-home-ip.sh"
        if not script.exists():
            return
        try:
            subprocess.run([str(script)], timeout=30, capture_output=True)
        except Exception:
            pass

    # -- are we the problem? ----------------------------------------------
    def check_impact(self, s: dict) -> None:
        """Alert if this host's own traffic is degrading everyone else's latency.

        Bufferbloat is invisible to the person causing it — their transfer runs
        at full speed. It is only visible to whoever else is trying to use the
        line. In a family home that person will not file a bug report.
        """
        host = s.get("host") or {}
        tx = host.get("tx_bps")
        if tx is None:
            return
        rtts = [r["rtt_ms"] for r in (s.get("wan") or {}).values()
                if r.get("up") and r.get("rtt_ms") is not None]
        if not rtts:
            return
        self.impact_window.append((tx, min(rtts)))
        if len(self.impact_window) > IMPACT_WINDOW:
            self.impact_window.pop(0)

        idle = [r for bps, r in self.impact_window if bps < 1_000_000]
        busy = [r for bps, r in self.impact_window if bps >= 10_000_000]
        if len(idle) < IMPACT_MIN_PER_SIDE or len(busy) < IMPACT_MIN_PER_SIDE:
            return
        idle_p95, busy_p95 = _pct(idle, 95), _pct(busy, 95)
        if not idle_p95 or not busy_p95 or busy_p95 < IMPACT_RATIO * idle_p95:
            return
        t = time.monotonic()
        if self.last_impact_alert is not None and \
                t - self.last_impact_alert < IMPACT_COOLDOWN_S:
            return
        self.last_impact_alert = t
        ev = {
            "type": "bad_neighbour",
            "ts": s["ts"],
            "idle_p95_ms": round(idle_p95, 1),
            "loaded_p95_ms": round(busy_p95, 1),
            "ratio": round(busy_p95 / idle_p95, 1),
        }
        self.emit_event(ev)
        self.notify(
            f"📶 This host is degrading the network for everyone else.\n"
            f"Latency idle: {idle_p95:.0f} ms → under our upload: {busy_p95:.0f} ms "
            f"({ev['ratio']}x)\n"
            f"Other people's calls and browsing will feel this.\n"
            f"Fix: shape this host's egress, or move the transfer off-peak.",
            force=True,
        )

    # -- how long did coming back actually take? ---------------------------
    def track_recovery(self, s: dict) -> None:
        """Time the sequence link-up → gateway → WAN → DNS after a fault.

        This is what tells us whether NetworkManager's DHCP wait is on the
        critical path — measured, before anyone edits IP configuration on a
        machine that is only reachable remotely.
        """
        link_up = (s.get("link") or {}).get("carrier") == 1
        if self.recovery is None:
            if self.state in BAD_STATES and link_up:
                self.recovery = {"link_up_at": now(), "gateway": None,
                                 "wan": None, "dns": None}
            return
        r = self.recovery
        base = r["link_up_at"]
        if r["gateway"] is None and (s.get("gw") or {}).get("up"):
            r["gateway"] = (now() - base).total_seconds()
        if r["wan"] is None and any(x.get("up") for x in (s.get("wan") or {}).values()):
            r["wan"] = (now() - base).total_seconds()
        dns = s.get("dns")
        if r["dns"] is None and dns is not None and dns.get("ok"):
            r["dns"] = (now() - base).total_seconds()
        if r["wan"] is not None and r["dns"] is not None:
            self.emit_event({
                "type": "recovery_profile",
                "ts": s["ts"],
                "link_up_at": iso(base),
                "gateway_after_s": round(r["gateway"], 1) if r["gateway"] else None,
                "wan_after_s": round(r["wan"], 1),
                "dns_after_s": round(r["dns"], 1),
            })
            self.recovery = None
        elif (now() - base).total_seconds() > 300:
            self.recovery = None  # gave up waiting; don't leak state

    def run(self) -> int:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.emit_event({"type": "netwatch_start", "interval_s": self.args.interval,
                         "wan_targets": self.args.wan_targets})
        last_rotate = 0.0
        while not self.stop:
            started = time.monotonic()
            try:
                s = self.sample()
                if self.args.record_samples:
                    append_jsonl(SAMPLES, s)
                self.check_flaps(s)
                self.check_router_restart(s)
                self.check_public_ip(s, force=(self.cycle == 0))
                self.check_impact(s)
                # Before check_transition, which overwrites self.state.
                self.track_recovery(s)
                self.check_transition(s)
                self.heartbeat(s)
            except Exception as exc:  # a monitor that dies is worse than useless
                append_jsonl(EVENTS, {"type": "netwatch_error", "ts": iso(now()),
                                      "err": repr(exc)})
            if time.monotonic() - last_rotate > 3600:
                rotate_samples(self.args.keep_days)
                last_rotate = time.monotonic()
            self.cycle += 1
            time.sleep(max(0.0, self.args.interval - (time.monotonic() - started)))
        self.emit_event({"type": "netwatch_stop"})
        return 0


# ----------------------------------------------------------------- reporting

def coverage(stamps, since: float, until: float, max_gap: float = HC_INTERVAL_S * 2):
    """How much of the window was the recorder demonstrably awake?

    This is the difference between "nothing bad happened" and "nothing was
    recorded". Any written record — a heartbeat, an event, or a raw sample — is
    proof of life at that instant. A stretch longer than two heartbeat intervals
    with nothing written means we were blind, and whatever happened in it is
    unknown, not fine.

    Takes epoch-second timestamps so callers can pool every source of evidence
    they still have; raw samples rotate away, the ledger does not.

    Returns (observed_seconds, [(gap_start, gap_end), ...]).
    """
    stamps = sorted(t for t in stamps if t)
    # Proof of life from before the window still tells us we were up at its start.
    prior = [t for t in stamps if t < since]
    inside = [t for t in stamps if since <= t <= until]
    if not inside:
        return 0.0, [(since, until)]

    gaps = []
    cursor = since
    # Head: if the last sign of life before the window is stale, we were down.
    if not prior or since - prior[-1] > max_gap:
        if inside[0] - since > max_gap:
            gaps.append((since, inside[0]))
            cursor = inside[0]
    for a, b in zip(inside, inside[1:]):
        if b - a > max_gap:
            gaps.append((a, b))
    if until - inside[-1] > max_gap:
        gaps.append((inside[-1], until))

    blind = sum(b - a for a, b in gaps)
    return max(0.0, (until - since) - blind), gaps


def mask_ip(ip: str | None) -> str | None:
    """Blunt the home address to its first two octets."""
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return f"{parts[0]}.{parts[1]}.x.x"


# The dashboard shows the full address by default. Every route on it — page and
# JSON alike — was verified to 302 to Cloudflare Access before this was turned
# on, and once the two recorders live in different houses their addresses are
# different facts worth seeing side by side. Set NETWATCH_MASK_PUBLIC_IP=1 to
# go back to two octets if this data ever gets served somewhere ungated.
MASK_PUBLIC_IP = bool(os.environ.get("NETWATCH_MASK_PUBLIC_IP"))


def summarize(days: int = 7) -> dict:
    """Everything the dashboard shows, as plain data.

    Kept separate from the printing in `report` so the same numbers drive the
    terminal and the web page — a dashboard that disagrees with the CLI is worse
    than no dashboard.
    """
    until = time.time()
    since = until - days * 86400
    all_events = list(read_jsonl(EVENTS))
    events = [e for e in all_events if (_ts(e.get("ts")) or 0) >= since]

    samples = list(load_samples(days))
    life = [_ts(e.get("ts")) for e in all_events] + [_ts(s.get("ts")) for s in samples]
    observed, gaps = coverage(life, since, until)

    def of(kind):
        return [e for e in events if e.get("type") == kind]

    outages = of("outage")
    restarts = [e for e in of("router_restart") if e.get("detector") == "natpmp_epoch"]
    total_down = sum(o.get("duration_s", 0) for o in outages)

    by_state: dict[str, dict] = {}
    for o in outages:
        b = by_state.setdefault(o["state"], {"count": 0, "total_s": 0.0,
                                             "blame": o.get("blame")})
        b["count"] += 1
        b["total_s"] = round(b["total_s"] + o.get("duration_s", 0), 1)

    latest = samples[-1] if samples else {}
    rtts = [r["rtt_ms"] for s in samples[-720:]
            for r in (s.get("wan") or {}).values()
            if r.get("up") and r.get("rtt_ms") is not None]

    vendor = {}
    for s in reversed(samples):
        if s.get("vendor"):
            vendor = s["vendor"]
            break

    natpmp_epoch_s = None
    for s in reversed(samples):
        pm = ((s.get("router") or {}).get("natpmp") or {})
        if pm.get("ok"):
            natpmp_epoch_s = pm.get("epoch_s")
            break

    ip_state = {}
    try:
        ip_state = json.loads((STATE_DIR / PUBLIC_IP_STATE).read_text())
    except Exception:
        pass

    return {
        "host": socket.gethostname(),
        "generated_at": iso(now()),
        "window_days": days,
        "coverage": {
            "pct": round(100.0 * observed / (until - since), 2) if until > since else 0,
            "observed_s": round(observed),
            "window_s": round(until - since),
            "gaps": [{"from": iso(datetime.fromtimestamp(a).astimezone()),
                      "to": iso(datetime.fromtimestamp(b).astimezone()),
                      "duration_s": round(b - a)} for a, b in gaps[-10:]],
        },
        "availability_pct": round(100.0 * (1 - total_down / observed), 4)
                            if observed > 0 else None,
        "router": {
            "restarts": len(restarts),
            "last_restart": restarts[-1].get("router_booted_at") if restarts else None,
            "uptime_s": natpmp_epoch_s,
            "model": vendor.get("model"),
            "firmware": vendor.get("firmware"),
            "temp_c": round(vendor["temp_mc"] / 1000.0, 1)
                      if vendor.get("temp_mc") else None,
            "wan_status": vendor.get("wan_status"),
        },
        "outages": {
            "count": len(outages),
            "total_down_s": round(total_down, 1),
            "longest_s": round(max((o.get("duration_s", 0) for o in outages), default=0), 1),
            "by_state": by_state,
            "recent": [{"start": o["start"], "state": o["state"],
                        "duration_s": o.get("duration_s"), "blame": o.get("blame")}
                       for o in outages[-10:]],
        },
        "link": {
            "carrier": (latest.get("link") or {}).get("carrier"),
            "speed": (latest.get("link") or {}).get("speed"),
            "iface": latest.get("iface"),
            "flaps": sum(e.get("transitions", 0) for e in of("link_flap")),
        },
        "wan": {
            "rtt_p50": _pct(rtts, 50),
            "rtt_p95": _pct(rtts, 95),
            "state": latest.get("state"),
        },
        "public_ip": {
            "address": (mask_ip(ip_state.get("ip")) if MASK_PUBLIC_IP
                        else ip_state.get("ip")),
            "masked": MASK_PUBLIC_IP,
            "since": ip_state.get("since"),
            "changes": len(of("public_ip_change")),
        },
        # Scan back for the last sample that actually carried a peer probe: it
        # only runs every PEER_EVERY cycles, so reading it off the newest sample
        # would report the other recorder as unknown about 11 times in 12.
        "peer": next((s["peer"] for s in reversed(samples) if s.get("peer")), None),
        "bad_neighbour": len(of("bad_neighbour")),
        "marks": [{"ts": m["ts"], "label": m.get("label")} for m in of("mark")[-5:]],
    }


def cmd_status(args) -> int:
    print(json.dumps(summarize(args.days), indent=None if args.compact else 2))
    return 0


def cmd_publish(args) -> int:
    """Push this host's summary to the machine that serves the dashboard.

    Push, not pull: after the move this host lives behind someone else's NAT
    with no inbound reachability, while the Pi stays put and always-on. Push
    also means the last good snapshot is what gets served when this host is
    down — which is exactly when you want to look.
    """
    payload = json.dumps(summarize(args.days), indent=2).encode()
    name = f"{socket.gethostname().lower()}.json"

    # A bare path means the dashboard directory is on this machine — the Pi
    # both serves the page and publishes into it, and routing that through ssh
    # to itself would add a failure mode for nothing.
    if ":" not in args.remote:
        dest = Path(args.remote) / name
        tmp_local = dest.with_suffix(".json.tmp")
        try:
            tmp_local.write_bytes(payload)
            tmp_local.chmod(0o644)
            tmp_local.replace(dest)
        except Exception as exc:
            print(f"publish failed: {exc}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"published {len(payload)}B -> {dest}")
        return 0

    host, _, path = args.remote.partition(":")
    if not host or not path:
        print("--remote must be host:/srv/network or a local directory",
              file=sys.stderr)
        return 2
    target = str(PurePosixPath(path) / name)
    tmp = f"{target}.tmp"
    # Atomic: a half-written file must never be served as the current state.
    cmd = f"cat > {tmp} && chmod 644 {tmp} && mv -f {tmp} {target}"
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, cmd],
            input=payload, capture_output=True, timeout=30, check=False,
        )
    except Exception as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"publish failed (exit {r.returncode}): "
              f"{r.stderr.decode(errors='replace').strip()}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"published {len(payload)}B -> {args.remote}/{name}")
    return 0


def cmd_report(args) -> int:
    since = time.time() - args.days * 86400
    all_events = list(read_jsonl(EVENTS))
    events = [e for e in all_events
              if _ts(e.get("ts")) and _ts(e["ts"]) >= since]

    outages = [e for e in events if e.get("type") == "outage"]
    restarts = [e for e in events if e.get("type") == "router_restart"
                and e.get("detector") == "natpmp_epoch"]
    marks = [e for e in events if e.get("type") == "mark"]

    host = socket.gethostname()
    print(f"netwatch report — {host} — last {args.days} day(s)")
    print("=" * 62)

    if not events:
        print("No data yet. Is netwatch running?  systemctl --user status netwatch")
        return 0

    # Coverage first, deliberately. Every number below it is only worth what
    # this number is worth.
    until = time.time()
    # Pool every surviving source of proof-of-life. Heartbeats alone would
    # under-report coverage for any period recorded before they existed.
    life = [_ts(e.get("ts")) for e in all_events]
    life += [_ts(s.get("ts")) for s in load_samples(args.days)]
    observed, gaps = coverage(life, since, until)
    window = until - since
    cov_pct = 100.0 * observed / window if window else 0.0
    print(f"\nCoverage:         {cov_pct:.2f}%  "
          f"({human_dur(observed)} watched of {human_dur(window)})")
    if gaps:
        print(f"  {len(gaps)} blind period(s) — NOT known to be healthy:")
        for a, b in gaps[:8]:
            print(f"    {datetime.fromtimestamp(a):%Y-%m-%d %H:%M:%S}"
                  f" → {datetime.fromtimestamp(b):%H:%M:%S}"
                  f"   ({human_dur(b - a)})")
        if len(gaps) > 8:
            print(f"    … and {len(gaps) - 8} more")

    flaps = [e for e in events if e.get("type") == "link_flap"]
    if flaps:
        total = sum(e.get("transitions", 0) for e in flaps)
        print(f"\nBrief link flaps: {len(flaps)} ({total} carrier transitions)")
        print("  Too short for polling to see directly; counted by the kernel.")

    bad_neighbour = [e for e in events if e.get("type") == "bad_neighbour"]
    if bad_neighbour:
        print(f"\nBad-neighbour:    {len(bad_neighbour)} episode(s) where our own"
              f" load degraded latency")
        for e in bad_neighbour[:5]:
            print(f"    {e['ts'][:19]}  {e['idle_p95_ms']}ms idle →"
                  f" {e['loaded_p95_ms']}ms loaded ({e['ratio']}x)")

    recoveries = [e for e in events if e.get("type") == "recovery_profile"]
    if recoveries:
        print(f"\nRecovery profile ({len(recoveries)} sample(s), from link-up):")
        for e in recoveries[-5:]:
            print(f"    {e['ts'][:19]}  gateway +{e.get('gateway_after_s')}s"
                  f"  WAN +{e.get('wan_after_s')}s  DNS +{e.get('dns_after_s')}s")

    print(f"\nRouter restarts:  {len(restarts)}")
    for r in restarts:
        prev = r.get("prev_epoch_s")
        print(f"  {r.get('router_booted_at', r['ts'])[:19]}"
              f"   (had been up {human_dur(prev) if prev else '?'})")

    print(f"\nOutages:          {len(outages)}")
    by_blame: dict[str, list] = {}
    total_down = 0.0
    for o in outages:
        by_blame.setdefault(o["state"], []).append(o)
        total_down += o.get("duration_s", 0)
    for state, group in sorted(by_blame.items(), key=lambda kv: -len(kv[1])):
        dur = sum(g.get("duration_s", 0) for g in group)
        print(f"  {state:<14} {len(group):>3}x  total {human_dur(dur):>8}"
              f"   → {BLAME.get(state, '?')}")
    if outages:
        longest = max(outages, key=lambda o: o.get("duration_s", 0))
        print(f"\n  Longest: {human_dur(longest['duration_s'])} "
              f"at {longest['start'][:19]} ({longest['state']})")

    if observed > 0:
        avail = 100.0 * (1 - total_down / observed)
        print(f"\nAvailability:     {avail:.4f}%   "
              f"({human_dur(total_down)} down of {human_dur(observed)} watched)")

    if marks:
        print("\nMarks:")
        for m in marks:
            print(f"  {m['ts'][:19]}  {m.get('label', '')}")

    print("\nVerdict:")
    if cov_pct < 99.0:
        print(f"  ⚠ NOT A CLEAN BILL — only {cov_pct:.1f}% of this window was"
              f" watched.\n  Whatever happened in the blind periods above is"
              f" unknown, not fine.\n  Findings below cover the watched time only.")
    if restarts:
        print(f"  The ROUTER restarted {len(restarts)}x in this window. Outages on"
              f"\n  every LAN device at those moments are the router's fault, not"
              f"\n  any one machine's. Router uptime is read from its own NAT-PMP"
              f"\n  epoch — it is the router's own counter, not an inference.")
    elif outages:
        print("  Outages seen, but the router never restarted — look upstream (ISP)"
              "\n  or at DNS, per the blame column above.")
    elif cov_pct >= 99.0:
        print("  Clean window. No outages, no router restarts, and the recorder"
              f"\n  was awake for {cov_pct:.2f}% of it — so this is an observation,"
              "\n  not an absence of data.")
    return 0


def load_samples(days: int):
    """Yield samples from the live file plus any rotated archives in range."""
    cutoff = time.time() - days * 86400
    paths = sorted(SAMPLES.parent.glob("samples-*.jsonl.gz")) if SAMPLES.parent.exists() else []
    for arch in paths:
        if arch.stat().st_mtime < cutoff - 86400:
            continue
        try:
            with gzip.open(arch, "rt") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue
    yield from read_jsonl(SAMPLES)


def _pct(values, p):
    if not values:
        return None
    vs = sorted(values)
    k = min(len(vs) - 1, int(round((p / 100.0) * (len(vs) - 1))))
    return vs[k]


# Minimum samples per side (idle vs loaded) before impact will state a verdict.
MIN_BUCKET = 200

# Throughput buckets, in bits/sec, for the impact analysis.
BUCKETS = [
    ("idle    (<1 Mb/s)",      0,          1_000_000),
    ("light   (1-10 Mb/s)",    1_000_000,  10_000_000),
    ("medium  (10-50 Mb/s)",   10_000_000, 50_000_000),
    ("heavy   (>50 Mb/s)",     50_000_000, float("inf")),
]


def cmd_impact(args) -> int:
    """Does THIS host's traffic degrade latency for everyone else?

    The mechanism that makes one busy machine ruin a household's internet is
    bufferbloat: a heavy uploader fills the router's egress queue, and every
    other device's packets wait behind it. Web pages stall, calls stutter,
    games lag — while raw bandwidth still looks fine on a speed test.

    Because every sample carries both this host's throughput and the measured
    round-trip time, we can just bucket one by the other. If latency climbs
    with our upload rate, we are the problem — and by how much.
    """
    direction = args.direction
    rows = []
    for s in load_samples(args.days):
        host = s.get("host") or {}
        bps = host.get("tx_bps") if direction == "up" else host.get("rx_bps")
        if bps is None:
            continue
        rtts = [r["rtt_ms"] for r in (s.get("wan") or {}).values()
                if r.get("up") and r.get("rtt_ms") is not None]
        gw = s.get("gw") or {}
        lost = bool((s.get("wan") or {})) and not rtts
        rows.append((bps, min(rtts) if rtts else None,
                     gw.get("rtt_ms") if gw.get("up") else None, lost))

    if not rows:
        print("No samples with throughput data yet. Let netwatch run a while"
              " (it needs two consecutive samples to compute a rate).")
        return 0

    print(f"netwatch impact — {socket.gethostname()} — last {args.days} day(s)")
    print(f"Does this host's {'UPLOAD' if direction == 'up' else 'DOWNLOAD'}"
          f" traffic hurt everyone else's latency?")
    print("=" * 72)
    print(f"{'this host':<22}{'samples':>8}{'WAN p50':>10}{'WAN p95':>10}"
          f"{'gw p95':>9}{'loss':>8}")

    stats, counts = {}, {}
    for label, lo, hi in BUCKETS:
        sel = [r for r in rows if lo <= r[0] < hi]
        counts[label] = len(sel)
        if not sel:
            print(f"{label:<22}{0:>8}{'-':>10}{'-':>10}{'-':>9}{'-':>8}")
            continue
        wan = [r[1] for r in sel if r[1] is not None]
        gws = [r[2] for r in sel if r[2] is not None]
        loss = 100.0 * sum(1 for r in sel if r[3]) / len(sel)
        p50, p95 = _pct(wan, 50), _pct(wan, 95)
        gp95 = _pct(gws, 95)
        stats[label] = (p50, p95)
        print(f"{label:<22}{len(sel):>8}"
              f"{(f'{p50:.1f}ms' if p50 else '-'):>10}"
              f"{(f'{p95:.1f}ms' if p95 else '-'):>10}"
              f"{(f'{gp95:.1f}ms' if gp95 else '-'):>9}"
              f"{loss:>7.1f}%")

    idle = stats.get("idle    (<1 Mb/s)")
    busy = [v for k, v in stats.items() if not k.startswith("idle")]
    print("\nVerdict:")

    # A confident-sounding verdict off a handful of samples is worse than no
    # verdict at all — it is exactly the kind of thing someone acts on. Refuse
    # to answer until there is enough of both idle and loaded traffic to mean
    # anything.
    idle_n = counts.get("idle    (<1 Mb/s)", 0)
    busy_n = sum(n for k, n in counts.items() if not k.startswith("idle"))
    if idle_n < MIN_BUCKET or busy_n < MIN_BUCKET:
        print(f"  INSUFFICIENT DATA — {idle_n} idle and {busy_n} loaded samples"
              f" (need {MIN_BUCKET}+ of each).")
        print("  Let netwatch run through a normal day, including a backup or a"
              "\n  large upload, then ask again.")
        return 0

    if not idle or not idle[1] or not busy:
        print("  Not enough contrast yet — need samples both idle and busy.")
        return 0
    worst = max((b[1] for b in busy if b[1]), default=None)
    if worst is None:
        print("  Not enough busy samples yet.")
        return 0
    ratio = worst / idle[1]
    print(f"  Idle p95 latency : {idle[1]:.1f} ms")
    print(f"  Busy p95 latency : {worst:.1f} ms   ({ratio:.1f}x idle)")
    if ratio >= 4:
        print("\n  ⚠ SEVERE bufferbloat. When this host is busy, everyone else on"
              "\n  the network feels it — calls stutter, pages hang. Shape this"
              "\n  host's egress before putting it in someone else's home.")
    elif ratio >= 2:
        print("\n  ⚠ Noticeable bufferbloat. Latency roughly doubles under load."
              "\n  Tolerable for browsing, irritating for calls and gaming.")
    else:
        print("\n  ✓ This host's traffic is NOT meaningfully degrading latency."
              "\n  Good neighbour: safe to run alongside other people's usage.")
    return 0


UPLOAD_ENDPOINT = "https://speed.cloudflare.com/__up"


def _upload_worker(stop_at, endpoint, chunk, counter, idx, errors):
    """Push data until stop_at, tallying bytes actually accepted.

    Records why it stopped. A load generator that silently fails turns this
    whole command into a machine for producing false reassurance.
    """
    while time.monotonic() < stop_at:
        try:
            req = urllib.request.Request(endpoint, data=chunk, method="POST")
            req.add_header("Content-Type", "application/octet-stream")
            # Cloudflare rejects the default Python-urllib agent with a 403.
            req.add_header("User-Agent", "netwatch/1.0")
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read(64)
            counter[idx] += len(chunk)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            return


def cmd_bufferbloat(args) -> int:
    """Measure the uplink and what saturating it does to everyone's latency.

    This is the number that decides whether a machine is safe to put in someone
    else's home. Raw bandwidth is not the problem — a server can saturate an
    uplink and still be a good neighbour if the queue is managed. What ruins a
    household is the latency other people see while that transfer runs.

    Run it once before adding the machine to a network, and again after shaping,
    to prove the shaping worked rather than assuming it.
    """
    import threading

    target = args.target
    print(f"netwatch bufferbloat — {socket.gethostname()}")
    print("=" * 62)

    print(f"\n1/2  idle latency to {target} ...", flush=True)
    idle = []
    for _ in range(args.samples):
        r = ping(target, timeout=2)
        if r.get("rtt_ms") is not None:
            idle.append(r["rtt_ms"])
        time.sleep(0.2)
    if not idle:
        print(f"  {target} is not answering — cannot measure.")
        return 1
    idle_p50, idle_p95 = _pct(idle, 50), _pct(idle, 95)
    print(f"     p50 {idle_p50:.1f} ms   p95 {idle_p95:.1f} ms   ({len(idle)} samples)")

    # Router uptime before the load. A latency grade cannot see the worst
    # failure mode there is: the router surviving the queue but not the load.
    gw = args.gateway or default_route()[1]
    epoch_before = None
    if gw:
        pm = natpmp_epoch(gw)
        epoch_before = pm.get("epoch_s") if pm.get("ok") else None

    print(f"\n2/2  saturating upload for {args.duration}s "
          f"({args.streams} streams) while measuring latency ...", flush=True)
    chunk = os.urandom(2_000_000)
    counter = [0] * args.streams
    errors: list[str] = []
    stop_at = time.monotonic() + args.duration
    threads = [threading.Thread(target=_upload_worker,
                                args=(stop_at, args.endpoint, chunk, counter, i,
                                      errors),
                                daemon=True)
               for i in range(args.streams)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    loaded = []
    while time.monotonic() < stop_at:
        r = ping(target, timeout=2)
        if r.get("rtt_ms") is not None:
            loaded.append(r["rtt_ms"])
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - t0
    mbps = sum(counter) * 8 / elapsed / 1e6

    if not loaded:
        print("  no latency samples under load — link died entirely.")
        return 1

    # Refuse to grade a measurement that did not happen. A load generator that
    # quietly uploaded nothing would otherwise score a perfect "A" and print a
    # shaping command derived from ~0 Mb/s, which would throttle the machine to
    # nothing. No result is far better than a confidently wrong one.
    if mbps < 1.0:
        print(f"     upload  {mbps:.2f} Mb/s  ← the load generator did not run")
        print("\nMEASUREMENT FAILED — no grade, no recommendation.")
        if errors:
            seen = []
            for e in errors:
                if e not in seen:
                    seen.append(e)
            for e in seen[:3]:
                print(f"  upload error: {e}")
        else:
            print("  uploads produced no bytes and reported no error.")
        print(f"  Check that {args.endpoint} is reachable, or pass --endpoint.")
        return 1

    load_p50, load_p95 = _pct(loaded, 50), _pct(loaded, 95)
    print(f"     upload  {mbps:.1f} Mb/s")
    print(f"     p50 {load_p50:.1f} ms   p95 {load_p95:.1f} ms   "
          f"({len(loaded)} samples)")

    # Did the router survive? This overrides every latency number below it: a
    # gateway that reboots under load makes the whole household's internet drop
    # for a minute, which no percentile of round-trip time can express.
    router_died = False
    if gw and epoch_before is not None:
        pm = natpmp_epoch(gw)
        after = pm.get("epoch_s") if pm.get("ok") else None
        if after is None:
            print("\n⚠ The router stopped answering NAT-PMP during this test.")
            router_died = True
        elif after < epoch_before:
            print(f"\n⛔ THE ROUTER RESTARTED DURING THIS TEST."
                  f"\n   It had been up {human_dur(epoch_before)}; it is now up"
                  f" {human_dur(after)}."
                  f"\n   Sustained upload at {mbps:.0f} Mb/s is enough to take this"
                  f" gateway down."
                  f"\n   Every device in the household lost its connection, not"
                  f" just this one.")
            router_died = True

    bloat = load_p95 - idle_p95
    ratio = load_p95 / idle_p95 if idle_p95 else 0
    print(f"\nLatency added by our own upload:  +{bloat:.0f} ms "
          f"({ratio:.1f}x idle p95)")

    if router_died:
        print("Grade: F — the gateway does not survive this load.")
        print("\n  Latency stayed fine right up until the router fell over, which"
              "\n  is exactly why a latency grade alone is not enough. Shape this"
              "\n  host's egress well below line rate before trusting it here:")
        rec = max(1, int(mbps * 0.5))
        print(f"    sudo tc qdisc replace dev "
              f"{args.iface or default_route()[0]} root cake bandwidth {rec}Mbit")
        print(f"  Then re-run. Start at half the measured rate and raise it only"
              f"\n  while the router keeps surviving.")
        return 1

    # Thresholds in absolute added latency: what a person actually perceives.
    if bloat < 20:
        grade, verdict = "A", "Good neighbour. Safe to run on a shared line."
    elif bloat < 60:
        grade, verdict = "B", ("Noticeable. Calls and games will feel it a bit;"
                               " browsing is fine.")
    elif bloat < 200:
        grade, verdict = "D", ("Bad neighbour. Video calls stutter and pages"
                               " hang for everyone else while this uploads.")
    else:
        grade, verdict = "F", ("Severe. This will make the household's internet"
                               " feel broken whenever the machine is busy.")
    print(f"Grade: {grade} — {verdict}")

    if grade == "A":
        print("\nNo shaping needed on this line. The uplink is wide enough that"
              "\nsaturating it barely queues, so a cap would cost throughput and"
              "\nbuy nothing. This verdict is specific to THIS network — re-run"
              "\nthe command on any line you move the machine to, because the"
              "\nanswer is set by the uplink, not by the machine.")
        return 0

    rec = max(1, int(mbps * 0.90))
    print(f"\nSuggested shaping (makes THIS host the managed bottleneck instead"
          f"\nof the router's dumb buffer):")
    print(f"    sudo tc qdisc replace dev {args.iface or default_route()[0]} "
          f"root cake bandwidth {rec}Mbit")
    print(f"\n  {rec} Mbit is 90% of the {mbps:.0f} Mb/s measured. Shaping below"
          f"\n  the real uplink is what keeps the queue here, where CAKE can"
          f"\n  manage it, rather than in the router where nothing can.")
    print("  Re-run this command afterwards; added latency should drop under 20 ms.")
    return 0


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def cmd_mark(args) -> int:
    append_jsonl(EVENTS, {"type": "mark", "ts": iso(now()),
                          "host": socket.gethostname(), "label": args.label})
    print(f"marked: {args.label}")
    return 0


def lan_address(iface: str | None) -> str | None:
    """This host's address on the local network, or None."""
    if not iface:
        return None
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show", "dev", iface],
                             capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def tailscale_state() -> dict:
    """Best-effort tailnet summary. Tailscale being absent is a fact, not an error."""
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, timeout=10).stdout
        d = json.loads(out)
    except Exception:
        return {}
    me = d.get("Self") or {}
    peers = []
    for p in (d.get("Peer") or {}).values():
        if p.get("Online"):
            peers.append(f"{p.get('HostName')} "
                         f"({'direct' if p.get('CurAddr') else 'relay ' + str(p.get('Relay'))})")
    return {"state": d.get("BackendState"),
            "name": (me.get("DNSName") or "").rstrip("."),
            "ip": (me.get("TailscaleIPs") or [None])[0],
            "peers": peers}


def ngrok_endpoint(wait_s: int = 0) -> str | None:
    """The ngrok TCP address, polling until it appears.

    Free-tier ngrok rolls a new host:port every time it starts, so this address
    is unknowable in advance and worthless if written down. It is also started
    from cron on a 5-minute tick, so at boot it is usually not up yet — hence
    the wait rather than a single look.
    """
    deadline = time.monotonic() + max(0, wait_s)
    while True:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:4040/api/tunnels", timeout=5) as r:
                for t in json.loads(r.read()).get("tunnels", []):
                    if t.get("proto") == "tcp":
                        return t.get("public_url")
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(5)


def cmd_announce(args) -> int:
    """Report how to reach this machine, over the one channel that does not need it.

    A house move invalidates every address at once: the public IP is new, the
    LAN is renumbered, and ngrok has rolled its host:port. The machine is the
    only party that knows all three, and Telegram is the only path out that
    needs no inbound reachability — so it says so itself, on boot, rather than
    leaving Louis to guess from the outside with nothing to guess from.
    """
    # ngrok first: its wait doubles as the settling time for everything else.
    # At boot the network is often not usable yet, and a public-IP probe taken
    # too early reports "unknown" for a machine that is merely still starting.
    ng = ngrok_endpoint(args.wait)
    iface, gateway = default_route()
    ip = public_ip_probe()
    ts = tailscale_state()

    prev = None
    try:
        prev = json.loads((STATE_DIR / PUBLIC_IP_STATE).read_text()).get("ip")
    except Exception:
        pass
    if ip and prev and ip != prev:
        ip_note = f"{ip}  (CHANGED from {prev})"
    elif ip:
        ip_note = f"{ip}  (unchanged)" if prev else ip
    else:
        ip_note = "unknown — no provider answered"

    lines = [f"{socket.gethostname()} is up",
             f"public IP : {ip_note}",
             f"LAN       : {lan_address(iface) or '?'} via {gateway or '?'} ({iface or '?'})"]
    if ts:
        lines.append(f"tailnet   : {ts.get('ip') or '?'}  {ts.get('name') or ''} "
                     f"[{ts.get('state')}]")
        if ts.get("peers"):
            lines.append(f"peers     : {', '.join(ts['peers'])}")
    else:
        lines.append("tailnet   : UNAVAILABLE — tailscale not reachable")
    lines.append(f"ngrok     : {ng or 'not up (cron starts it within 5 min)'}")

    body = "\n".join(lines)
    print(body)
    if not args.no_telegram:
        if telegram_send(body):
            print("\n(sent to Telegram)")
        else:
            print("\nWARNING: Telegram send FAILED — this message did not reach Louis",
                  file=sys.stderr)
            return 1
    return 0


def cmd_probe(args) -> int:
    """One-shot diagnostic — what netwatch sees right now."""
    iface, gateway = default_route()
    print(f"interface : {iface}")
    print(f"gateway   : {gateway}")
    print(f"link      : {link_state(iface)}")
    if gateway:
        print(f"gw ping   : {ping(gateway)}")
        pm = natpmp_epoch(gateway)
        print(f"nat-pmp   : {pm}")
        if pm.get("ok"):
            boot = datetime.fromtimestamp(time.time() - pm["epoch_s"]).astimezone()
            print(f"            router up {human_dur(pm['epoch_s'])} "
                  f"(booted {boot:%Y-%m-%d %H:%M:%S})")
        print(f"ssdp      : {ssdp_bootid()}")
        v = vendor_probe(gateway)
        print(f"vendor    : {v or 'none (generic router — portable probes only)'}")
    for t in args.wan_targets:
        print(f"wan {t:<9}: {ping(t)}")
    print(f"dns       : {dns_probe(args.dns_name)}")
    print(f"conntrack : {conntrack()}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="netwatch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--wan-targets", nargs="+", default=DEFAULT_WAN_TARGETS)
        sp.add_argument("--dns-name", default=DEFAULT_DNS_NAME)

    r = sub.add_parser("run", help="run the recorder (daemon)")
    common(r)
    r.add_argument("--interval", type=float, default=5.0)
    r.add_argument("--ping-timeout", type=int, default=2)
    r.add_argument("--alert-after", type=float, default=15.0,
                   help="only Telegram outages at least this long (seconds)")
    r.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    r.add_argument("--no-telegram", action="store_true")
    r.add_argument("--no-vendor", dest="vendor", action="store_false",
                   help="skip vendor-specific router API enrichment")
    r.add_argument("--no-samples", dest="record_samples", action="store_false")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--peer", default=os.environ.get("NETWATCH_PEER"),
                   help="address of the other recorder, to witness each other")
    r.add_argument("--track-public-ip", action="store_true",
                   default=bool(os.environ.get("NETWATCH_TRACK_PUBLIC_IP")),
                   help="hourly public-IP check; Telegram on change")
    r.add_argument("--healthcheck-url",
                   default=os.environ.get("NETWATCH_HEALTHCHECK_URL"),
                   help="dead-man's-switch ping URL; alarms externally on silence. "
                        "Prefer the env var — this is a capability, keep it out of "
                        "process listings and shell history.")
    r.set_defaults(func=lambda a: Runner(a).run(), vendor=True, record_samples=True)

    pr = sub.add_parser("probe", help="one-shot: show what netwatch sees now")
    common(pr)
    pr.set_defaults(func=cmd_probe)

    rp = sub.add_parser("report", help="summarise the ledger")
    rp.add_argument("--days", type=int, default=7)
    rp.set_defaults(func=cmd_report)

    im = sub.add_parser("impact", help="is THIS host degrading everyone else's latency?")
    im.add_argument("--days", type=int, default=7)
    im.add_argument("--direction", choices=["up", "down"], default="up")
    im.set_defaults(func=cmd_impact)

    st = sub.add_parser("status", help="machine-readable summary (JSON)")
    st.add_argument("--days", type=int, default=7)
    st.add_argument("--compact", action="store_true")
    st.set_defaults(func=cmd_status)

    pb = sub.add_parser("publish", help="push this host's summary to the dashboard host")
    pb.add_argument("--days", type=int, default=7)
    pb.add_argument("--remote", default=os.environ.get(
        "NETWATCH_PUBLISH_REMOTE", "TinyButMighty:/srv/network"))
    pb.add_argument("--quiet", action="store_true")
    pb.set_defaults(func=cmd_publish)

    bb = sub.add_parser("bufferbloat",
                        help="measure uplink + what saturating it does to latency")
    bb.add_argument("--duration", type=int, default=12)
    bb.add_argument("--streams", type=int, default=4)
    bb.add_argument("--samples", type=int, default=25, help="idle latency samples")
    bb.add_argument("--target", default="1.1.1.1")
    bb.add_argument("--iface", default=None)
    bb.add_argument("--gateway", default=None,
                    help="router address, for the survived-the-test check")
    bb.add_argument("--endpoint", default=UPLOAD_ENDPOINT)
    bb.set_defaults(func=cmd_bufferbloat)

    mk = sub.add_parser("mark", help="annotate the ledger (e.g. before/after a change)")
    mk.add_argument("label")
    mk.set_defaults(func=cmd_mark)

    an = sub.add_parser("announce",
                        help="Telegram how to reach this host (public IP, tailnet, ngrok)")
    an.add_argument("--wait", type=int, default=180,
                    help="seconds to wait for ngrok to come up (cron starts it "
                         "on a 5-minute tick, so at boot it is usually not up yet)")
    an.add_argument("--no-telegram", action="store_true",
                    help="print only; do not send")
    an.set_defaults(func=cmd_announce)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

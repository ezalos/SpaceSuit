#!/usr/bin/env python3
# ABOUTME: Tests for netwatch classification, transition ledger and router-restart detection.
# ABOUTME: Run with: python3 test_netwatch.py  (stdlib only, no framework needed).
"""Every check here must be able to fail.

These drive the real classifier and the real state machine over synthetic
samples. Nothing is mocked out except the passage of time and the shape of the
samples themselves — the logic under test is the shipped logic.
"""

import argparse
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import netwatch as nw

FAILURES = []
_real_telegram_send = nw.telegram_send
_real_public_ip_probe = nw.public_ip_probe
_real_state_dir = nw.STATE_DIR


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {name}")


def sample(**kw):
    """A healthy sample, overridable field by field."""
    s = {
        "ts": nw.iso(nw.now()),
        "iface": "eth0",
        "gateway": "192.0.2.1",
        "link": {"carrier": 1, "operstate": "up", "speed": "1000"},
        "gw": {"up": True, "rtt_ms": 0.5, "loss": 0.0},
        "wan": {"1.1.1.1": {"up": True, "rtt_ms": 4.0, "loss": 0.0},
                "9.9.9.9": {"up": True, "rtt_ms": 3.0, "loss": 0.0}},
        "dns": {"ok": True, "ms": 6.0},
        "host": {"conntrack": {"count": 200, "max": 262144},
                 "rx_bps": 1_000_000, "tx_bps": 2_000_000},
    }
    s.update(kw)
    # The real loop stamps this on every sample before the state machine sees
    # it; mirror that rather than hand-writing expected states in each test.
    s["state"] = nw.classify(s)
    return s


def test_classification():
    print("classification:")
    down = {"up": False, "rtt_ms": None, "loss": 1.0}
    check("healthy -> ok", nw.classify(sample()), "ok")
    check("carrier 0 -> link_down",
          nw.classify(sample(link={"carrier": 0, "operstate": "down", "speed": None})),
          "link_down")
    check("no default route -> no_route", nw.classify(sample(gateway=None)), "no_route")
    # Gateway unreachable is only an outage when it takes the internet with it.
    # With the WAN still answering it is gateway_silent — see
    # test_gateway_silent_is_not_an_outage for why.
    check("gateway unreachable + WAN down -> gateway_down",
          nw.classify(sample(gw=down, wan={"1.1.1.1": down, "9.9.9.9": down})),
          "gateway_down")
    check("all WAN targets down -> wan_down",
          nw.classify(sample(wan={"1.1.1.1": down, "9.9.9.9": down})), "wan_down")
    check("one WAN target down -> wan_degraded",
          nw.classify(sample(wan={"1.1.1.1": down,
                                  "9.9.9.9": {"up": True, "rtt_ms": 3.0, "loss": 0.0}})),
          "wan_degraded")
    check("DNS only -> dns_down",
          nw.classify(sample(dns={"ok": False, "ms": None, "err": "timeout"})),
          "dns_down")
    # Ordering matters: a dead link must not be reported as a DNS problem.
    check("link down beats dns down",
          nw.classify(sample(link={"carrier": 0, "operstate": "down", "speed": None},
                             dns={"ok": False, "ms": None})),
          "link_down")


def test_gateway_silent_is_not_an_outage():
    print("gateway silent on ICMP while the internet still works:")
    down = {"up": False, "rtt_ms": None, "loss": 1.0}
    up = {"up": True, "rtt_ms": 4.0, "loss": 0.0}

    # The case that produced the false alerts: the Livebox stops answering
    # pings to itself while its UPnP stack is busy, but forwards traffic fine.
    check("gw down + WAN reachable -> gateway_silent",
          nw.classify(sample(gw=down, wan={"1.1.1.1": up, "9.9.9.9": up})),
          "gateway_silent")
    check("gateway_silent is not a bad state",
          "gateway_silent" in nw.BAD_STATES, False)

    # A gateway that is down AND takes the internet with it is a real outage
    # and must still alert exactly as before.
    check("gw down + WAN all down -> gateway_down",
          nw.classify(sample(gw=down, wan={"1.1.1.1": down, "9.9.9.9": down})),
          "gateway_down")

    # With no WAN evidence we cannot conclude the internet is fine, so stay
    # conservative rather than silently downgrading a real fault.
    check("gw down + no WAN data -> gateway_down (no evidence)",
          nw.classify(sample(gw=down, wan={})), "gateway_down")

    # A more actionable fault still wins over the cosmetic one.
    check("gw down + WAN ok + DNS failing -> dns_down",
          nw.classify(sample(gw=down, wan={"1.1.1.1": up},
                             dns={"ok": False, "ms": None})),
          "dns_down")
    check("gw down + WAN partially up -> wan_degraded",
          nw.classify(sample(gw=down, wan={"1.1.1.1": up, "9.9.9.9": down})),
          "wan_degraded")

    # Unchanged behaviour: a healthy gateway must not be reported as silent.
    check("healthy stays ok", nw.classify(sample()), "ok")


def make_runner(tmp):
    nw.EVENTS = Path(tmp) / "events.jsonl"
    nw.SAMPLES = Path(tmp) / "samples.jsonl"
    args = argparse.Namespace(
        interval=5.0, ping_timeout=2, alert_after=15.0, keep_days=14,
        no_telegram=True, vendor=False, record_samples=False, quiet=True,
        wan_targets=["1.1.1.1", "9.9.9.9"], dns_name="example.com",
        healthcheck_url=None,
    )
    return nw.Runner(args)


def events(tmp):
    return list(nw.read_jsonl(Path(tmp) / "events.jsonl"))


def test_outage_ledger():
    print("outage ledger (healthy -> wan_down -> healthy):")
    down = {"up": False, "rtt_ms": None, "loss": 1.0}
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.check_transition(sample())                                   # ok
        r.check_transition(sample(wan={"1.1.1.1": down, "9.9.9.9": down}))  # break
        # rewind the clock so the outage has a measurable, assertable duration
        r.state_since = r.state_since - timedelta(seconds=42)
        r.check_transition(sample())                                   # recover

        evs = events(tmp)
        types = [e["type"] for e in evs]
        check("emitted start then outage", types, ["degraded_start", "outage"])
        out = evs[1]
        check("outage state", out["state"], "wan_down")
        check("outage blamed upstream", out["blame"],
              "ISP / upstream (router alive, internet not)")
        check("duration recorded (~42s)", 41 <= out["duration_s"] <= 44, True)
        check("recovery target", out["recovered_to"], "ok")
        check("snapshot kept host load", out["snapshot"]["host"]["tx_bps"], 2_000_000)

        msg = r.format_outage(out)
        check("alert names the duration", "42s" in msg, True)
        check("alert names this host's egress", "↑2.0 Mb/s" in msg, True)


def test_startup_during_outage():
    print("outage already in progress when netwatch starts:")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.check_transition(sample(link={"carrier": 0, "operstate": "down",
                                        "speed": None}))
        evs = events(tmp)
        check("startup fault is recorded", [e["type"] for e in evs], ["degraded_start"])
        check("flagged as at_startup", evs[0].get("at_startup"), True)
        check("blamed locally", evs[0]["blame"], "cable/switch port or router LAN side")


def test_router_restart_detection():
    print("router restart via NAT-PMP epoch:")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        # Router has been up 6.3 days, then comes back with a tiny epoch.
        r.check_router_restart(sample(router={"natpmp": {"ok": True, "epoch_s": 544382}}))
        check("no event on first observation", events(tmp), [])
        r.check_router_restart(sample(router={"natpmp": {"ok": True, "epoch_s": 88}}))
        evs = events(tmp)
        check("restart detected", [e["type"] for e in evs], ["router_restart"])
        check("detector named", evs[0]["detector"], "natpmp_epoch")
        check("previous uptime carried", evs[0]["prev_epoch_s"], 544382)

        # A rising epoch is the normal case and must stay silent.
        r.check_router_restart(sample(router={"natpmp": {"ok": True, "epoch_s": 400}}))
        check("rising epoch stays silent", len(events(tmp)), 1)

        # A failed probe must not be mistaken for a restart.
        r.check_router_restart(sample(router={"natpmp": {"ok": False, "err": "timeout"}}))
        check("failed probe is not a restart", len(events(tmp)), 1)


def test_bootid_restart():
    print("router restart via SSDP BOOTID:")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.check_router_restart(sample(router={"ssdp": {"ok": True, "bootid": "111"}}))
        r.check_router_restart(sample(router={"ssdp": {"ok": True, "bootid": "111"}}))
        check("stable bootid is silent", events(tmp), [])
        r.check_router_restart(sample(router={"ssdp": {"ok": True, "bootid": "222"}}))
        check("changed bootid detected",
              [e["detector"] for e in events(tmp)], ["ssdp_bootid"])


def test_subinterval_flap_detection():
    print("sub-interval link flaps (kernel carrier counter):")
    def link(n, carrier=1):
        return {"carrier": carrier, "operstate": "up" if carrier else "down",
                "speed": "1000", "carrier_changes": n}
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.check_flaps(sample(link=link(10)))
        check("first observation is silent", events(tmp), [])
        r.check_flaps(sample(link=link(10)))
        check("unchanged counter is silent", events(tmp), [])

        # Link bounced twice between samples; we never saw carrier=0.
        r.check_flaps(sample(link=link(12)))
        evs = events(tmp)
        check("flap detected without ever seeing carrier=0",
              [e["type"] for e in evs], ["link_flap"])
        check("transition count", evs[0]["transitions"], 2)

        # A counter reset is not a flap, but must not vanish silently either.
        r.check_flaps(sample(link=link(1)))
        check("counter reset reported distinctly",
              [e["type"] for e in events(tmp)][-1], "carrier_counter_reset")

    with tempfile.TemporaryDirectory() as tmp:
        # When the sampler DID see the link down, the outage path owns it —
        # don't double-report the same event as a flap.
        r = make_runner(tmp)
        r.check_flaps(sample(link=link(10, carrier=0)))
        r.check_flaps(sample(link=link(11, carrier=0)))
        check("no duplicate flap while link is observably down", events(tmp), [])


def test_coverage():
    print("coverage accounting (silence must not read as health):")
    base = 1_000_000.0
    hb = nw.HC_INTERVAL_S

    # Continuous heartbeats across the whole window -> full coverage.
    stamps = [base + i * hb for i in range(11)]
    obs, gaps = nw.coverage(stamps, base, base + 10 * hb)
    check("continuous heartbeats -> no gaps", gaps, [])
    check("continuous heartbeats -> full coverage", round(obs), 10 * hb)

    # Recorder dead in the middle: a long silence must surface as a gap.
    stamps = [base, base + hb, base + 9 * hb, base + 10 * hb]
    obs, gaps = nw.coverage(stamps, base, base + 10 * hb)
    check("silence detected as one gap", len(gaps), 1)
    check("gap has the right span", round(gaps[0][1] - gaps[0][0]), 8 * hb)
    check("coverage excludes the gap", round(obs), 2 * hb)

    # No events at all in the window: the entire window is unknown.
    obs, gaps = nw.coverage([], base, base + 10 * hb)
    check("no data -> zero coverage", obs, 0.0)
    check("no data -> whole window is a gap", gaps, [(base, base + 10 * hb)])


def test_alert_coalescing():
    print("alert coalescing (a crash loop must not become an alert storm):")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.args.no_telegram = False
        sent = []
        nw.telegram_send = lambda text: sent.append(text) or True
        try:
            for i in range(5):
                r.notify(f"restart {i}")
            check("only ALERT_MAX messages sent", len(sent), nw.ALERT_MAX)
            check("remainder counted, not lost", r.alert_suppressed,
                  5 - nw.ALERT_MAX)
            # force=True is for findings that must never be swallowed.
            r.notify("degradation", force=True)
            check("forced alert bypasses the cap", len(sent), nw.ALERT_MAX + 1)
        finally:
            nw.telegram_send = _real_telegram_send


def test_bad_neighbour():
    print("bad-neighbour detection (nobody else will report this):")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.args.no_telegram = True
        # Idle: 10ms. Loaded: 90ms. That is bufferbloat, and 9x the threshold-1.
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 10.0,
                                                   "loss": 0.0}}))
        check("idle traffic alone raises nothing", events(tmp), [])
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 40_000_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 90.0,
                                                   "loss": 0.0}}))
        evs = [e for e in events(tmp) if e["type"] == "bad_neighbour"]
        check("degradation under our own load is caught", len(evs), 1)
        check("ratio reported", evs[0]["ratio"] >= nw.IMPACT_RATIO, True)

    with tempfile.TemporaryDirectory() as tmp:
        # Heavy upload with latency that does NOT move is a good neighbour.
        r = make_runner(tmp)
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 10.0,
                                                   "loss": 0.0}}))
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 80_000_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 11.0,
                                                   "loss": 0.0}}))
        check("heavy load without latency cost stays silent",
              [e for e in events(tmp) if e["type"] == "bad_neighbour"], [])

    # Regression, deterministic. time.monotonic() starts near zero, so a 0.0
    # "last alert" baseline makes the cooldown look active for the first hours
    # of uptime and swallows the first alert — exactly the alert that matters
    # after a move. Assert the sentinel directly rather than relying on this
    # machine's uptime to expose it.
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        check("impact cooldown starts unset, not zero", r.last_impact_alert, None)
        check("heartbeat clock starts unset, not zero", r.last_hc_ping, None)

    with tempfile.TemporaryDirectory() as tmp:
        # And the cooldown does still suppress once it has genuinely fired.
        import time as _t
        r = make_runner(tmp)
        r.last_impact_alert = _t.monotonic()
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 10.0,
                                                   "loss": 0.0}}))
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 40_000_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 90.0,
                                                   "loss": 0.0}}))
        check("cooldown suppresses a repeat",
              [e for e in events(tmp) if e["type"] == "bad_neighbour"], [])


def test_bad_neighbour_needs_perceptible_harm():
    """The 2026-08-18 false positive, with its own numbers.

    netwatch woke Louis to say he was ruining his father's household. It had
    measured 1.1 ms idle -> 4.1 ms under a 48 Mb/s upload, on a line that has
    carried 423 Mb/s: three milliseconds, on a link whose own active test grades
    anything under twenty as "good neighbour". A ratio test with no absolute
    floor cannot tell jitter from harm at fibre latencies.
    """
    print("bad-neighbour detection requires perceptible harm, not a ratio:")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.args.no_telegram = True
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 1.1,
                                                   "loss": 0.0}}))
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 48_000_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 4.1,
                                                   "loss": 0.0}}))
        check("3.6x of 1.1 ms is not a bad neighbour",
              [e for e in events(tmp) if e["type"] == "bad_neighbour"], [])

    # ...and the same ratio at latencies a person can feel still alerts, so the
    # fix is a floor and not a mute button.
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.args.no_telegram = True
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 11.0,
                                                   "loss": 0.0}}))
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 48_000_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 41.0,
                                                   "loss": 0.0}}))
        evs = [e for e in events(tmp) if e["type"] == "bad_neighbour"]
        check("the same 3.6x at 11 ms -> 41 ms does alert", len(evs), 1)
        check("the alert carries the rate that caused it",
              evs[0].get("busy_mbps"), 48.0)
        check("and the added milliseconds", evs[0].get("added_ms"), 30.0)
        check("and the grade", evs[0].get("grade"), "B")

    # A trickle is not load. On this fibre 10 Mb/s is 2% of the line, so the old
    # fixed floor let near-idle samples into the "loaded" side and compared
    # jitter against jitter.
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.args.no_telegram = True
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 10.0,
                                                   "loss": 0.0}}))
        # 12 Mb/s alongside a 900 Mb/s peak: over the absolute floor, nowhere
        # near half the peak, so it must not count as loaded.
        r.check_impact(sample(host={"tx_bps": 900_000_000, "conntrack": {}},
                              wan={"1.1.1.1": {"up": True, "rtt_ms": 10.0,
                                               "loss": 0.0}}))
        for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
            r.check_impact(sample(host={"tx_bps": 12_000_000, "conntrack": {}},
                                  wan={"1.1.1.1": {"up": True, "rtt_ms": 90.0,
                                                   "loss": 0.0}}))
        check("a trickle next to a 900 Mb/s peak is not 'loaded'",
              [e for e in events(tmp) if e["type"] == "bad_neighbour"], [])


def test_bloat_grade_is_the_only_judge():
    """One grader, three call sites. They used to disagree.

    `bufferbloat` graded added milliseconds; `impact` and the live monitor
    graded ratios. That is how a measurement the active test calls grade A
    became a push notification calling it harm.
    """
    print("bloat grading (thresholds are perception, not arithmetic):")
    check("+3 ms on fibre is an A", nw.bloat_grade(1.1, 4.1)[0], "A")
    check("19 ms added is still an A", nw.bloat_grade(1.0, 20.0)[0], "A")
    check("20 ms added crosses into B", nw.bloat_grade(1.0, 21.0)[0], "B")
    check("60 ms added is a D", nw.bloat_grade(10.0, 70.0)[0], "D")
    check("200 ms added is an F", nw.bloat_grade(10.0, 210.0)[0], "F")
    check("a huge ratio at tiny latencies is still an A",
          nw.bloat_grade(0.5, 5.0)[0], "A")
    check("no added latency on a slow link is an A",
          nw.bloat_grade(200.0, 205.0)[0], "A")

    # The monitor must never contradict the grader: whatever it alerts on, the
    # grader must agree is worse than an A.
    with tempfile.TemporaryDirectory() as tmp:
        for idle_ms, loaded_ms in ((1.1, 4.1), (2.0, 12.0), (10.0, 90.0),
                                   (5.0, 24.0), (1.0, 3.5), (50.0, 150.0)):
            r = make_runner(tmp + f"/{idle_ms}-{loaded_ms}")
            r.args.no_telegram = True
            for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
                r.check_impact(sample(host={"tx_bps": 100_000, "conntrack": {}},
                                      wan={"1.1.1.1": {"up": True,
                                                       "rtt_ms": idle_ms,
                                                       "loss": 0.0}}))
            for _ in range(nw.IMPACT_MIN_PER_SIDE + 5):
                r.check_impact(sample(host={"tx_bps": 40_000_000,
                                            "conntrack": {}},
                                      wan={"1.1.1.1": {"up": True,
                                                       "rtt_ms": loaded_ms,
                                                       "loss": 0.0}}))
            alerted = bool([e for e in events(tmp + f"/{idle_ms}-{loaded_ms}")
                            if e["type"] == "bad_neighbour"])
            graded_harmful = nw.bloat_grade(idle_ms, loaded_ms)[0] != "A"
            check(f"monitor and grader agree at {idle_ms}->{loaded_ms} ms",
                  alerted, graded_harmful and loaded_ms >= nw.IMPACT_RATIO * idle_ms)


def test_public_ip_probe_validation():
    print("public IP probe (a bad provider must not look like a change):")
    import urllib.request as _u
    real = _u.urlopen

    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(bodies):
        seq = list(bodies)
        def _open(url, timeout=None):
            v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return Resp(v)
        return _open

    try:
        _u.urlopen = fake([b"203.0.113.7\n"])
        check("plain IP accepted", nw.public_ip_probe(["u1"]), "203.0.113.7")

        # An HTML error page must never be mistaken for an address.
        _u.urlopen = fake([b"<html>502 Bad Gateway</html>", b"203.0.113.7"])
        check("garbage rejected, next provider used",
              nw.public_ip_probe(["u1", "u2"]), "203.0.113.7")

        # Octet range matters: 999.1.1.1 is regex-shaped but not an address.
        _u.urlopen = fake([b"999.1.1.1", b"203.0.113.7"])
        check("out-of-range octets rejected",
              nw.public_ip_probe(["u1", "u2"]), "203.0.113.7")

        # A provider outage must yield None (no alert), not a false change.
        _u.urlopen = fake([OSError("down"), OSError("down")])
        check("all providers down -> None, never a false change",
              nw.public_ip_probe(["u1", "u2"]), None)
    finally:
        _u.urlopen = real


def test_public_ip_change():
    print("public IP change detection:")
    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        r.args.track_public_ip = True
        nw.STATE_DIR = Path(tmp)
        r.register_protected_ip = lambda: None  # don't shell out in tests
        try:
            nw.public_ip_probe = lambda *a, **k: "203.0.113.7"
            r.check_public_ip(sample(), force=True)
            check("first sighting is a baseline, not an alert",
                  [e["type"] for e in events(tmp)], ["public_ip_baseline"])

            r.check_public_ip(sample(), force=True)
            check("unchanged IP stays silent", len(events(tmp)), 1)

            nw.public_ip_probe = lambda *a, **k: "198.51.100.4"
            r.check_public_ip(sample(), force=True)
            evs = events(tmp)
            check("change detected", [e["type"] for e in evs][-1], "public_ip_change")
            check("old address carried", evs[-1]["previous"], "203.0.113.7")
            check("new address carried", evs[-1]["current"], "198.51.100.4")

            # Baseline must survive a restart: a fresh Runner over the same
            # state dir must not re-alert for an IP it already knows.
            r2 = make_runner(tmp)
            r2.args.track_public_ip = True
            nw.EVENTS = Path(tmp) / "events.jsonl"
            # Two events so far: baseline, then change. Asserting the exact
            # count is what makes a spurious re-alert visible here.
            r2.check_public_ip(sample(), force=True)
            check("restart does not re-alert for a known IP", len(events(tmp)), 2)

            # A failed lookup must never be read as a change.
            nw.public_ip_probe = lambda *a, **k: None
            r2.check_public_ip(sample(), force=True)
            check("failed lookup raises nothing", len(events(tmp)), 2)
        finally:
            nw.public_ip_probe = _real_public_ip_probe
            nw.STATE_DIR = _real_state_dir


def test_undeliverable_alert():
    print("undeliverable alert (a dead channel must not look like a quiet week):")
    import urllib.request as _u
    real_open = _u.urlopen

    with tempfile.TemporaryDirectory() as tmp:
        r = make_runner(tmp)
        try:
            # A send that fails must leave the alert in the permanent ledger,
            # so nothing Louis should have been told is lost.
            nw.telegram_send = lambda body: False
            r.deliver("boom")
            evs = [e for e in events(tmp) if e["type"] == "alert_undelivered"]
            check("failed send is recorded", len(evs), 1)
            check("the undelivered text is kept",
                  evs[0]["text"] if evs else None, "boom")

            # A send that succeeds must stay silent — otherwise the ledger
            # fills with noise and the real failures stop standing out.
            nw.telegram_send = lambda body: True
            r.deliver("fine")
            check("successful send records nothing",
                  len([e for e in events(tmp) if e["type"] == "alert_undelivered"]), 1)

            # The escalation is the point: a broken Telegram must reach him by
            # a different provider entirely.
            pinged = []
            _u.urlopen = lambda req, timeout=None: (
                pinged.append(req.full_url if hasattr(req, "full_url") else req),
                type("R", (), {"read": lambda self: b""})())[1]
            r.args.healthcheck_url = "https://hc-ping.com/abc"
            nw.telegram_send = lambda body: False
            r.deliver("boom again")
            check("dead-man's switch tripped once", len(pinged), 1)
            check("tripped via the /fail endpoint",
                  pinged[0] if pinged else None,
                  "https://hc-ping.com/abc/fail")

            # A trailing slash must not produce a double slash.
            pinged.clear()
            r.args.healthcheck_url = "https://hc-ping.com/abc/"
            r.deliver("boom thrice")
            check("trailing slash normalised", pinged[0] if pinged else None,
                  "https://hc-ping.com/abc/fail")

            # With no switch configured this must be a no-op, not a crash —
            # the recorder must never die because an alert could not be sent.
            pinged.clear()
            r.args.healthcheck_url = None
            r.deliver("boom quietly")
            check("no healthcheck configured -> no ping", len(pinged), 0)
            check("ledger still records it",
                  len([e for e in events(tmp) if e["type"] == "alert_undelivered"]), 4)
        finally:
            nw.telegram_send = _real_telegram_send
            _u.urlopen = real_open


def test_human_dur():
    print("duration formatting:")
    check("seconds", nw.human_dur(42), "42s")
    check("minutes", nw.human_dur(125), "2m05s")
    check("hours", nw.human_dur(7325), "2h02m")
    check("days", nw.human_dur(544382), "6d07h")


def main():
    for t in (test_classification, test_gateway_silent_is_not_an_outage, test_outage_ledger, test_startup_during_outage,
              test_router_restart_detection, test_bootid_restart,
              test_subinterval_flap_detection, test_coverage,
              test_alert_coalescing, test_bad_neighbour,
              test_bad_neighbour_needs_perceptible_harm,
              test_bloat_grade_is_the_only_judge,
              test_public_ip_probe_validation, test_public_ip_change,
              test_undeliverable_alert, test_human_dur):
        t()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print("  " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

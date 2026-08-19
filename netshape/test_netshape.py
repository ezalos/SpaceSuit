#!/usr/bin/env python3
# ABOUTME: Tests for netshape — config handling, plus REAL tc apply/revert in a netns.
# ABOUTME: Run with: python3 test_netshape.py  (stdlib only, no framework needed).
"""Every check here must be able to fail.

The interesting half of this file does not mock `tc`. It creates a network
namespace with `unshare -rn` (no root needed — the kernel grants it inside a user
namespace), puts a dummy interface in it, and drives the shipped code against the
real kernel qdisc. A shaper whose tests only assert on command strings would pass
while `tc` rejected every one of them.

The one thing that is faked is the gateway reachability probe, in the rollback
test: "the cap costs us the gateway" cannot be produced on demand in a namespace.
The rollback itself — noticing, deleting the qdisc, reporting — runs for real
against a real device.
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL  {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {name}")


def load_netshape():
    """Import the tool fresh, so NETSHAPE_CONF set just now is the one it uses."""
    loader = importlib.machinery.SourceFileLoader("netshape",
                                                  str(HERE / "netshape"))
    spec = importlib.util.spec_from_loader("netshape", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ------------------------------------------------------------------ config

def test_config():
    ns = load_netshape()
    print("config parsing:")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "netshape.conf"
        check("missing file yields defaults", ns.read_conf(path)["EGRESS_MBIT"],
              "0")
        path.write_text(
            "# a comment\n"
            "IFACE=enp9s0\n"
            "EGRESS_MBIT = 380 \n"
            'OPTIONS="diffserv4 nat"\n'
            "#EGRESS_MBIT=999\n"
            "NONSENSE=1\n")
        conf = ns.read_conf(path)
        check("iface read", conf["IFACE"], "enp9s0")
        check("whitespace stripped", conf["EGRESS_MBIT"], "380")
        check("quotes stripped", conf["OPTIONS"], "diffserv4 nat")
        check("commented-out setting ignored", ns.declared_mbit(conf), 380)
        check("unknown keys are not adopted", "NONSENSE" in conf, False)
        check("PROBE defaults empty", conf["PROBE"], "")

    print("config editing keeps the reasoning around the number:")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "netshape.conf"
        path.write_text("# why this number matters\nEGRESS_MBIT=0\nIFACE=auto\n")
        ns.write_conf("EGRESS_MBIT", "380", path)
        text = path.read_text()
        check("comment survives an edit", "# why this number matters" in text,
              True)
        check("value replaced in place", "EGRESS_MBIT=380" in text, True)
        check("no duplicate key", text.count("EGRESS_MBIT="), 1)
        check("other settings untouched", "IFACE=auto" in text, True)
        # RFC 5737 documentation address: this repo is public, and a real LAN
        # gateway in a test fixture is a real LAN layout in a public repo.
        ns.write_conf("PROBE", "192.0.2.1", path)
        check("a missing key is appended", ns.read_conf(path)["PROBE"],
              "192.0.2.1")
        check("editing rejects unknown keys",
              _raises(ns.write_conf, "HAX", "1", path), ValueError)

    print("the declared number:")
    check("off means zero", ns.declared_mbit({"EGRESS_MBIT": "off"}), 0)
    check("empty means zero", ns.declared_mbit({"EGRESS_MBIT": ""}), 0)
    check("float accepted", ns.declared_mbit({"EGRESS_MBIT": "380.0"}), 380)
    check("garbage refused, not silently zero",
          _raises(ns.declared_mbit, {"EGRESS_MBIT": "fast"}), ValueError)
    check("negative refused",
          _raises(ns.declared_mbit, {"EGRESS_MBIT": "-1"}), ValueError)

    print("the shipped example must be valid and must not shape anything:")
    example = ns.read_conf(HERE / "netshape.conf.example")
    check("example parses to no cap", ns.declared_mbit(example), 0)
    check("example is auto-interface", example["IFACE"], "auto")


def test_command_construction():
    ns = load_netshape()
    print("the tc invocation:")
    check("a cap becomes one cake replace",
          ns.apply_cmd("enp9s0", 380, "besteffort nat"),
          ["tc", "qdisc", "replace", "dev", "enp9s0", "root", "cake",
           "bandwidth", "380Mbit", "besteffort", "nat"])
    check("zero becomes a delete", ns.apply_cmd("enp9s0", 0, "besteffort nat"),
          ["tc", "qdisc", "del", "dev", "enp9s0", "root"])
    check("empty options add nothing", ns.apply_cmd("eth0", 100, ""),
          ["tc", "qdisc", "replace", "dev", "eth0", "root", "cake",
           "bandwidth", "100Mbit"])

    print("in-sync comparison (drift is a finding, so this must be exact):")
    check("cake at the declared rate is in sync",
          ns.in_sync({"kind": "cake", "mbit": 380}, 380), True)
    check("cake at the wrong rate is drift",
          ns.in_sync({"kind": "cake", "mbit": 200}, 380), False)
    check("no cake when a cap is declared is drift",
          ns.in_sync({"kind": "fq_codel", "mbit": None}, 380), False)
    check("fq_codel with no cap declared is in sync",
          ns.in_sync({"kind": "fq_codel", "mbit": None}, 0), True)
    check("noqueue with no cap declared is in sync (dummy/virtual devices)",
          ns.in_sync({"kind": "noqueue", "mbit": None}, 0), True)
    check("leftover cake with no cap declared is drift",
          ns.in_sync({"kind": "cake", "mbit": 380}, 0), False)


def _raises(fn, *args):
    try:
        fn(*args)
    except Exception as exc:      # noqa: BLE001 — the type is the assertion
        return type(exc)
    return None


# ------------------------------------------- the real kernel, in a namespace

IFACE = "nstest0"


def _write_conf(tmp: Path, **kw) -> Path:
    path = tmp / "netshape.conf"
    body = {"IFACE": IFACE, "EGRESS_MBIT": "0", "OPTIONS": "besteffort nat",
            "PROBE": ""}
    body.update({k: str(v) for k, v in kw.items()})
    path.write_text("".join(f"{k}={v}\n" for k, v in body.items()))
    return path


def test_in_netns():
    """Drive the shipped code against a real qdisc on a real device."""
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["ip", "link", "add", IFACE, "type", "dummy"], check=True)
    subprocess.run(["ip", "link", "set", IFACE, "up"], check=True)

    print("applying a cap to a real device:")
    conf = _write_conf(tmp, EGRESS_MBIT=380)
    os.environ["NETSHAPE_CONF"] = str(conf)
    ns = load_netshape()
    args = _args(force=False)
    check("apply succeeds", ns.cmd_apply(args), ns.OK)
    live = ns.live_root_qdisc(IFACE)
    check("the kernel really has cake", live["kind"], "cake")
    check("at the declared rate", live["mbit"], 380)
    check("status agrees", ns.cmd_status(_args(json=True)), ns.OK)

    print("idempotence (this runs at every boot):")
    check("a second apply is a no-op that still succeeds",
          ns.cmd_apply(args), ns.OK)
    check("and the rate did not move", ns.live_root_qdisc(IFACE)["mbit"], 380)

    print("changing the number:")
    check("set 200 succeeds", ns.cmd_set(_args(value="200")), ns.OK)
    check("the wire followed", ns.live_root_qdisc(IFACE)["mbit"], 200)
    check("and so did the config", ns.declared_mbit(ns.read_conf(conf)), 200)

    print("drift is detected, not assumed away:")
    subprocess.run(["tc", "qdisc", "del", "dev", IFACE, "root"], check=False)
    check("status reports drift when the qdisc vanishes",
          ns.cmd_status(_args(json=True)), ns.FINDING)
    check("apply repairs it", ns.cmd_apply(args), ns.OK)
    check("repaired to the declared rate",
          ns.live_root_qdisc(IFACE)["mbit"], 200)

    print("stop must not rewrite the policy:")
    check("clear unshapes the wire", ns.cmd_clear(args), ns.OK)
    check("nothing is shaped now",
          ns.live_root_qdisc(IFACE)["kind"] != "cake", True)
    check("the declared policy is untouched",
          ns.declared_mbit(ns.read_conf(conf)), 200)
    check("so apply brings it straight back", ns.cmd_apply(args), ns.OK)
    check("back at the declared rate", ns.live_root_qdisc(IFACE)["mbit"], 200)

    print("turning the policy off:")
    check("off succeeds", ns.cmd_set(_args(value="off")), ns.OK)
    check("wire unshaped", ns.live_root_qdisc(IFACE)["kind"] != "cake", True)
    check("config says zero", ns.declared_mbit(ns.read_conf(conf)), 0)
    check("apply on an already-unshaped device succeeds",
          ns.cmd_apply(args), ns.OK)

    print("the lockout guard (this machine is only reachable over the network):")
    check("a cap below the floor is refused",
          ns.cmd_set(_args(value=str(ns.MIN_MBIT - 1))), ns.FINDING)
    check("and nothing was written",
          ns.declared_mbit(ns.read_conf(conf)), 0)
    check("--force allows it", ns.cmd_set(_args(value="1", force=True)), ns.OK)
    check("forced cap is really on the wire",
          ns.live_root_qdisc(IFACE)["mbit"], 1)
    ns.cmd_set(_args(value="off"))

    print("rollback when the cap costs us the gateway:")
    conf = _write_conf(tmp, EGRESS_MBIT=380, PROBE="192.0.2.1")
    os.environ["NETSHAPE_CONF"] = str(conf)
    ns = load_netshape()
    probes = iter([True, False])          # reachable before, gone after
    ns.gateway_reachable = lambda gw: next(probes)
    check("apply reports the failure", ns.cmd_apply(_args()), ns.FINDING)
    check("and the qdisc was actually removed",
          ns.live_root_qdisc(IFACE)["kind"] != "cake", True)

    print("a healthy apply is not rolled back:")
    ns = load_netshape()
    ns.gateway_reachable = lambda gw: True
    check("apply succeeds", ns.cmd_apply(_args()), ns.OK)
    check("cap stays on the wire", ns.live_root_qdisc(IFACE)["mbit"], 380)

    print("an unreachable gateway before the change is not our fault:")
    ns = load_netshape()
    ns.cmd_clear(_args())
    ns.gateway_reachable = lambda gw: False
    check("apply still succeeds", ns.cmd_apply(_args()), ns.OK)
    check("and the cap is applied", ns.live_root_qdisc(IFACE)["mbit"], 380)


class _args:
    """Stand-in for argparse's namespace."""

    def __init__(self, **kw):
        self.force = kw.pop("force", False)
        self.json = kw.pop("json", False)
        for k, v in kw.items():
            setattr(self, k, v)


def main() -> int:
    if "--in-netns" in sys.argv:
        # Child half: we are root inside a fresh network namespace.
        test_in_netns()
        return 1 if FAILURES else 0

    test_config()
    test_command_construction()

    print("real tc, in a network namespace:")
    if not shutil.which("unshare"):
        print("  SKIP  unshare(1) not installed — the kernel-facing half of"
              " this suite did not run")
        FAILURES.append("unshare missing: tc was never exercised")
    else:
        proc = subprocess.run(
            ["unshare", "-rn", sys.executable, str(Path(__file__).resolve()),
             "--in-netns"],
            text=True, capture_output=True)
        print("\n".join("  " + line for line in
                        (proc.stdout or "").rstrip().splitlines()))
        if proc.returncode != 0:
            FAILURES.append("namespace half failed"
                            + (f": {proc.stderr.strip()}" if proc.stderr else ""))
            if proc.stderr:
                print("  stderr: " + proc.stderr.strip())

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

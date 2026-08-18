# ABOUTME: The dead-man's switch pings from the PUBLISHER, gated on recorder
# ABOUTME: freshness -- born from the 5-day TBM wedge the old design slept through.
import importlib.util
import json
import sys
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "netwatch", Path(__file__).resolve().parent.parent / "netwatch" / "netwatch.py")
netwatch = importlib.util.module_from_spec(_SPEC)
sys.modules["netwatch"] = netwatch
_SPEC.loader.exec_module(netwatch)


def _stamp(events: Path, age_s: float) -> None:
    ts = datetime.fromtimestamp(time.time() - age_s).astimezone().isoformat()
    with events.open("a") as fh:
        fh.write(json.dumps({"type": "heartbeat", "ts": ts, "host": "t", "state": "ok"}) + "\n")


@pytest.fixture
def events(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(netwatch, "EVENTS", path)
    return path


@pytest.fixture
def pings(monkeypatch):
    calls = []

    class _Resp:
        def read(self):
            return b"OK"

    monkeypatch.setattr(netwatch.urllib.request, "urlopen",
                        lambda url, timeout=10: calls.append(url) or _Resp())
    return calls


def _args(**kw):
    base = {"healthcheck_url": "https://hc.example/uuid", "quiet": True}
    base.update(kw)
    return types.SimpleNamespace(**base)


# --- last_heartbeat_age -------------------------------------------------------

def test_age_of_newest_stamp(events):
    _stamp(events, 900)
    _stamp(events, 30)
    age = netwatch.last_heartbeat_age()
    assert age is not None and 25 <= age <= 60


def test_age_none_without_ledger(events):
    assert netwatch.last_heartbeat_age() is None


def test_age_tail_reads_past_64k_of_noise(events):
    _stamp(events, 30)
    with events.open("a") as fh:
        for i in range(2000):
            fh.write(json.dumps({"type": "noise", "n": i, "pad": "x" * 40}) + "\n")
    # newest heartbeat is >64KiB from the end: tail window must miss it and
    # report None rather than scan the whole permanent ledger
    assert events.stat().st_size > 65536
    assert netwatch.last_heartbeat_age() is None


# --- _publish_heartbeat gate --------------------------------------------------

def test_pings_on_fresh_stamp(events, pings):
    _stamp(events, 30)
    netwatch._publish_heartbeat(_args())
    assert len(pings) == 1


def test_withholds_on_stale_stamp(events, pings, capsys):
    _stamp(events, netwatch.HC_INTERVAL_S * 2 + 60)
    netwatch._publish_heartbeat(_args())
    assert pings == []
    assert "withheld" in capsys.readouterr().err


def test_withholds_without_any_stamp(events, pings, capsys):
    netwatch._publish_heartbeat(_args())
    assert pings == []
    assert "missing" in capsys.readouterr().err


def test_no_url_no_ping(events, pings):
    _stamp(events, 30)
    netwatch._publish_heartbeat(_args(healthcheck_url=None))
    assert pings == []


def test_sink_failure_never_raises(events, monkeypatch):
    _stamp(events, 30)
    def boom(url, timeout=10):
        raise OSError("sink down")
    monkeypatch.setattr(netwatch.urllib.request, "urlopen", boom)
    netwatch._publish_heartbeat(_args())  # must not raise


# --- recorder heartbeat: stamps the ledger, never pings -----------------------

def test_recorder_heartbeat_stamps_but_never_pings(events, pings):
    fake = types.SimpleNamespace(last_hc_ping=None,
                                 args=types.SimpleNamespace(healthcheck_url="https://hc.example/uuid"))
    netwatch.Runner.heartbeat(fake, {"ts": datetime.now().astimezone().isoformat(),
                                     "state": "ok"})
    assert pings == []
    lines = [json.loads(l) for l in events.read_text().splitlines()]
    assert [e["type"] for e in lines] == ["heartbeat"]

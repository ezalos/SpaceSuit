# ABOUTME: Tests Installs/install-pass-cli.sh against a fake curl: hash match installs, mismatch refuses.
# ABOUTME: Never touches the network or the real ~/.local/bin; HOME and PATH are redirected per test.
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "Installs" / "install-pass-cli.sh"


def _fake_curl(bin_dir: Path, index_file: Path, payload_file: Path):
    # curl -fsSL <url> -o <out>: copies the index fixture for the .json URL,
    # the payload fixture otherwise. Fixtures are written by the test itself
    # (see _run) so arbitrary payload bytes never round-trip through shell
    # quoting inside this generated script.
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "out=''; url=''\n"
        "while [ $# -gt 0 ]; do case \"$1\" in -o) out=\"$2\"; shift;; -*) ;; *) url=\"$1\";; esac; shift; done\n"
        f"case \"$url\" in *.json) cp '{index_file}' \"$out\";; *) cp '{payload_file}' \"$out\";; esac\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, index, payload):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    index_file = tmp_path / "index.json"
    index_file.write_bytes(json.dumps(index).encode())
    payload_file = tmp_path / "payload.bin"
    payload_file.write_bytes(payload)
    _fake_curl(fakebin, index_file, payload_file)
    env = {**os.environ, "HOME": str(home), "PATH": f"{fakebin}:{os.environ['PATH']}",
           "PASS_CLI_INDEX_URL": "https://example.invalid/versions.json"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)
    return r, home / ".local" / "bin" / "pass-cli"


def _index(sha):
    return {"passCliVersions": {"version": "9.9.9", "urls": {
        "linux": {"x86_64": {"url": "https://example.invalid/pass-cli", "hash": sha},
                  "aarch64": {"url": "https://example.invalid/pass-cli", "hash": sha}},
        "macos": {"aarch64": {"url": "https://example.invalid/pass-cli", "hash": sha},
                  "x86_64": {"url": "https://example.invalid/pass-cli", "hash": sha}}}}}


def test_install_pass_cli_installs_on_hash_match(tmp_path):
    payload = b"#!/bin/sh\necho 'Proton Pass CLI 9.9.9'\n"
    r, installed = _run(tmp_path, _index(hashlib.sha256(payload).hexdigest()), payload)
    assert r.returncode == 0, r.stderr
    assert installed.exists() and os.access(installed, os.X_OK)


def test_install_pass_cli_refuses_hash_mismatch(tmp_path):
    payload = b"#!/bin/sh\necho tampered\n"
    r, installed = _run(tmp_path, _index("0" * 64), payload)
    assert r.returncode != 0
    assert "MISMATCH" in r.stderr
    assert not installed.exists()

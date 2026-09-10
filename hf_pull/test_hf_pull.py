#!/usr/bin/env python3
# ABOUTME: Tests for hf_pull: the two checksums must match what git and sha256sum compute,
# ABOUTME: and the rate parser must read curl-style values. No network, no mocks.
import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hf_pull  # noqa: E402


def test_git_blob_sha1_matches_git():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b'{"a": 1}\n' * 100)
    try:
        want = subprocess.run(["git", "hash-object", f.name], capture_output=True, text=True, check=True).stdout.strip()
        assert hf_pull.digest(f.name, lfs=False, size=os.path.getsize(f.name)) == want
    finally:
        os.unlink(f.name)


def test_lfs_sha256_matches_hashlib():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(os.urandom(3 << 20))
    try:
        want = hashlib.sha256(open(f.name, "rb").read()).hexdigest()
        assert hf_pull.digest(f.name, lfs=True, size=os.path.getsize(f.name)) == want
    finally:
        os.unlink(f.name)


def test_parse_rate():
    assert hf_pull.parse_rate("25M") == 25 * 1024**2
    assert hf_pull.parse_rate("800k") == 800 * 1024
    assert hf_pull.parse_rate("1g") == 1024**3
    assert hf_pull.parse_rate("4096") == 4096


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)

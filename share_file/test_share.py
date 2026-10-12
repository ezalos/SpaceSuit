#!/usr/bin/env python3
# ABOUTME: Tests for share-file's local-folder mode, its config refusals, and the unchanged remote mode.
# ABOUTME: Run with: python3 test_share.py  (stdlib only; runs the real share.py as a subprocess).
"""Every check drives the shipped CLI.

HOME points at a temporary directory so no real ~/.config/share-file/env is read.
Remote mode runs against stand-in ssh/scp scripts that only log their argv, so
nothing leaves the machine.
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

SHARE = Path(__file__).resolve().parent / "share.py"
BASE_URL = "https://share.example.invalid"
TOKEN_RE = r"[A-Za-z0-9_-]{32}"
ONE_HOUR_S = 3600
CLOCK_SLACK_S = 60
# The tightest umask a caller may have: the shared file must still come out world-readable.
STRICT_UMASK = 0o077
CLEANUP = SHARE.parent / "cleanup.py"
FAKE_TOOL = '#!/bin/sh\necho "$(basename "$0") $*" >> "$FAKE_LOG"\n'


class ShareFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="share-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.fakebin = self.tmp / "bin"
        self.fakebin.mkdir()
        self.log = self.tmp / "fake.log"
        for tool in ("ssh", "scp"):
            f = self.fakebin / tool
            f.write_text(FAKE_TOOL)
            f.chmod(0o755)

    def run_share(self, *argv, **env):
        full = {"HOME": str(self.home), "FAKE_LOG": str(self.log),
                "PATH": f"{self.fakebin}:{os.environ['PATH']}"}
        full.update(env)
        return subprocess.run([sys.executable, str(SHARE), *argv], env=full,
                              capture_output=True, text=True,
                              preexec_fn=lambda: os.umask(STRICT_UMASK))

    def test_local_mode_copies_file_writes_expires_prints_quoted_url(self):
        src = self.tmp / "my report.txt"
        src.write_text("hello\n")
        root = self.tmp / "served"
        before = int(time.time())
        r = self.run_share(str(src), "--duration", "1h",
                           SHARE_FILE_LOCAL_ROOT=str(root), SHARE_FILE_BASE_URL=BASE_URL)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = re.fullmatch(rf"{re.escape(BASE_URL)}/({TOKEN_RE})/my%20report\.txt\n", r.stdout)
        self.assertIsNotNone(m, r.stdout)
        token_dir = root / m.group(1)
        self.assertEqual((token_dir / "my report.txt").read_text(), "hello\n")
        self.assertEqual(stat.S_IMODE(token_dir.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((token_dir / "my report.txt").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((token_dir / ".expires").stat().st_mode), 0o644)
        expires = int((token_dir / ".expires").read_text())
        self.assertTrue(before + ONE_HOUR_S <= expires <= before + ONE_HOUR_S + CLOCK_SLACK_S)
        self.assertIn("expires: ", r.stderr)
        self.assertIn("size: 6 B (6 bytes)", r.stderr)
        self.assertFalse(self.log.exists(), "local mode must not call ssh or scp")

    def test_local_mode_zips_a_directory(self):
        d = self.tmp / "bundle"
        d.mkdir()
        (d / "a.txt").write_text("a")
        root = self.tmp / "served"
        r = self.run_share(str(d), "--local-root", str(root), "--base-url", BASE_URL)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = re.fullmatch(rf"{re.escape(BASE_URL)}/({TOKEN_RE})/bundle\.zip\n", r.stdout)
        self.assertIsNotNone(m, r.stdout)
        with zipfile.ZipFile(root / m.group(1) / "bundle.zip") as z:
            self.assertIn("bundle/a.txt", z.namelist())

    def test_failed_local_copy_still_expires_and_is_reaped(self):
        if os.geteuid() == 0:
            self.skipTest("root reads a mode-000 file")
        src = self.tmp / "locked.txt"
        src.write_text("secret")
        src.chmod(0)
        root = self.tmp / "served"
        r = self.run_share(str(src), "--local-root", str(root), "--base-url", BASE_URL)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertRegex(r.stderr, r"^share-file: local copy failed: ")
        self.assertNotIn("Traceback", r.stderr)
        (token_dir,) = list(root.iterdir())
        self.assertTrue((token_dir / ".expires").is_file())
        (token_dir / ".expires").write_text("1\n")
        c = subprocess.run([sys.executable, str(CLEANUP), "--root", str(root)],
                           capture_output=True, text=True)
        self.assertEqual(c.returncode, 0, c.stderr)
        self.assertIn("removed=1", c.stderr)
        self.assertEqual(list(root.iterdir()), [])

    def test_local_mode_refuses_dot_names(self):
        dotfile = self.tmp / ".expires"
        dotfile.write_text("x")
        dotdir = self.tmp / ".bundle"
        dotdir.mkdir()
        (dotdir / "a.txt").write_text("a")
        root = self.tmp / "served"
        for src in (dotfile, dotdir):
            r = self.run_share(str(src), "--local-root", str(root), "--base-url", BASE_URL)
            self.assertEqual(r.returncode, 2, src)
            self.assertIn("starting with '.'", r.stderr)
            self.assertFalse(root.exists())

    def test_host_and_local_root_together_refused(self):
        src = self.tmp / "x.txt"
        src.write_text("x")
        root = self.tmp / "served"
        r = self.run_share(str(src), SHARE_FILE_HOST="somehost",
                           SHARE_FILE_LOCAL_ROOT=str(root), SHARE_FILE_BASE_URL=BASE_URL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ambiguous", r.stderr)
        self.assertFalse(root.exists())
        self.assertFalse(self.log.exists())

    def test_neither_host_nor_local_root_refused(self):
        src = self.tmp / "x.txt"
        src.write_text("x")
        r = self.run_share(str(src), SHARE_FILE_BASE_URL=BASE_URL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SHARE_FILE_LOCAL_ROOT", r.stderr)
        self.assertFalse(self.log.exists())

    def test_remote_mode_still_requires_host(self):
        src = self.tmp / "x.txt"
        src.write_text("x")
        r = self.run_share(str(src), "--remote-root", "/srv/share", "--base-url", BASE_URL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--host", r.stderr)
        self.assertFalse(self.log.exists())

    def test_remote_mode_runs_the_same_three_commands(self):
        src = self.tmp / "x.txt"
        src.write_text("x")
        r = self.run_share(str(src), SHARE_FILE_HOST="somehost", SHARE_FILE_BASE_URL=BASE_URL)
        self.assertEqual(r.returncode, 0, r.stderr)
        token = re.fullmatch(rf"{re.escape(BASE_URL)}/({TOKEN_RE})/x\.txt\n", r.stdout).group(1)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 3, calls)
        self.assertEqual(calls[0], f"ssh somehost mkdir -p /srv/share/{token}")
        self.assertEqual(calls[1], f"scp -q {src} somehost:/srv/share/{token}/")
        self.assertRegex(calls[2], rf"^ssh somehost echo \d+ > /srv/share/{token}/\.expires$")


if __name__ == "__main__":
    unittest.main()

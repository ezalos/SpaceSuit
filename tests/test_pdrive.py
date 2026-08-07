# ABOUTME: Tests for the pdrive wrapper: arg mapping to proton-drive and auth-failure translation.
# ABOUTME: Uses a stub proton-drive binary on PATH; no network, no real Drive session.

import os
import stat
import subprocess
from pathlib import Path

SETUP_BIN = Path(__file__).resolve().parent.parent / "bin"

STUB_OK = """#!/usr/bin/env bash
echo "$@" > "$STUB_ARGS_FILE"
echo "stub-ok"
"""

# Byte-identical to the real logged-out output observed on 2026-07-18 (exit 1).
STUB_AUTH_FAIL = """#!/usr/bin/env bash
echo "You need to login first"
exit 1
"""


def run_pdrive(tmp_path, args, stub_body):
    stub = tmp_path / "proton-drive"
    stub.write_text(stub_body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    args_file = tmp_path / "recorded_args"
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["STUB_ARGS_FILE"] = str(args_file)
    proc = subprocess.run(
        [str(SETUP_BIN / "pdrive"), *args],
        capture_output=True, text=True, env=env,
    )
    recorded = args_file.read_text().strip() if args_file.exists() else None
    return proc, recorded


def test_push_maps_to_upload_with_default_dir(tmp_path):
    proc, recorded = run_pdrive(tmp_path, ["push", "file.txt"], STUB_OK)
    assert proc.returncode == 0
    assert recorded == "filesystem upload file.txt /my-files/TheBeast"


def test_push_with_explicit_remote_dir(tmp_path):
    _, recorded = run_pdrive(tmp_path, ["push", "file.txt", "/my-files/Docs"], STUB_OK)
    assert recorded == "filesystem upload file.txt /my-files/Docs"


def test_pull_maps_to_download_default_cwd(tmp_path):
    _, recorded = run_pdrive(tmp_path, ["pull", "/my-files/TheBeast/file.txt"], STUB_OK)
    assert recorded == "filesystem download /my-files/TheBeast/file.txt ."


def test_ls_defaults_to_remote_root(tmp_path):
    _, recorded = run_pdrive(tmp_path, ["ls"], STUB_OK)
    assert recorded == "filesystem list /my-files"


def test_login_maps_to_auth_login(tmp_path):
    proc, recorded = run_pdrive(tmp_path, ["login"], STUB_OK)
    assert proc.returncode == 0
    assert recorded == "auth login"


def test_auth_failure_translated(tmp_path):
    proc, _ = run_pdrive(tmp_path, ["push", "file.txt"], STUB_AUTH_FAIL)
    assert proc.returncode == 3
    assert "pdrive login" in proc.stderr


def test_no_args_shows_usage(tmp_path):
    proc, _ = run_pdrive(tmp_path, [], STUB_OK)
    assert proc.returncode == 2
    assert "Usage" in proc.stderr

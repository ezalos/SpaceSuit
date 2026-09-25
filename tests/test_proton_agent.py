# ABOUTME: Tests bin/proton-agent against a fake pass-cli that records every call: one login under
# ABOUTME: concurrency, no health check on the happy path, and the RAM-only cache (TTL, modes, scope).
import os
import shutil
import stat
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "bin" / "proton-agent"

FAKE_PASS_CLI = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/calls"
case "$1" in
  info) [ -f "$FAKE_DIR/session" ] ;;
  login) sleep 0.3; touch "$FAKE_DIR/session"; echo x >> "$FAKE_DIR/logins" ;;
  item)
    [ -f "$FAKE_DIR/session" ] || { echo "not logged in" >&2; exit 1; }
    case "$3" in *missing*) echo "Error finding item" >&2; exit 1 ;; esac
    echo "VALUE-for-$3" ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pc = bindir / "pass-cli"
    pc.write_text(FAKE_PASS_CLI)
    pc.chmod(pc.stat().st_mode | stat.S_IEXEC)
    pat = tmp_path / "ctx.pat"
    pat.write_text("fake-token")
    ctx = f"test-{uuid.uuid4().hex[:8]}"
    e = {k: v for k, v in os.environ.items() if not k.startswith("PROTON")}
    e.update({
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_DIR": str(fake_dir),
        "PROTON_AGENT_CONTEXT": ctx,
        "PROTON_AGENT_PAT_FILE": str(pat),
        "PROTON_PASS_SESSION_DIR": str(tmp_path / "session"),
        "XDG_RUNTIME_DIR": str(tmp_path),  # not under /run/user: cache off unless a test opts in
    })
    yield e, fake_dir, ctx
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if rt.startswith("/run/user/"):
        shutil.rmtree(Path(rt) / "proton-agent-cache" / ctx, ignore_errors=True)


def run(e, *args):
    return subprocess.run([str(AGENT), *args], capture_output=True, text=True, env=e, timeout=60)


def calls(fake_dir, word):
    f = fake_dir / "calls"
    return [l for l in f.read_text().splitlines() if l.split()[0] == word] if f.exists() else []


def test_happy_path_is_a_single_call(env):
    e, fake, _ = env
    (fake / "session").touch()
    r = run(e, "item", "view", "pass://s/a/f")
    assert r.returncode == 0 and r.stdout.strip() == "VALUE-for-pass://s/a/f"
    assert (fake / "calls").read_text().splitlines() == ["item view pass://s/a/f"]  # no `info` first


def test_expired_session_logs_in_once_then_retries(env):
    e, fake, _ = env
    r = run(e, "item", "view", "pass://s/a/f")
    assert r.returncode == 0 and r.stdout.strip() == "VALUE-for-pass://s/a/f"
    assert len(calls(fake, "login")) == 1
    assert len(calls(fake, "item")) == 2


def test_concurrent_callers_share_one_login(env):
    e, fake, _ = env
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda i: run(e, "item", "view", f"pass://s/a/f{i}"), range(6)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    assert len((fake / "logins").read_text().splitlines()) == 1


def test_bad_ref_with_a_valid_session_never_logs_in_and_passes_the_error(env):
    e, fake, _ = env
    (fake / "session").touch()
    r = run(e, "item", "view", "pass://s/missing/f")
    assert r.returncode != 0 and "Error finding item" in r.stderr
    assert calls(fake, "login") == []


@pytest.fixture
def ram_env(env):
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not rt.startswith("/run/user/") or not os.path.isdir(rt):
        pytest.skip("needs a per-user /run/user runtime dir")
    e, fake, ctx = env
    e = dict(e, XDG_RUNTIME_DIR=rt)
    (fake / "session").touch()
    return e, fake, ctx, Path(rt) / "proton-agent-cache" / ctx


def test_cache_serves_the_second_call_from_ram(ram_env):
    e, fake, ctx, cache = ram_env
    a = run(e, "item", "view", "pass://s/a/f")
    b = run(e, "item", "view", "pass://s/a/f")
    assert a.stdout == b.stdout and a.stdout.strip() == "VALUE-for-pass://s/a/f"
    assert len(calls(fake, "item")) == 1
    files = list(cache.iterdir())
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700


def test_cache_expires_after_ttl(ram_env):
    e, fake, ctx, cache = ram_env
    run(e, "item", "view", "pass://s/a/f")
    for f in cache.iterdir():
        old = f.stat().st_mtime - 1000
        os.utime(f, (old, old))
    run(e, "item", "view", "pass://s/a/f")
    assert len(calls(fake, "item")) == 2


def test_ttl_zero_disables_the_cache(ram_env):
    e, fake, ctx, cache = ram_env
    e = dict(e, PROTON_AGENT_CACHE_TTL="0")
    run(e, "item", "view", "pass://s/a/f")
    run(e, "item", "view", "pass://s/a/f")
    assert len(calls(fake, "item")) == 2
    assert not cache.exists()


def test_no_cache_outside_a_run_user_runtime_dir(env, tmp_path):
    e, fake, ctx = env  # XDG_RUNTIME_DIR is tmp_path here
    (fake / "session").touch()
    run(e, "item", "view", "pass://s/a/f")
    run(e, "item", "view", "pass://s/a/f")
    assert len(calls(fake, "item")) == 2
    assert not (tmp_path / "proton-agent-cache").exists()


def test_failed_lookups_are_never_cached(ram_env):
    e, fake, ctx, cache = ram_env
    run(e, "item", "view", "pass://s/missing/f")
    run(e, "item", "view", "pass://s/missing/f")
    assert len(calls(fake, "item")) == 2
    assert not cache.exists() or not any(cache.iterdir())


def test_unset_runtime_dir_falls_back_to_run_user(env):
    uid_dir = Path(f"/run/user/{os.getuid()}")
    if not uid_dir.is_dir() or stat.S_IMODE(uid_dir.stat().st_mode) != 0o700:
        pytest.skip("needs a 0700 /run/user/<uid>")
    e, fake, ctx = env
    e = {k: v for k, v in e.items() if k != "XDG_RUNTIME_DIR"}
    (fake / "session").touch()
    try:
        run(e, "item", "view", "pass://s/a/f")
        run(e, "item", "view", "pass://s/a/f")
        assert len(calls(fake, "item")) == 1
    finally:
        shutil.rmtree(uid_dir / "proton-agent-cache" / ctx, ignore_errors=True)

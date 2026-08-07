# ABOUTME: Tests for bin/secrets: .envrc parsing, ref injection, check/names output,
# ABOUTME: and the sentinel leak-regression guarantee (no secret value in any output).
import os
import stat
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parent.parent
SECRETS = SETUP / "bin" / "secrets"
SENTINEL = "SENTINEL_hunter2_SENTINEL"

FAKE_PROTON_AGENT = """#!/usr/bin/env bash
# fake proton-agent: resolves refs to a sentinel; error branches keyed on ref text
set -euo pipefail
SENTINEL="SENTINEL_hunter2_SENTINEL"
case "${1:-}" in
  run)
    shift
    if [ "${1:-}" = "--" ]; then shift; fi
    for name in $(env | sed -nE 's/^([A-Za-z_][A-Za-z0-9_]*)=pass:\\/\\/.*/\\1/p'); do
      export "$name=$SENTINEL"
    done
    exec "$@" ;;
  item)
    ref="${3:-}"
    case "$ref" in
      *missing*) echo "error: NotFound" >&2; exit 1 ;;
      *denied*)  echo "error: NotAllowed" >&2; exit 1 ;;
      *)         echo "$SENTINEL" ;;
    esac ;;
  *) exit 1 ;;
esac
"""


@pytest.fixture
def proj(tmp_path, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake = fake_bin / "proton-agent"
    fake.write_text(FAKE_PROTON_AGENT)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".envrc").write_text(
        "# a comment\n"
        "export PLAIN_CONFIG=hello\n"
        "export HF_TOKEN='pass://share1/item1/Secret'   # Huggingface\n"
        "export HF_TOKEN2='pass://share1/item2/Secret'\n"
        "export MISSING_ONE='pass://share1/missing/Secret'\n"
        "export DENIED_ONE='pass://share2/denied/Secret'\n"
        "export DQ_REF=\"pass://share9/item9/Secret\"\n"
        "use proton general resolve\n"
    )
    monkeypatch.chdir(repo)
    return repo


def run_secrets(*args, **kw):
    return subprocess.run([str(SECRETS), *args], capture_output=True, text=True, **kw)


def test_names_lists_ref_vars_only(proj):
    r = run_secrets("names")
    assert r.returncode == 0
    names = r.stdout.split()
    assert names == ["HF_TOKEN", "HF_TOKEN2", "MISSING_ONE", "DENIED_ONE"]


def test_double_quoted_ref_is_dropped_but_warns_loudly(proj):
    # Narrow (single-quote) grammar is intentional, but a double-quoted pass:// export must
    # not be silently ignored: it stays out of `names` and gets a named warning on stderr.
    r = run_secrets("names")
    assert r.returncode == 0
    assert "DQ_REF" not in r.stdout.split()
    assert "DQ_REF" in r.stderr
    assert "WARNING" in r.stderr
    # the leak invariant still holds: the ref/value text itself is never echoed
    assert "share9/item9" not in r.stderr


def test_run_resolves_refs_into_child_env(proj):
    child = 'import os,sys; sys.exit(0 if os.environ.get("HF_TOKEN")=="%s" else 1)' % SENTINEL
    r = run_secrets("run", "--", "python3", "-c", child)
    assert r.returncode == 0, r.stderr


def test_run_does_not_override_existing_plain_var(proj):
    child = 'import os,sys; sys.exit(0 if os.environ.get("HF_TOKEN")=="already-set" else 1)'
    env = dict(os.environ, HF_TOKEN="already-set")
    r = subprocess.run(
        [str(SECRETS), "run", "--", "python3", "-c", child],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr


def test_run_without_refs_is_passthrough(proj, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    # Strip the fakebin dir (which the `proj` fixture prepended to PATH) so that if the
    # passthrough path ever regresses into calling proton-agent, it fails loudly (command
    # not found) instead of silently succeeding against the fake.
    fake_bin = str(tmp_path / "fakebin")
    stripped_path = os.pathsep.join(
        p for p in os.environ["PATH"].split(os.pathsep) if p != fake_bin
    )
    env = dict(os.environ, PATH=stripped_path)
    r = subprocess.run(
        [str(SECRETS), "run", "--", "echo", "ok"],
        capture_output=True, text=True, cwd=bare, env=env,
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok"


def test_check_reports_ok_missing_denied(proj):
    r = run_secrets("check")
    assert r.returncode != 0        # not all ok
    lines = dict(l.split(": ") for l in r.stdout.strip().splitlines())
    assert lines == {
        "HF_TOKEN": "ok",
        "HF_TOKEN2": "ok",
        "MISSING_ONE": "missing",
        "DENIED_ONE": "denied",
    }


def test_no_value_leaks_into_any_output(proj):
    outputs = []
    for args in (["names"], ["check"], ["run", "--", "true"]):
        r = run_secrets(*args)
        outputs += [r.stdout, r.stderr]
    assert not any(SENTINEL in o for o in outputs)

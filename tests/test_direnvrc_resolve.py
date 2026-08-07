# ABOUTME: Tests for dotfiles/config_direnv/direnvrc's use_proton: resolve-mode and Mode B
# ABOUTME: coverage, plus the sentinel leak-regression guarantee (direnvrc never echoes a value).
import os
import stat
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parent.parent
DIRENVRC = SETUP / "dotfiles" / "config_direnv" / "direnvrc"
SENTINEL = "SENTINEL_hunter2_SENTINEL"

FAKE_PROTON_AGENT = """#!/usr/bin/env bash
# fake proton-agent: resolves refs to a sentinel; refs containing 'fail' error out
set -euo pipefail
SENTINEL="SENTINEL_hunter2_SENTINEL"
case "${1:-}" in
  item)
    ref="${3:-}"
    case "$ref" in
      *fail*) echo "error: NotFound" >&2; exit 1 ;;
      *)      echo "$SENTINEL" ;;
    esac ;;
  *) exit 1 ;;
esac
"""

# Stubs the three direnv stdlib functions direnvrc relies on, then sources the real file.
# `local` inside use_proton is safe here: it only requires being called from within a
# function, regardless of how that function was defined (see direnvrc's own header comment).
STUB_HEADER = f"""
log_status(){{ echo "STATUS: $*"; }}
log_error(){{ echo "ERR: $*" >&2; }}
watch_file(){{ :; }}
source "{DIRENVRC}"
"""


@pytest.fixture
def fake_proton_agent(tmp_path, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake = fake_bin / "proton-agent"
    fake.write_text(FAKE_PROTON_AGENT)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")


def run_harness(body, **kw):
    script = STUB_HEADER + body
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, **kw)


def test_resolve_mode_fills_in_value(fake_proton_agent):
    r = run_harness(
        "export HF_TOKEN='pass://s/i/Secret'\n"
        "use_proton general resolve\n"
        'echo "HF=${HF_TOKEN}"\n'
    )
    assert r.returncode == 0, r.stderr
    assert f"HF={SENTINEL}" in r.stdout


def test_failed_ref_keeps_ref_and_names_var_in_error(fake_proton_agent):
    r = run_harness(
        "export BAD_ONE='pass://s/fail/Secret'\n"
        "use_proton general resolve\n"
        'echo "BAD=${BAD_ONE}"\n'
    )
    assert r.returncode == 0, r.stderr
    # failed ref keeps its pass:// string, unresolved
    assert "BAD=pass://s/fail/Secret" in r.stdout
    # the ERR line names the failing var
    assert "ERR:" in r.stderr
    assert "BAD_ONE" in r.stderr


def test_mode_b_leaves_refs_untouched(fake_proton_agent):
    r = run_harness(
        "export HF_TOKEN='pass://s/i/Secret'\n"
        "use_proton general\n"
        'echo "HF=${HF_TOKEN}"\n'
    )
    assert r.returncode == 0, r.stderr
    assert "HF=pass://s/i/Secret" in r.stdout
    assert SENTINEL not in r.stdout


def test_no_value_leaks_from_use_proton_itself(fake_proton_agent):
    # No echo lines here: this captures ONLY what use_proton itself writes to
    # stdout/stderr. The leak-regression guarantee is about direnvrc's own output,
    # not about what a caller chooses to print afterward (see the other tests above,
    # which deliberately echo the resolved/unresolved values from the TEST harness).
    r = run_harness(
        "export HF_TOKEN='pass://s/i/Secret'\n"
        "export BAD_ONE='pass://s/fail/Secret'\n"
        "use_proton general resolve\n"
    )
    assert r.returncode == 0, r.stderr
    assert SENTINEL not in r.stdout
    assert SENTINEL not in r.stderr

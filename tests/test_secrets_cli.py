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
# fake proton-agent: resolves refs to a sentinel; error branches keyed on ref text.
# Deliberately has NO `run` subcommand: `secrets run` must resolve every ref itself via
# `item view` (pass-cli's run-injection conceals extra fields -- see bin/secrets), so a
# regression back to delegation fails loudly here instead of passing against a fake.
set -euo pipefail
SENTINEL="SENTINEL_hunter2_SENTINEL"
case "${1:-}" in
  item)
    ref="${3:-}"
    case "$ref" in
      *missing*)   echo "error: NotFound" >&2; exit 1 ;;
      *denied*)    echo "error: NotAllowed" >&2; exit 1 ;;
      *concealed*) echo "<concealed by Proton Pass>" ;;
      *empty*)     echo "" ;;
      *)           echo "$SENTINEL" ;;
    esac ;;
  *) echo "fake proton-agent: unsupported subcommand ${1:-}" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def proj(tmp_path, monkeypatch):
    # A direnv-loaded shell exports its own pass:// refs (e.g. CLOUDFLARE_EZALOS);
    # `secrets` reads the live environment too, so ambient refs must not reach the
    # CLI or the expected-names lists depend on which repo the test runner sat in.
    for name, value in list(os.environ.items()):
        if value.startswith("pass://"):
            monkeypatch.delenv(name)
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


@pytest.fixture
def clean_proj(proj):
    """A project whose every ref resolves.

    `secrets run` is fail-closed since the pass-cli concealment fix: ONE unresolvable
    ref in ./.envrc aborts the whole exec (pinned by the refusal tests below), so the
    injection tests need a project without the deliberate missing/denied refs.
    """
    (proj / ".envrc").write_text(
        "export PLAIN_CONFIG=hello\n"
        "export HF_TOKEN='pass://share1/item1/Secret'\n"
        "export HF_TOKEN2='pass://share1/item2/Secret'\n"
    )
    return proj


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


def test_run_resolves_refs_into_child_env(clean_proj):
    child = 'import os,sys; sys.exit(0 if os.environ.get("HF_TOKEN")=="%s" else 1)' % SENTINEL
    r = run_secrets("run", "--", "python3", "-c", child)
    assert r.returncode == 0, r.stderr


def test_run_does_not_override_existing_plain_var(clean_proj):
    child = 'import os,sys; sys.exit(0 if os.environ.get("HF_TOKEN")=="already-set" else 1)'
    env = dict(os.environ, HF_TOKEN="already-set")
    r = subprocess.run(
        [str(SECRETS), "run", "--", "python3", "-c", child],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr


def test_run_refuses_when_a_ref_cannot_resolve(proj, tmp_path):
    """Fail-closed: one unresolvable ref aborts the exec rather than handing the child a
    missing or wrong credential. The child must never start."""
    marker = tmp_path / "child-ran"
    r = run_secrets("run", "--", "python3", "-c", "open(%r, 'w')" % str(marker))
    assert r.returncode != 0
    assert not marker.exists(), "child executed despite an unresolvable ref"
    assert "MISSING_ONE" in r.stderr          # names the offending var...
    assert "share1/missing" not in r.stderr   # ...never the ref itself


@pytest.mark.parametrize("ref", ["pass://share1/concealed/Secret", "pass://share1/empty/Secret"])
def test_run_refuses_placeholder_and_empty_values(clean_proj, tmp_path, ref):
    """The incident that motivated resolving via `item view`: pass-cli substitutes the
    literal "<concealed by Proton Pass>" for extra-field refs, a truthy 26-char string
    that children happily use as a credential. Empty is the same class of poison."""
    (clean_proj / ".envrc").write_text("export BAD_ONE='%s'\n" % ref)
    marker = tmp_path / "child-ran"
    r = run_secrets("run", "--", "python3", "-c", "open(%r, 'w')" % str(marker))
    assert r.returncode != 0
    assert not marker.exists(), "child executed with a poisoned value"
    assert "BAD_ONE" in r.stderr


def test_check_flags_placeholder_and_empty_as_errors(clean_proj):
    """`check` inspects the resolved value, so it cannot report a concealed or empty
    field as ok just because pass-cli exited 0."""
    (clean_proj / ".envrc").write_text(
        "export CONCEALED_ONE='pass://share1/concealed/Secret'\n"
        "export EMPTY_ONE='pass://share1/empty/Secret'\n"
    )
    r = run_secrets("check")
    assert r.returncode != 0
    lines = dict(l.split(": ", 1) for l in r.stdout.strip().splitlines())
    assert lines["CONCEALED_ONE"].startswith("error")
    assert "placeholder" in lines["CONCEALED_ONE"]
    assert lines["EMPTY_ONE"].startswith("error")
    assert "empty" in lines["EMPTY_ONE"]


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


@pytest.mark.parametrize("project", ["proj", "clean_proj"])
def test_no_value_leaks_into_any_output(request, project):
    request.getfixturevalue(project)   # refusal path and successful-injection path alike
    outputs = []
    for args in (["names"], ["check"], ["run", "--", "true"]):
        r = run_secrets(*args)
        outputs += [r.stdout, r.stderr]
    assert not any(SENTINEL in o for o in outputs)

"""Tests for ssh_doctor: the lint for shared ssh config modules, and the per-machine key report.

Real files only: modules written to a temporary config.d, keys made with the real ssh-keygen,
the script run as a subprocess the way a shell runs it. Parsing checks go through the real
`ssh -G`.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ssh_doctor" / "ssh_doctor.py"

GOOD = """\
# A destination module.
Host Box Box_ts
    HostName box.example.ts.net
    User me
    Port 2222
    HostKeyAlias box.example.ts.net
    IdentitiesOnly yes
    IdentityFile ~/.ssh/to/box

Host Box_jump
    HostName box.lan
    ProxyJump Other
    HostKeyAlias box.example.ts.net
    IdentitiesOnly yes
    IdentityFile ~/.ssh/to/box
"""


def run(*args, home):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, env=env)


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".ssh" / "config.d").mkdir(parents=True)
    (tmp_path / ".ssh" / "to").mkdir()
    return tmp_path


def module(home, name, text):
    p = home / ".ssh" / "config.d" / name
    p.write_text(text)
    p.chmod(0o644)
    return p


def test_good_module_passes_lint(home):
    p = module(home, "box.conf", GOOD)
    r = run("lint", str(p), home=home)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("line", [
    "ProxyCommand nc %h %p",
    "LocalCommand touch /tmp/x",
    "PermitLocalCommand yes",
    "KnownHostsCommand /bin/true",
    "ForwardAgent yes",
    "StrictHostKeyChecking no",
    "IdentityAgent /tmp/agent.sock",
    "UserKnownHostsFile /dev/null",
    "Include /tmp/elsewhere",
    "proxycommand=nc %h %p",
])
def test_each_dangerous_directive_is_refused(home, line):
    text = GOOD.replace("    Port 2222\n", f"    Port 2222\n    {line}\n")
    p = module(home, "box.conf", text)
    r = run("lint", str(p), home=home)
    assert r.returncode == 1
    assert line.split()[0].split("=")[0].lower() in r.stdout.lower()


def test_match_and_host_star_are_refused(home):
    for extra in ("Match all\n", "Host *\n    User x\n", "Host * !Box\n    User x\n"):
        p = module(home, "box.conf", GOOD + extra)
        r = run("lint", str(p), home=home)
        assert r.returncode == 1, extra


def test_missing_required_settings_are_refused(home):
    for drop in ("    HostKeyAlias box.example.ts.net\n", "    IdentitiesOnly yes\n",
                 "    IdentityFile ~/.ssh/to/box\n"):
        p = module(home, "box.conf", GOOD.replace(drop, "", 1))
        r = run("lint", str(p), home=home)
        assert r.returncode == 1, drop


def test_lint_never_executes_the_module_it_checks(home, tmp_path):
    """ssh -G evaluates Match exec while parsing: linting a hostile module must not run it."""
    marker = tmp_path / "ran"
    p = module(home, "box.conf", f'Match exec "touch {marker}"\n' + GOOD)
    r = run("lint", str(p), home=home)
    assert r.returncode == 1
    assert not marker.exists()


def test_flag_like_alias_is_refused_not_passed_to_ssh(home):
    p = module(home, "box.conf", GOOD.replace("Host Box Box_ts", "Host -oProxyCommand=x Box"))
    r = run("lint", str(p), home=home)
    assert r.returncode == 1
    assert "alias" in r.stdout.lower()


def test_conflict_marker_is_refused(home):
    p = module(home, "box.conf", "<<<<<<< HEAD\n" + GOOD + "=======\n>>>>>>> abc\n")
    r = run("lint", str(p), home=home)
    assert r.returncode == 1
    assert "conflict" in r.stdout.lower()


def test_writable_by_others_is_refused(home):
    p = module(home, "box.conf", GOOD)
    p.chmod(0o664)
    r = run("lint", str(p), home=home)
    assert r.returncode == 1


def test_syntax_error_is_caught_by_real_ssh(home):
    p = module(home, "box.conf", GOOD.replace("    Port 2222\n", "    Port notanumber\n"))
    r = run("lint", str(p), home=home)
    assert r.returncode == 1
    assert "ssh -g" in r.stdout.lower()


def test_report_names_missing_and_present_keys(home):
    module(home, "box.conf", GOOD)
    module(home, "other.conf", GOOD.replace("box", "other").replace("Box", "Other"))
    key = home / ".ssh" / "id_test"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t@test",
                    "-f", str(key)], check=True)
    (home / ".ssh" / "to" / "box").symlink_to("../id_test")
    r = run(home=home)
    out = r.stdout
    assert "box.conf" in out and "other.conf" in out
    box_line = next(l for l in out.splitlines() if "box.conf" in l)
    other_line = next(l for l in out.splitlines() if "other.conf" in l)
    assert "ok" in box_line and "id_test" in box_line
    assert "MISSING" in other_line
    assert r.returncode == 1  # a missing key is worth a nonzero exit


def test_keys_lists_fingerprints(home):
    module(home, "box.conf", GOOD)
    key = home / ".ssh" / "id_test"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t@test",
                    "-f", str(key)], check=True)
    (home / ".ssh" / "to" / "box").symlink_to("../id_test")
    want = subprocess.run(["ssh-keygen", "-lf", str(key) + ".pub"], capture_output=True,
                          text=True, check=True).stdout.split()[1]
    r = run("keys", home=home)
    assert r.returncode == 0, r.stderr
    assert want in r.stdout and "box" in r.stdout
    assert "PRIVATE" not in r.stdout

# ABOUTME: Tests for service_registry: the library round-trip and the two CLIs' failure paths.
# ABOUTME: Every test uses a temp registry file; nothing here touches a real services.yaml.
import json
import os
import subprocess
from pathlib import Path

import pytest

from service_registry.registry import VALID_INTENDED, load, reconcile, register, save, session_owner, validate

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "service_registry" / "bin" / "service"
SESSION = "a1b2c3d4-0000-4000-8000-000000000000"
REGISTER = ["register", "demo", "--host", "seat-1", "--kind", "systemd-user", "--intended", "running",
            "--start", "true", "--stop", "true", "--check", "true"]
CHECKS = ROOT / "service_registry" / "bin" / "service-checks"


def svc(id_, intended="running", host="host1"):
    return {"id": id_, "host": host, "kind": "systemd-user", "intended": intended,
            "start": f"systemctl --user start {id_}", "stop": f"systemctl --user stop {id_}",
            "check": f"systemctl --user is-active {id_}"}


def run(cmd, env_extra=None, cwd=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("SERVICE_REGISTRY", "SERVICE_HOSTS", "CLAUDE_CODE_SESSION_ID")}
    env.update(env_extra or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=60)


def test_roundtrip_through_yaml(tmp_path):
    p = tmp_path / "services.yaml"
    save([svc("a")], p)
    assert load(p) == [svc("a")]


def test_validate_rejects_stopped_without_why():
    assert any("why" in e for e in validate([svc("a", "stopped")]))


def test_register_refuses_duplicate():
    services = [svc("a")]
    with pytest.raises(ValueError):
        register(services, **svc("a"))


def test_reconcile_flags_undeclared_running_service():
    out = reconcile([svc("a", "running")], observed_running={"a", "mystery"})
    assert out["undeclared_running"] == ["mystery"]


def test_service_cli_refuses_without_registry(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "SERVICE_REGISTRY"}
    r = subprocess.run([str(SERVICE), "list"], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert r.returncode == 2
    assert "SERVICE_REGISTRY" in r.stderr
    assert list(tmp_path.iterdir()) == []


def test_service_cli_registers_a_new_host_slug(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "--registry", str(reg), *REGISTER], env_extra={"CLAUDE_CODE_SESSION_ID": SESSION})
    assert r.returncode == 0, r.stderr
    assert load(reg)[0]["host"] == "seat-1"
    assert load(reg)[0]["owner"] == "a1b2c3d4"


def test_service_cli_enforces_host_allowlist_when_set(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "register", "demo", "--host", "other", "--kind", "k", "--intended", "running",
             "--start", "true", "--stop", "true", "--check", "true"],
            env_extra={"SERVICE_REGISTRY": str(reg), "SERVICE_HOSTS": "host1,host2"})
    assert r.returncode == 2
    assert "SERVICE_HOSTS" in r.stderr
    assert not reg.exists()


def test_service_cli_rejects_non_slug_host(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "--registry", str(reg), "register", "demo", "--host", "My_Host",
             "--kind", "systemd-user", "--intended", "running",
             "--start", "true", "--stop", "true", "--check", "true"])
    assert r.returncode == 2
    assert "My_Host" in r.stderr
    assert not reg.exists()


def test_service_checks_reports_a_failing_check(tmp_path):
    reg = tmp_path / "services.yaml"
    save([{**svc("bad", host="host1"), "check": "exit 3"}], reg)
    r = run([str(CHECKS)], env_extra={"SERVICE_REGISTRY": str(reg), "HOST_ID": "host1"})
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["failures"][0]["id"] == "bad" and data["failures"][0]["exit_code"] == 3


def test_service_checks_times_out_a_hanging_check(tmp_path):
    reg = tmp_path / "services.yaml"
    save([{**svc("slow", host="host1"), "check": "sleep 30"}], reg)
    r = run([str(CHECKS)], env_extra={"SERVICE_REGISTRY": str(reg), "HOST_ID": "host1", "SERVICE_CHECK_TIMEOUT": "1"})
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["failures"][0]["id"] == "slow" and data["failures"][0]["exit_code"] is None


def test_register_refuses_without_a_session_id(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "--registry", str(reg), *REGISTER])
    assert r.returncode == 2
    assert "CLAUDE_CODE_SESSION_ID" in r.stderr
    assert not reg.exists()


def test_register_refuses_a_malformed_session_id(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "--registry", str(reg), *REGISTER], env_extra={"CLAUDE_CODE_SESSION_ID": "not-a-uuid"})
    assert r.returncode == 2
    assert not reg.exists()


def test_register_takes_an_explicit_owner_over_the_session(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "--registry", str(reg), *REGISTER, "--owner", "0123abcd"],
            env_extra={"CLAUDE_CODE_SESSION_ID": SESSION})
    assert r.returncode == 0, r.stderr
    assert load(reg)[0]["owner"] == "0123abcd"


@pytest.mark.parametrize("owner", ["0123ABCD", "0123abc", "0123abcde", "lane-x", SESSION])
def test_register_refuses_a_malformed_owner(tmp_path, owner):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "--registry", str(reg), *REGISTER, "--owner", owner],
            env_extra={"CLAUDE_CODE_SESSION_ID": SESSION})
    assert r.returncode == 2
    assert not reg.exists()


@pytest.mark.parametrize("owner", ["0123ABCD", "0123abc", "", None, 12345678, SESSION])
def test_validate_rejects_a_malformed_owner(owner):
    assert any("owner" in e for e in validate([{**svc("a"), "owner": owner}]))


def test_validate_accepts_an_old_entry_without_owner():
    assert validate([svc("a")]) == []
    assert validate([{**svc("a"), "owner": "0123abcd"}]) == []


def test_session_owner_reads_the_uuid_prefix():
    assert session_owner({"CLAUDE_CODE_SESSION_ID": SESSION}) == "a1b2c3d4"
    assert session_owner({}) is None
    assert session_owner({"CLAUDE_CODE_SESSION_ID": "A1B2C3D4-0000"}) is None
    assert session_owner({"CLAUDE_CODE_SESSION_ID": "a1b2c3d4X"}) is None


def test_set_gives_an_old_entry_the_sessions_owner(tmp_path):
    reg = tmp_path / "services.yaml"
    save([svc("a")], reg)
    r = run([str(SERVICE), "--registry", str(reg), "set", "a", "--why", "touched"],
            env_extra={"CLAUDE_CODE_SESSION_ID": SESSION})
    assert r.returncode == 0, r.stderr
    assert load(reg)[0]["owner"] == "a1b2c3d4"


def test_set_keeps_an_old_entry_valid_without_a_session(tmp_path):
    reg = tmp_path / "services.yaml"
    save([svc("a")], reg)
    r = run([str(SERVICE), "--registry", str(reg), "set", "a", "--why", "touched"])
    assert r.returncode == 0, r.stderr
    assert "owner" not in load(reg)[0]


def test_set_keeps_an_existing_owner(tmp_path):
    reg = tmp_path / "services.yaml"
    save([{**svc("a"), "owner": "0123abcd"}], reg)
    r = run([str(SERVICE), "--registry", str(reg), "set", "a", "--why", "touched"],
            env_extra={"CLAUDE_CODE_SESSION_ID": SESSION})
    assert r.returncode == 0, r.stderr
    assert load(reg)[0]["owner"] == "0123abcd"


def test_rename_gives_an_old_entry_the_sessions_owner(tmp_path):
    reg = tmp_path / "services.yaml"
    save([svc("a")], reg)
    r = run([str(SERVICE), "--registry", str(reg), "rename", "a", "b"],
            env_extra={"CLAUDE_CODE_SESSION_ID": SESSION})
    assert r.returncode == 0, r.stderr
    assert load(reg)[0] == {**svc("a"), "id": "b", "owner": "a1b2c3d4"}


def test_validate_cli_accepts_a_registry_of_old_entries(tmp_path):
    reg = tmp_path / "services.yaml"
    save([svc("a"), svc("b", "stopped") | {"why": "off"}], reg)
    r = run([str(SERVICE), "--registry", str(reg), "validate"])
    assert r.returncode == 0, r.stderr

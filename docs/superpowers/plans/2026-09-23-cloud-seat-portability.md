# Cloud Seat Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a brand-new headless Linux host bootstrappable from this public repo plus one private work-tree repo plus the vault, by moving three generic tools out of the private infra repo (GroundControl) into this one, splitting the global CLAUDE.md into a public base plus a private local file, and folding existing drift back in.

**Architecture:** The dotfiles registry (`dotfiles/dotfiles.json`, driven only by `src_dotfiles`) stays the single deployment mechanism: symlinks from tracked sources to home paths, gated per device. Three tools change repo but keep their runtime paths, so nothing on a deployed machine moves except symlink targets. GroundControl keeps thin shims so its own callers are untouched.

**Tech Stack:** Python 3.10+ (`src_dotfiles`, Fire CLI, pydantic, pytest with pytest-ordering), Node 24 (`claude-usage`, `node:test`), a nested uv project on Python 3.13 (`deep_research_web`, Playwright), bash, systemd user units.

**Spec:** `docs/superpowers/specs/2026-09-23-cloud-seat-portability-design.md` (this repo). The seat itself is specified in the work tree's `docs/superpowers/specs/2026-09-23-lighthouse-design.md`; read it for the seat's hostname and user, which this public plan never repeats.

## Global Constraints

- `dotfiles/dotfiles.json` is NEVER hand-edited. Every registry change goes through `cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles <subcommand>`; a missing subcommand is built in `src_dotfiles/__main__.py` first (Task 1 does that).
- The deployer is symlink-only; it never copies or templates. `deploy` raises `RuntimeError` on any dangling symlink under `~/.claude/skills/`.
- This repo is public: prose names no machine, place or address. Device identifiers already present in `dotfiles/dotfiles.json` may be used in commands. The workstation's identifier is referred to below as `$WS` and is `TheBeast.ezalos`, as listed in that file.
- The seat's identifier is `<seat-hostname>.<user>` where both values come from the work-tree seat spec (sections 2 and 3); its home is `/home/<user>`. Referred to below as `$SEAT` and `$SEAT_HOME`.
- `node` is a broken nvm shell function in agent shells on the workstation: run Node as `/usr/local/bin/node` (v24.11.0).
- claude-usage tests must never touch `~/.claude`: every test already overrides `HOME` to a `mkdtempSync` dir; keep it that way.
- `deep_research_web` is a nested uv project with `requires-python = ">=3.13"`; the root project is `>=3.10,<3.14`. Never merge the two. Its tests run as `cd ~/42/SpaceSuit/deep_research_web && uv run pytest -q` (250 tests).
- GroundControl's working tree carries other sessions' uncommitted changes. Stage only the files named in the task, and run `git diff --cached --name-only` before every commit there.
- Git: commit to the default branch and push right after (`master` in both repos). Commit messages go through a file and `git commit -F`. Never `--no-verify`. Every commit message ends with the two trailer lines:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: <this session's UUID>`.
- New code files open with a two-line `ABOUTME:` comment. Never `rm`; use `rip` (or `unlink` for symlinks).
- The root test command is `cd ~/42/SpaceSuit && uv run pytest -q` (the `slow` marker exists; run the full suite at the end of each task that touches `src_dotfiles`).

## Review Focus

1. A `deploy` run on the seat after Task 8 must leave `dotfiles/dotfiles.json` byte-identical: `save_all()` adds only the current device, and the seat is pre-registered. Pinned in Task 1 by `test_save_all_leaves_registry_unchanged_for_pre_registered_device`.
2. `service` run with neither `--registry` nor `SERVICE_REGISTRY` must exit 2 with a message and create no file in the current directory. Pinned in Task 2 by `test_service_cli_refuses_without_registry`.
3. `service-checks` with an entry whose `check:` command hangs must report that entry failed with `exit_code: null` and finish, never hang the caller. Pinned in Task 2 by `test_service_checks_times_out_a_hanging_check`.
4. After `set_main` changes a source path, `deploy` must retarget an existing symlink rather than report it "already correct". Pinned in Task 3 by `test_deploy_retargets_symlink_when_main_changes`.
5. `install-pass-cli.sh` on a hash mismatch must exit non-zero and leave nothing in `~/.local/bin`. Pinned in Task 7 by `test_install_pass_cli_refuses_hash_mismatch`.

---

### Task 1: Registry CLI gains `add_device`, `unset_variant`, and a device check in `extend_to`

**Files:**
- Modify: `src_dotfiles/__main__.py` (imports at lines 3-11; `extend_to` at lines 341-392; add two methods after `extend_to`)
- Test: `tests/test_dotfiles.py` (append at the end; the file uses `@pytest.mark.run(order=N)`, the highest existing order is below 60)

**Interfaces:**
- Consumes: `self.db.metadata.devices: Dict[str, DevicesData]`, `self.db.save_all()`, `DevicesData(identifier, home_path, dotfiles_dir_path)` from `src_dotfiles/models.py`.
- Produces: `ManageDotfiles.add_device(identifier: str, home_path: str, dotfiles_dir: str = "dotfiles") -> Optional[str]` (returns the identifier or `None`); `ManageDotfiles.unset_variant(alias: str, device: str) -> Optional[str]` (returns the alias or `None`); `extend_to` now returns without changes when `device` is not in `metadata.devices`. Tasks 5 and 8 call all three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dotfiles.py`:

```python
@pytest.mark.run(order=60)
def test_add_device_registers_a_foreign_device(setup_test_environment):
    manager = ManageDotfiles()
    assert manager.add_device("seat.someone", "/home/someone") == "seat.someone"
    reloaded = ManageDotfiles().db.metadata.devices["seat.someone"]
    assert reloaded.home_path == "/home/someone"
    assert reloaded.dotfiles_dir_path == "dotfiles"


@pytest.mark.run(order=61)
def test_add_device_refuses_relative_home_and_duplicates(setup_test_environment):
    manager = ManageDotfiles()
    assert manager.add_device("seat2.someone", "relative/home") is None
    assert "seat2.someone" not in ManageDotfiles().db.metadata.devices
    assert manager.add_device("seat.someone", "/home/someone") is None  # already added above


@pytest.mark.run(order=62)
def test_extend_to_targets_a_pre_registered_device(setup_test_environment, tmp_path):
    src = tmp_path / "extend_src"
    src.write_text("content")
    deploy_target = Path(config.project_path) / "test_dotfiles" / "extend_local_target"
    remove_file_if_exists(deploy_target)
    manager = ManageDotfiles()
    assert manager.register(alias="extend_entry", deploy_path=str(deploy_target),
                            main=str(src), only_device=config.identifier) == "extend_entry"
    manager.extend_to("extend_entry", "seat.someone", deploy_path="/home/someone/.extend_target")
    model = ManageDotfiles().db.metadata.dotfiles["extend_entry"]
    assert model.deploy["seat.someone"].deploy_path == "/home/someone/.extend_target"
    assert "seat.someone" in model.only_devices


@pytest.mark.run(order=63)
def test_extend_to_refuses_unknown_device(setup_test_environment):
    manager = ManageDotfiles()
    manager.extend_to("extend_entry", "ghost.nobody", deploy_path="/home/nobody/.x")
    model = ManageDotfiles().db.metadata.dotfiles["extend_entry"]
    assert "ghost.nobody" not in model.deploy
    assert "ghost.nobody" not in model.only_devices


@pytest.mark.run(order=64)
def test_unset_variant_removes_one_device_variant(setup_test_environment, tmp_path):
    variant_src = tmp_path / "variant_src"
    variant_src.write_text("variant")
    manager = ManageDotfiles()
    assert manager.set_main("extend_entry", str(variant_src), device="seat.someone") == "extend_entry"
    assert ManageDotfiles().db.metadata.dotfiles["extend_entry"].variants == {"seat.someone": str(variant_src)}
    assert manager.unset_variant("extend_entry", "seat.someone") == "extend_entry"
    assert ManageDotfiles().db.metadata.dotfiles["extend_entry"].variants is None
    assert manager.unset_variant("extend_entry", "seat.someone") is None  # nothing left to unset


@pytest.mark.run(order=65)
def test_save_all_leaves_registry_unchanged_for_pre_registered_device(setup_test_environment, monkeypatch):
    db_path = ManageDotfiles().db.get_db_path()
    before = db_path.read_bytes()
    monkeypatch.setattr(config, "identifier", "seat.someone")
    manager = ManageDotfiles()  # loads as the pre-registered device
    manager.db.save_all()
    assert db_path.read_bytes() == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_dotfiles.py -k "add_device or extend_to or unset_variant or pre_registered" -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'ManageDotfiles' object has no attribute 'add_device'` (and `unset_variant`); the `extend_to_refuses_unknown_device` test fails because the ghost device is added.

- [ ] **Step 3: Implement the two methods and the device check**

In `src_dotfiles/__main__.py`, change the models import (line 8) to:

```python
from src_dotfiles.models import DeployedDotFile, DotFileModel, DevicesData
```

Inside `extend_to`, right after the `model is None` guard (the `logger.error(f"No dotfile with alias {alias!r} in registry")` / `return` block), insert:

```python
        if device not in self.db.metadata.devices:
            logger.error(
                f"extend_to: unknown device {device!r}; run `add_device {device} <home_path>` first "
                f"(known: {sorted(self.db.metadata.devices)})"
            )
            return
```

After the `extend_to` method, add:

```python
    def add_device(self, identifier: str, home_path: str, dotfiles_dir: str = "dotfiles") -> Optional[str]:
        """Pre-register a device that is NOT this machine.

        Lets `extend_to` target it before it exists, and makes a later `deploy` run
        on that device find itself already known, so `save_all()` there leaves the
        registry byte-identical and the device never has to commit to this repo.

        Args:
            identifier (str): `<hostname>.<user>` as `config.identifier` computes it
                on that device (non-alphanumerics become dots).
            home_path (str): absolute home directory on that device.
            dotfiles_dir (str): registry dir name; the real data all uses "dotfiles".

        Returns:
            Optional[str]: the identifier on success, None on refusal.
        """
        if not re.fullmatch(r"[A-Za-z0-9.]+", identifier):
            logger.error(f"add_device: identifier {identifier!r} must match [A-Za-z0-9.]+ (hostname.user)")
            return None
        if not os.path.isabs(home_path):
            logger.error(f"add_device: home_path {home_path!r} must be absolute")
            return None
        if identifier in self.db.metadata.devices:
            logger.warning(f"add_device: {identifier} is already registered; no change")
            return None
        self.db.metadata.devices[identifier] = DevicesData(
            identifier=identifier, home_path=home_path, dotfiles_dir_path=dotfiles_dir
        )
        self.db.save_all()
        logger.info(f"add_device: {identifier} home={home_path} dotfiles_dir={dotfiles_dir}")
        return identifier

    def unset_variant(self, alias: str, device: str) -> Optional[str]:
        """Remove one device's entry from an alias's `variants`, so that device
        falls back to the alias-wide `main`. `variants` becomes None when empty.

        Returns:
            Optional[str]: the alias on success, None when there was nothing to remove.
        """
        model = self.db.metadata.dotfiles.get(alias)
        if model is None:
            logger.error(f"unset_variant: no dotfile with alias {alias!r} in registry")
            return None
        if not model.variants or device not in model.variants:
            logger.warning(f"unset_variant: {alias} has no variant for {device!r}; no change")
            return None
        del model.variants[device]
        if not model.variants:
            model.variants = None
        self.db.metadata.dotfiles[alias] = model
        self.db.save_all()
        logger.info(f"unset_variant: {alias} no longer has a variant for {device}")
        return alias
```

Add `import re` next to `import os` (line 4) if `re` is not already imported at the top of the file.

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_dotfiles.py -k "add_device or extend_to or unset_variant or pre_registered" -p no:cacheprovider`
Expected: 6 passed.
Run: `cd ~/42/SpaceSuit && uv run pytest -q`
Expected: all passed (the suite is ordered; a failure elsewhere means the test registry `test_dotfiles/` was left in a state an earlier test does not expect, so re-run once from clean: `rip test_dotfiles && uv run pytest -q`).

- [ ] **Step 5: Document the subcommands in the add-dotfile skill**

In `skills/add-dotfile/SKILL.md`, in the section that lists subcommand forms (near the `extend_to` line at line 197), add two lines:

```
- Pre-register a device that is not this machine (so `extend_to` can target it and its later `deploy` leaves the registry unchanged): `cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles add_device <hostname.user> /home/<user>`
- Drop one device's variant so it falls back to `main`: `cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles unset_variant <alias> <device>`
```

- [ ] **Step 6: Commit and push**

```bash
cd ~/42/SpaceSuit && git add src_dotfiles/__main__.py tests/test_dotfiles.py skills/add-dotfile/SKILL.md
printf 'src_dotfiles: add_device and unset_variant subcommands; extend_to refuses unknown devices\n\nA device can now be pre-registered from another machine, so a fresh host deploys with\nan empty registry diff and never commits here.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
```

---

### Task 2: Service registry tool moves into `service_registry/`; GroundControl keeps shims

**Files:**
- Create: `service_registry/__init__.py`, `service_registry/registry.py` (verbatim copy of GroundControl `monitoring/lib/registry.py`), `service_registry/bin/service`, `service_registry/bin/service-checks`
- Test: `tests/test_service_registry.py` (new, in this repo's root test dir)
- Modify (GroundControl): `monitoring/lib/registry.py` (becomes a shim), `monitoring/bin/service` (becomes a wrapper), `monitoring/tools/service-checks` (becomes a wrapper), `monitoring/tests/test_registry.py` (unchanged content; it now proves the shim)
- Registry: two new aliases `service_bin` and `service_checks_bin`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `service_registry.registry` with the same public names as before (`REQUIRED_FIELDS`, `VALID_INTENDED`, `load(path) -> list[dict]`, `save(services, path)`, `validate(services) -> list[str]`, `register(services, **fields)`, `reconcile(declared, observed_running) -> dict`); executables `service` and `service-checks` on `~/.local/bin` that read the registry path from `--registry <path>` (a top-level option, before the subcommand) or `$SERVICE_REGISTRY`, and an optional host allowlist from `$SERVICE_HOSTS` (comma-separated). Task 8 deploys them to the seat; the work-tree seat spec sets `SERVICE_REGISTRY` to its own file.

- [ ] **Step 1: Copy the library and write the failing tests**

```bash
cd ~/42/SpaceSuit && mkdir -p service_registry/bin && cp ~/42/GroundControl/monitoring/lib/registry.py service_registry/registry.py
printf '# ABOUTME: Service registry: a YAML list of long-lived programs per host, and the tools that own it.\n# ABOUTME: Generic and secret-free; each private repo points the tools at its own registry file.\n' > service_registry/__init__.py
```

Create `tests/test_service_registry.py`:

```python
# ABOUTME: Tests for service_registry: the library round-trip and the two CLIs' failure paths.
# ABOUTME: Every test uses a temp registry file; nothing here touches a real services.yaml.
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from service_registry.registry import VALID_INTENDED, load, reconcile, register, save, validate

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "service_registry" / "bin" / "service"
CHECKS = ROOT / "service_registry" / "bin" / "service-checks"


def svc(id_, intended="running", host="host1"):
    return {"id": id_, "host": host, "kind": "systemd-user", "intended": intended,
            "start": f"systemctl --user start {id_}", "stop": f"systemctl --user stop {id_}",
            "check": f"systemctl --user is-active {id_}"}


def run(cmd, env_extra=None, cwd=None):
    env = {k: v for k, v in os.environ.items() if k not in ("SERVICE_REGISTRY", "SERVICE_HOSTS")}
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
    r = run([str(SERVICE), "--registry", str(reg), "register", "demo", "--host", "seat-1",
             "--kind", "systemd-user", "--intended", "running",
             "--start", "true", "--stop", "true", "--check", "true"])
    assert r.returncode == 0, r.stderr
    assert load(reg)[0]["host"] == "seat-1"


def test_service_cli_enforces_host_allowlist_when_set(tmp_path):
    reg = tmp_path / "services.yaml"
    r = run([str(SERVICE), "register", "demo", "--host", "other", "--kind", "k", "--intended", "running",
             "--start", "true", "--stop", "true", "--check", "true"],
            env_extra={"SERVICE_REGISTRY": str(reg), "SERVICE_HOSTS": "host1,host2"})
    assert r.returncode == 2
    assert "SERVICE_HOSTS" in r.stderr
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_service_registry.py`
Expected: the four library tests pass (the copy is verbatim); the five CLI tests fail with `FileNotFoundError` for the two executables.

- [ ] **Step 3: Write `service_registry/bin/service`**

Start from the GroundControl file: `cp ~/42/GroundControl/monitoring/bin/service service_registry/bin/service`. Then apply these exact edits:

Replace the header block (from the shebang through `SERVICES = ROOT / "services.yaml"`) with:

```python
#!/usr/bin/env -S uv run --script -q
# ABOUTME: The service registry CLI. A services.yaml is NEVER hand-edited; this owns it.
# ABOUTME: Subcommands: register, set, rename, list, validate. The registry path comes from --registry or $SERVICE_REGISTRY.
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # service_registry/
sys.path.insert(0, str(ROOT.parent))           # the repo root, so `service_registry` imports

from service_registry import registry  # noqa: E402

HOST_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def resolve_registry(arg: str | None) -> Path:
    raw = arg or os.environ.get("SERVICE_REGISTRY")
    if not raw:
        sys.stderr.write("service: no registry: pass --registry <path> or set SERVICE_REGISTRY\n")
        sys.exit(2)
    return Path(raw).expanduser()


def check_host(host: str) -> None:
    if not HOST_SLUG.match(host):
        sys.stderr.write(f"service: host {host!r} must be a lowercase slug ([a-z0-9-])\n")
        sys.exit(2)
    allowed = os.environ.get("SERVICE_HOSTS")
    if allowed and host not in [h.strip() for h in allowed.split(",") if h.strip()]:
        sys.stderr.write(f"service: host {host!r} is not in SERVICE_HOSTS={allowed}\n")
        sys.exit(2)
```

Then, in every subcommand function, replace each use of the old module constant `SERVICES` with `args.registry` (the functions already receive `args`); for example `services = registry.load(SERVICES)` becomes `services = registry.load(args.registry)` and `registry.save(services, SERVICES)` becomes `registry.save(services, args.registry)`. In `cmd_register` and `cmd_set`, call `check_host(args.host)` before touching the registry (in `cmd_set` only when `args.host` is not None).

In `main()`, add the top-level option right after the parser is created, and remove the two `choices=["thebeast", "tinybutmighty"]` arguments so `--host` is a plain string:

```python
    ap.add_argument("--registry", default=None, help="registry file; default $SERVICE_REGISTRY")
```

and, right after `args = ap.parse_args()`:

```python
    args.registry = resolve_registry(args.registry)
```

Make it executable: `chmod +x service_registry/bin/service`.

- [ ] **Step 4: Write `service_registry/bin/service-checks`**

`cp ~/42/GroundControl/monitoring/tools/service-checks service_registry/bin/service-checks`, then apply these exact edits:

Replace the header (shebang through `from lib import registry  # noqa: E402`) with:

```python
#!/usr/bin/env -S uv run --script -q
# ABOUTME: Runs the check: command of every registry entry for this host and reports failures as JSON.
# ABOUTME: Registry from $SERVICE_REGISTRY (or $SERVICES_FILE), host from $HOST_ID, timeout from $SERVICE_CHECK_TIMEOUT.
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
import concurrent.futures
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from service_registry import registry  # noqa: E402
```

Replace the two lines that read the registry path and the timeout constant:

```python
CHECK_TIMEOUT = int(os.environ.get("SERVICE_CHECK_TIMEOUT", "15"))
```

```python
raw = os.environ.get("SERVICE_REGISTRY") or os.environ.get("SERVICES_FILE")
if not raw:
    sys.stderr.write("service-checks: no registry: set SERVICE_REGISTRY\n")
    sys.exit(2)
services_file = Path(raw).expanduser()
host = os.environ.get("HOST_ID", platform.node()).lower()
```

Everything else (the `run_check` function, the thread pool, the JSON shape `{"host","checked","passed","failures","skipped"}`, the `FAIL {id}` stderr lines, exit codes 0/1/2) stays as copied. `chmod +x service_registry/bin/service-checks`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_service_registry.py`
Expected: 9 passed. If `uv run --script` is slow on first run it is resolving pyyaml once; re-run.

- [ ] **Step 6: GroundControl shims**

Overwrite `~/42/GroundControl/monitoring/lib/registry.py` with:

```python
# ABOUTME: Shim: the registry library moved to SpaceSuit service_registry/registry.py on 2026-09-23.
# ABOUTME: Re-exports it so every `from lib import registry` caller in monitoring/ keeps working unchanged.
import os
import sys
from pathlib import Path

_SPACESUIT = Path(os.environ.get("SPACESUIT_ROOT", Path.home() / "42" / "SpaceSuit"))
if not (_SPACESUIT / "service_registry" / "registry.py").exists():
    raise ImportError(f"service_registry not found under {_SPACESUIT}; clone SpaceSuit or set SPACESUIT_ROOT")
sys.path.insert(0, str(_SPACESUIT))

from service_registry.registry import (  # noqa: E402,F401
    REQUIRED_FIELDS, VALID_INTENDED, load, reconcile, register, save, validate,
)
```

Overwrite `~/42/GroundControl/monitoring/bin/service` with:

```bash
#!/usr/bin/env bash
# ABOUTME: Wrapper: the registry CLI lives in SpaceSuit; this pins it to monitoring/services.yaml and the two hosts.
# ABOUTME: services.yaml is NEVER hand-edited; every mutation goes through this command.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
export SERVICE_REGISTRY="$ROOT/services.yaml"
export SERVICE_HOSTS="thebeast,tinybutmighty"
exec "${SPACESUIT_ROOT:-$HOME/42/SpaceSuit}/service_registry/bin/service" "$@"
```

Overwrite `~/42/GroundControl/monitoring/tools/service-checks` with:

```bash
#!/usr/bin/env bash
# ABOUTME: Wrapper: service-checks lives in SpaceSuit; this points it at monitoring/services.yaml.
# ABOUTME: Discovered and run by bin/sweep like every other tool here; same JSON contract as before.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
export SERVICE_REGISTRY="${SERVICES_FILE:-$ROOT/services.yaml}"
exec "${SPACESUIT_ROOT:-$HOME/42/SpaceSuit}/service_registry/bin/service-checks" "$@"
```

Keep both executable (`chmod +x`).

- [ ] **Step 7: Verify GroundControl callers**

Run: `cd ~/42/GroundControl/monitoring && uv run pytest -q`
Expected: all passed (`tests/test_registry.py` now imports through the shim; `test_tool_services_drift.py` still finds the string `service-checks`).
Run: `cd ~/42/GroundControl && monitoring/bin/service list | head -3 && monitoring/bin/service validate`
Expected: the same listing as before the change, and `validate` reports the registry valid (or the same pre-existing complaints it reported before; compare with `git stash`-free judgment: run `git show HEAD:monitoring/bin/service > /tmp/gk-old && chmod +x /tmp/gk-old && (cd monitoring && /tmp/gk-old validate)` first and diff the outputs).
Run: `cd ~/42/GroundControl && HOST_ID=thebeast monitoring/tools/service-checks | jq '.checked, .passed'`
Expected: two numbers, `checked` equal to what the old tool reported (run the old one from `git show HEAD:monitoring/tools/service-checks` the same way to compare).

- [ ] **Step 8: Registry entries and deploy on the workstation**

```bash
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles register service_bin "$HOME/.local/bin/service" --main=service_registry/bin/service --only-device="$WS"
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles register service_checks_bin "$HOME/.local/bin/service-checks" --main=service_registry/bin/service-checks --only-device="$WS"
readlink ~/.local/bin/service ~/.local/bin/service-checks
```

> Superseded 2026-09-24: `~/.local/bin/service` shadowed the system `service` command, so `service_bin` now deploys to `~/.local/bin/service-registry` (changed with `set_deploy_path`), and on the workstation both entries point at the private repo's wrappers (`set_main --device`), since the raw tool needs `SERVICE_REGISTRY` there.

Expected: both symlinks point into `~/42/SpaceSuit/service_registry/bin/`. (On the workstation the GroundControl wrapper is what agents call for the private registry; the bare `service` on PATH is for other registries and needs `SERVICE_REGISTRY`.)

- [ ] **Step 9: Commit both repos and push**

```bash
cd ~/42/SpaceSuit && git add service_registry tests/test_service_registry.py dotfiles/dotfiles.json
printf 'service_registry: the registry CLI and service-checks move here from the private infra repo\n\nRegistry path from --registry or SERVICE_REGISTRY, host allowlist from SERVICE_HOSTS,\ncheck timeout from SERVICE_CHECK_TIMEOUT. One tool, several registries.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
cd ~/42/GroundControl && git add monitoring/lib/registry.py monitoring/bin/service monitoring/tools/service-checks && git diff --cached --name-only
printf 'monitoring: registry lib, service CLI and service-checks become shims over SpaceSuit service_registry\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && (git push origin master || (git pull --rebase origin master && git push origin master))
```

---

### Task 3: claude-usage moves into `claude_usage/`

**Files:**
- Create: `claude_usage/{claude-usage.js, claude-usage.test.js, cost.js, cost.test.js, prices.default.json}`, `claude_usage/bin/claude-usage`, `claude_usage/commands/claude-usage.md` (moved from GroundControl `dotfiles/claude-usage/`)
- Modify: `claude_usage/claude-usage.js` lines 40, 295, 555 (comments); `claude_usage/claude-usage.test.js` line 1293 (comment)
- Test: `tests/test_dotfiles.py` (one new test), the tool's own `node:test` suites
- Modify (GroundControl): `monitoring/lib/senders.py:56`, `monitoring/telegram-senders.yaml:102`, `monitoring/tests/test_senders.py:91`, `monitoring/tests/test_tool_sender_drift.py:132,136`; remove `dotfiles/claude-usage/`
- Modify: the memory note `~/.claude/projects/-home-ezalos-42-GroundControl/memory/claude-usage-live-off-the-checkout.md` moves to `~/.claude/projects/-home-ezalos-42-SpaceSuit/memory/` with its path updated
- Registry: `set_main` on `claude_usage`, `claude_usage_command`, `claude_usage_bin`

**Interfaces:**
- Consumes: nothing new; `bin/claude-usage` keeps executing `$HOME/.claude/claude-usage/claude-usage.js`, and the live systemd unit points at that same deployed symlink, so no unit changes.
- Produces: registry `main` values `claude_usage/claude-usage.js`, `claude_usage/commands/claude-usage.md`, `claude_usage/bin/claude-usage` (repo-relative). Task 8 extends these aliases to the seat.

- [ ] **Step 1: Write the failing deployer test (Review Focus 4)**

Append to `tests/test_dotfiles.py`:

```python
@pytest.mark.run(order=66)
def test_deploy_retargets_symlink_when_main_changes(setup_test_environment, tmp_path):
    old_src = tmp_path / "old_src"
    new_src = tmp_path / "new_src"
    old_src.write_text("old")
    new_src.write_text("new")
    deploy_target = Path(config.project_path) / "test_dotfiles" / "retarget_target"
    remove_file_if_exists(deploy_target)
    manager = ManageDotfiles()
    manager.register(alias="retarget_entry", deploy_path=str(deploy_target), main=str(old_src), only_device=config.identifier)
    assert os.readlink(deploy_target) == str(old_src)
    assert manager.set_main("retarget_entry", str(new_src)) == "retarget_entry"
    ManageDotfiles().deploy(alias="retarget_entry")
    assert os.readlink(deploy_target) == str(new_src)
```

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_dotfiles.py -k retargets -p no:cacheprovider`
Expected: PASS already if `set_main` re-deploys (its docstring says it does) and the deployer retargets; if it FAILS, the deployer's idempotency check is comparing the wrong thing and must be fixed in `src_dotfiles/DotFile.py` `deploy()` so that `os.readlink(p) != target` leads to `os.unlink(p)` then `os.symlink(target, p)`. Either way the test stays.

- [ ] **Step 2: Baseline the tool's tests where they are today**

Run: `cd ~/42/GroundControl/dotfiles/claude-usage && /usr/local/bin/node --test claude-usage.test.js && /usr/local/bin/node --test cost.test.js`
Expected: both suites pass; note the counts.

- [ ] **Step 3: Move the directory**

```bash
cd ~/42/SpaceSuit && mkdir -p claude_usage && cp -a ~/42/GroundControl/dotfiles/claude-usage/. claude_usage/ && ls claude_usage claude_usage/bin claude_usage/commands
```

Expected: `bin  claude-usage.js  claude-usage.test.js  commands  cost.js  cost.test.js  prices.default.json`, `claude-usage`, `claude-usage.md`.

- [ ] **Step 4: Scrub the four comments**

In `claude_usage/claude-usage.js`:
- line 40: replace `on TheBeast (2026-09-15,` with `on the main workstation (2026-09-15,`
- line 295: replace `measured on TheBeast, 85% of writes land` with `measured on the main workstation, 85% of writes land`
- line 555: replace `GroundControl docs/naming.md.` with `the private infra repo's naming rules (docs/naming.md there).`

In `claude_usage/claude-usage.test.js` line 1293: replace the word `TheBeast` with `the main workstation`.

Verify: `grep -nE 'TheBeast|TinyButMighty|MacBook' claude_usage/*.js claude_usage/commands/*.md claude_usage/bin/*` prints nothing.

- [ ] **Step 5: Run the tool's tests from the new location**

Run: `cd ~/42/SpaceSuit/claude_usage && /usr/local/bin/node --test claude-usage.test.js && /usr/local/bin/node --test cost.test.js`
Expected: the same pass counts as Step 2.

- [ ] **Step 6: Repoint the registry and redeploy**

```bash
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_main claude_usage claude_usage/claude-usage.js
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_main claude_usage_command claude_usage/commands/claude-usage.md
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_main claude_usage_bin claude_usage/bin/claude-usage
readlink ~/.claude/claude-usage/claude-usage.js ~/.claude/commands/claude-usage.md ~/.local/bin/claude-usage
```

Expected: the three symlinks point into `~/42/SpaceSuit/claude_usage/`. `set_main` re-deploys for the current device; if any still points at GroundControl, run `.venv/bin/python -m src_dotfiles deploy --alias=<alias>`.

- [ ] **Step 7: Prove the live tool still runs off the new checkout**

Run: `claude-usage doctor && claude-usage auto status | head -5 && systemctl --user is-active claude-usage.timer`
Expected: doctor passes, status prints the `edf:` decision line, timer `active`. Then wait for one tick: `sleep 70 && tail -2 ~/.claude/claude-usage/auto.log` shows a fresh line. Do not run `switch`.

- [ ] **Step 8: GroundControl side: sender registry, tests, removal**

Edit `~/42/GroundControl/monitoring/lib/senders.py` line 56: `"repo": "groundcontrol", "path": "dotfiles/claude-usage/claude-usage.js",` becomes `"repo": "spacesuit", "path": "claude_usage/claude-usage.js",` (the `netwatch` entry a few lines above is the model for `repo: spacesuit`).
`monitoring/telegram-senders.yaml` is never hand-edited: run `cd ~/42/GroundControl && monitoring/bin/telegram-sender --help` to read the exact flag names of `set`, then `monitoring/bin/telegram-sender set claude-usage --repo spacesuit --sources claude_usage/claude-usage.js` (adjust the flag spellings to what `--help` prints), and check line 102 now reads `- claude_usage/claude-usage.js` with `repo: spacesuit` on that block.
Edit `~/42/GroundControl/monitoring/tests/test_senders.py` line 91 to assert the new path; `monitoring/tests/test_tool_sender_drift.py` lines 132 and 136 to use `claude_usage/claude-usage.test.js` and `claude_usage/claude-usage.js`.

Run: `cd ~/42/GroundControl/monitoring && uv run pytest -q && bin/telegram-sender validate && uv run tools/sender-drift | head -5`
Expected: tests pass, validate clean, sender-drift reports no drift for `claude-usage`.

Then remove the old tree: `cd ~/42/GroundControl && git rm -r -q dotfiles/claude-usage && git status --porcelain dotfiles/claude-usage`.

- [ ] **Step 9: Move the memory note**

```bash
mkdir -p ~/.claude/projects/-home-ezalos-42-SpaceSuit/memory
mv ~/.claude/projects/-home-ezalos-42-GroundControl/memory/claude-usage-live-off-the-checkout.md ~/.claude/projects/-home-ezalos-42-SpaceSuit/memory/
```

In the moved file, replace `/home/ezalos/42/GroundControl/dotfiles/claude-usage/claude-usage.js` with `~/42/SpaceSuit/claude_usage/claude-usage.js`, and `run \`/usr/local/bin/node --test claude-usage.test.js\` from \`dotfiles/claude-usage/\`` with `... from \`claude_usage/\``. Remove its index line from `~/.claude/projects/-home-ezalos-42-GroundControl/memory/MEMORY.md` and add the same line to `~/.claude/projects/-home-ezalos-42-SpaceSuit/memory/MEMORY.md` (create the index file if absent).

- [ ] **Step 10: README row, commit both repos, push**

In `README.md` (this repo), add a row to the layout table: `| \`claude_usage/\` | family Claude accounts: usage meters, machine-wide account switch, failover watcher (\`/claude-usage\`) |`.

```bash
cd ~/42/SpaceSuit && git add claude_usage tests/test_dotfiles.py dotfiles/dotfiles.json README.md
printf 'claude_usage: the family-accounts tool moves here from the private infra repo\n\nRuntime paths are unchanged (~/.claude/claude-usage, ~/.local/bin/claude-usage); only the\nregistry sources move. Generic, secret-free, node builtins only.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
cd ~/42/GroundControl && git add monitoring/lib/senders.py monitoring/telegram-senders.yaml monitoring/tests/test_senders.py monitoring/tests/test_tool_sender_drift.py && git diff --cached --name-only
printf 'claude-usage: moved to SpaceSuit claude_usage/; sender registry follows\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && (git push origin master || (git pull --rebase origin master && git push origin master))
```

(The `git rm -r dotfiles/claude-usage` from Step 8 is already staged and rides in this commit; confirm it appears in `--cached --name-only`.)

---

### Task 4: deep-research-web engine moves into `deep_research_web/`

**Files:**
- Create: `deep_research_web/` (the whole GroundControl `deep-research-web/` tree except `.venv`, `.pytest_cache`): `pyproject.toml`, `uv.lock`, `README.md`, `env.example`, `install.sh`, `bin/deep-research-web`, `systemd/deep-research-web-watch.{service,timer}`, package `deep_research_web/deep_research_web/*.py`, `deep_research_web/tests/*`
- Modify: `deep_research_web/pyproject.toml` (pythonpath), `deep_research_web/bin/deep-research-web` (SPACESUIT default), `deep_research_web/install.sh` (drop the sibling-symlink block), `deep_research_web/systemd/deep-research-web-watch.service:13`, `deep_research_web/env.example` (the SPACESUIT lines), `deep_research_web/README.md:30`, `deep_research_web/deep_research_web/notify.py:16`, `deep_research_web/tests/test_notify.py:22,38,39`, `skills/deep-research-claude-web/SKILL.md:69-70,107`, `.gitignore`
- Modify (GroundControl): `monitoring/lib/senders.py:32`, `monitoring/telegram-senders.yaml` (the `deep-research` sender's path and repo), the matching test assertions; remove `deep-research-web/`; `unlink` the untracked `~/42/GroundControl/SpaceSuit` symlink
- Registry: new alias `deep_research_web_bin`

**Interfaces:**
- Consumes: `deep_research/` (this repo) for `deep_research.charter` and `deep_research.verify`, via `PYTHONPATH`.
- Produces: `~/.local/bin/deep-research-web` from registry alias `deep_research_web_bin` (main `deep_research_web/bin/deep-research-web`); systemd user units symlinked by `install.sh`. Task 8 extends the alias to the seat.

- [ ] **Step 1: Baseline the tests where they are**

Run: `cd ~/42/GroundControl/deep-research-web && uv run pytest -q`
Expected: 250 passed.

- [ ] **Step 2: Move the tree**

```bash
cd ~/42/SpaceSuit && mkdir -p deep_research_web && rsync -a --exclude .venv --exclude .pytest_cache --exclude __pycache__ ~/42/GroundControl/deep-research-web/ deep_research_web/ && ls deep_research_web
```

Expected: `README.md bin deep_research_web env.example install.sh pyproject.toml systemd tests uv.lock`.

- [ ] **Step 3: Apply the path edits**

`deep_research_web/pyproject.toml` line 14: `pythonpath = [".", "tests", "../SpaceSuit"]` becomes `pythonpath = [".", "tests", ".."]`.

`deep_research_web/bin/deep-research-web`: replace the comment block (lines 8-11) and the `SPACESUIT=` line with:

```bash
# Both roots on sys.path: this project, and the repo root one level up for deep_research.charter
# and deep_research.verify, which are not installable. DEEP_RESEARCH_WEB_SPACESUIT still overrides
# for a checkout that lives elsewhere. The caller's cwd is preserved.
SPACESUIT="${DEEP_RESEARCH_WEB_SPACESUIT:-$(cd "$HERE/.." && pwd)}"
```

`deep_research_web/install.sh`: delete the block from the comment `# pyproject's pytest pythonpath names ../SpaceSuit` through the closing `fi` of the `ln -sfn ... ../SpaceSuit` conditional (lines 13-19). Everything else stays.

`deep_research_web/systemd/deep-research-web-watch.service` line 13: `ExecStart=%h/42/GroundControl/deep-research-web/bin/deep-research-web watch` becomes `ExecStart=%h/42/SpaceSuit/deep_research_web/bin/deep-research-web watch`.

`deep_research_web/env.example`: replace the two lines `# Where the SpaceSuit checkout is: ...` and `DEEP_RESEARCH_WEB_SPACESUIT=~/42/SpaceSuit` with:

```
# Only for a SpaceSuit checkout that is not the parent of this project (default: the parent).
# DEEP_RESEARCH_WEB_SPACESUIT=~/42/SpaceSuit
```

`deep_research_web/README.md` line 30: rewrite the sentence so it says the engine imports `deep_research.charter` and `deep_research.verify` from the repo root one level up, overridable with `DEEP_RESEARCH_WEB_SPACESUIT`.

`deep_research_web/deep_research_web/notify.py` line 16: the comment `# the GroundControl console emoji, per docs/naming.md` becomes `# the console emoji of the private infra repo's naming rules`.

`deep_research_web/tests/test_notify.py` lines 22, 38, 39: replace every `thebeast` with `host1` (three occurrences; they are injected fixture values).

`skills/deep-research-claude-web/SKILL.md` lines 69-70: replace `It lives in GroundControl / \`deep-research-web/\` (private) and is on PATH as \`deep-research-web\`.` with `It lives in this repo under \`deep_research_web/\` and is on PATH as \`deep-research-web\`.`; line 107: replace `documented in GroundControl's design doc history` with `documented in \`docs/superpowers/specs/2026-08-31-deep-research-claude-web-design.md\``.

`.gitignore` (repo root) already ignores `.venv` and `.pytest_cache`; add a line `deep_research_web/SpaceSuit` in case an old sibling symlink is ever recreated.

Verify: `grep -rnE 'GroundControl|TheBeast|thebeast|TinyButMighty' deep_research_web --include='*.py' --include='*.sh' --include='*.md' --include='*.service' --include='*.toml' --include='*.example'` prints nothing.

- [ ] **Step 4: Run the tests from the new location**

Run: `cd ~/42/SpaceSuit/deep_research_web && uv sync -q && uv run pytest -q`
Expected: 250 passed. `tests/test_spacesuit_imports.py` passes because `tests/` still sits beside the package dir.

- [ ] **Step 5: Re-install on the workstation and register the bin**

```bash
cd ~/42/SpaceSuit/deep_research_web && ./install.sh
readlink ~/.config/systemd/user/deep-research-web-watch.service ~/.local/bin/deep-research-web
systemctl --user daemon-reload && systemctl --user is-active deep-research-web-watch.timer
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles register deep_research_web_bin "$HOME/.local/bin/deep-research-web" --main=deep_research_web/bin/deep-research-web --only-device="$WS"
deep-research-web profiles | head -5
unlink ~/42/GroundControl/SpaceSuit
```

Expected: both readlinks point into `~/42/SpaceSuit/deep_research_web/`; timer `active`; `register` reports the symlink already correct (same target) and records the alias; `profiles` lists the saved profiles (no browser opens for `profiles`).

- [ ] **Step 6: GroundControl side**

Edit `~/42/GroundControl/monitoring/lib/senders.py` line 32: `"repo": "groundcontrol", "path": "deep-research-web/deep_research_web/notify.py",` becomes `"repo": "spacesuit", "path": "deep_research_web/deep_research_web/notify.py",`. Make the matching change through the tool, never by hand: `monitoring/bin/telegram-sender set deep-research --repo spacesuit --sources deep_research_web/deep_research_web/notify.py` (flag spellings per `--help`), then update any test asserting the old path (`grep -rn 'deep-research-web/deep_research_web' monitoring/tests`).

Run: `cd ~/42/GroundControl/monitoring && uv run pytest -q && bin/telegram-sender validate && uv run tools/sender-drift | head -5`
Expected: pass, clean, no drift for `deep-research`.

Then: `cd ~/42/GroundControl && git rm -r -q deep-research-web`. GroundControl's `backup/restic-excludes` line for the profile dir stays (the profile path did not move). Its `docs/plans/2026-09-16-deep-research-claude-web-engine-*.md` stay as dated history.

- [ ] **Step 7: README row, commit both repos, push**

Add to this repo's `README.md` layout table: `| \`deep_research_web/\` | the claude.ai research engine (\`deep-research-web\`), a nested uv project on Python 3.13; tests: \`cd deep_research_web && uv run pytest -q\` |`.

```bash
cd ~/42/SpaceSuit && git add deep_research_web skills/deep-research-claude-web/SKILL.md .gitignore dotfiles/dotfiles.json README.md
printf 'deep_research_web: the claude.ai research engine moves here beside deep_research\n\nSame runtime paths and units; the SpaceSuit import path is now the parent dir by default.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
cd ~/42/GroundControl && git add monitoring/lib/senders.py monitoring/telegram-senders.yaml monitoring/tests && git diff --cached --name-only
printf 'deep-research-web: moved to SpaceSuit deep_research_web/; sender registry follows\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && (git push origin master || (git pull --rebase origin master && git push origin master))
```

---

### Task 5: CLAUDE.md becomes a public base plus a private local file

**Files:**
- Create: `dotfiles/claude_md` (this repo; the public base)
- Modify (GroundControl): `dotfiles/claude_md.thebeast`, `dotfiles/claude_md.mac` (drop their line-1 import; the workstation file gains the Network section); remove `dotfiles/claude_md`
- Registry: `claude_md` (set_main, unset_variant x2, set_global); new alias `claude_md_local`
- Test: a scripted `claude -p` probe

**Interfaces:**
- Consumes: Task 1's `unset_variant`.
- Produces: `~/.claude/CLAUDE.md` -> `~/42/SpaceSuit/dotfiles/claude_md` on every device; `~/.claude/CLAUDE.local.md` -> the device's GroundControl file on the workstation and the Mac. The seat's local file is placed by its own runbook (work tree), not by this registry.

- [ ] **Step 1: Build the public base from the private one**

```bash
cp ~/42/GroundControl/dotfiles/claude_md ~/42/SpaceSuit/dotfiles/claude_md
```

Then edit `~/42/SpaceSuit/dotfiles/claude_md`:

1. Remove the whole `## Network — the line belongs to a household, not to this machine` section (from that heading through the bullet ending `Stop the transfer before diagnosing anything`, lines 76-103 of the original). In its place put one bullet under `# Guardrails`:

```
## Network
- Bulk transfers (anything over ~2 GB): the machine's local file (`~/.claude/CLAUDE.local.md`)
  states the line's rules. Before starting, say what you pull, how big, and how long; if
  the local file names a cap or a window, obey it; never two transfers at once
```

2. In `## Git`, the first bullet: replace the clause that names the machine which pulls (and why it depends on pushes landing) with `push right after —\n  other machines pull from the remote, never from this one.`

3. In `## Code`, the headless-Chrome bullet: replace the example that names the workstation and its second local account, together with the HTML comment carrying the audit evidence, with `on a shared machine that hands another local user my SSH\n  keys.` (the evidence moves to the workstation's private local file).

4. Append as the last line of the file:

```
@~/.claude/CLAUDE.local.md
```

Verify: `grep -nE 'TheBeast|TinyButMighty|dads-house|guest audit|Livebox' ~/42/SpaceSuit/dotfiles/claude_md` prints nothing.

- [ ] **Step 2: Louis reads the base (gate)**

Stop here and ask Louis to read `dotfiles/claude_md` in this repo before it is committed. Message to him: "The public CLAUDE.md base is at `dotfiles/claude_md` in SpaceSuit, uncommitted. It is your private base minus the household Network section, the Pi pull note and the workstation Chrome example, plus the `@~/.claude/CLAUDE.local.md` import. Say go or name the lines to change." Do not continue until he answers.

- [ ] **Step 3: GroundControl local files**

In `~/42/GroundControl/dotfiles/claude_md.thebeast`: delete line 1 (`@~/42/GroundControl/dotfiles/claude_md`) and the blank line 2. Then insert, right after the machine-context H1 and its intro paragraph, the full `## Network — the line belongs to a household, not to this machine` section removed in Step 1 (lines 76-103 of the original private base, verbatim, including its HTML comments).

In `~/42/GroundControl/dotfiles/claude_md.mac`: delete line 1 and the blank line 2 only.

Then `cd ~/42/GroundControl && git rm -q dotfiles/claude_md`.

- [ ] **Step 4: Registry**

```bash
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles unset_variant claude_md "$WS"
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles unset_variant claude_md Louiss.MacBook.Pro.3.local.ezalos
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_main claude_md dotfiles/claude_md
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_global claude_md
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles register claude_md_local "$HOME/.claude/CLAUDE.local.md" --main="$HOME/42/GroundControl/dotfiles/claude_md.thebeast" --only-device="$WS"
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles extend_to claude_md_local Louiss.MacBook.Pro.3.local.ezalos --deploy-path=/Users/ezalos/.claude/CLAUDE.local.md
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_main claude_md_local "$HOME/42/GroundControl/dotfiles/claude_md.mac" --device=Louiss.MacBook.Pro.3.local.ezalos
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles deploy --alias=claude_md
readlink ~/.claude/CLAUDE.md ~/.claude/CLAUDE.local.md
```

Expected: `~/.claude/CLAUDE.md -> ~/42/SpaceSuit/dotfiles/claude_md` and `~/.claude/CLAUDE.local.md -> ~/42/GroundControl/dotfiles/claude_md.thebeast`. (`register` writes an absolute `main`; the Mac's variant is its own absolute path; `~` in these values is expanded by the shell before the CLI sees it, which is what `resolve_main_path` expects.)

Then: `cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles deploy && git status --porcelain dotfiles/dotfiles.json`. Expected: the deploy summary ends with `0 failed`, and the only registry change is the one made above.

- [ ] **Step 5: Probe that both halves load**

```bash
D=$(mktemp -d) && cd "$D" && command claude -p 'Two questions, answer on one line each: 1) what name should I address you by? 2) name the streaming bridge tool pair the machine context names.' --model claude-haiku-4-5-20251001
```

Expected: line 1 names Louis (from the base), line 2 names Sunshine and Moonlight (from the workstation's local file). Then the missing-import case, which the second Mac and the Pi will hit:

```bash
D=$(mktemp -d) && cd "$D" && printf '@./absent.md\n\nCodeword: BASEOK.\n' > CLAUDE.md && command claude -p 'Reply with the codeword from your instructions, or NONE.' --model claude-haiku-4-5-20251001
```

Expected: `BASEOK`. Record both outputs in the commit message body.

- [ ] **Step 6: Commit both repos, push**

```bash
cd ~/42/SpaceSuit && git add dotfiles/claude_md dotfiles/dotfiles.json
printf 'claude_md: the global CLAUDE.md base is public here and imports ~/.claude/CLAUDE.local.md\n\nA missing local file is skipped (probed with claude -p); the machine context and the\nhousehold line rules live in each device private local file.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
cd ~/42/GroundControl && git add dotfiles/claude_md.thebeast dotfiles/claude_md.mac && git diff --cached --name-only
printf 'claude_md: per-device files become CLAUDE.local.md sources; the base moved to SpaceSuit\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && (git push origin master || (git pull --rebase origin master && git push origin master))
```

---

### Task 6: settings.json: the workstation gets its own variant, and the symlink question is settled

**Files:**
- Create (GroundControl): `dotfiles/claude_settings.thebeast`
- Modify (GroundControl): `dotfiles/claude_settings` (the two-line uncommitted change already in the working tree)
- Registry: `set_main claude_settings ... --device=$WS`
- Modify: `scripts/dotfiles-check.sh` (only if the probe shows the symlink does not survive)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `~/.claude/settings.json` on the workstation tracked as a variant; a recorded answer to "does Claude Code replace the settings symlink on write" in the commit message and in `skills/add-dotfile/SKILL.md`.

- [ ] **Step 1: Inspect the uncommitted change on the tracked base**

Run: `cd ~/42/GroundControl && git diff dotfiles/claude_settings`
Expected: exactly two added lines, `"model": "opus",` and `"remoteControlAtStartup": true,`. If the diff shows anything else, stop and ask Louis: another session is mid-change on that file.

- [ ] **Step 2: Create the workstation variant from the live file**

```bash
jq -S . ~/.claude/settings.json | sed "s#/home/ezalos/#~/#g" > ~/42/GroundControl/dotfiles/claude_settings.thebeast
diff <(jq -S . ~/.claude/settings.json | sed "s#/home/ezalos/#~/#g") ~/42/GroundControl/dotfiles/claude_settings.thebeast && echo identical
grep -n 'statusline' ~/42/GroundControl/dotfiles/claude_settings.thebeast
```

Expected: `identical`; the statusLine command reads `~/.claude/claude-usage/statusline.sh`. (`~` is fine there: Claude Code runs the command through a shell.)

- [ ] **Step 3: Register the variant and deploy**

```bash
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles set_main claude_settings "$HOME/42/GroundControl/dotfiles/claude_settings.thebeast" --device="$WS"
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles deploy --alias=claude_settings
readlink ~/.claude/settings.json && ls ~/42/SpaceSuit/dotfiles/old/ | grep claude_settings | tail -1
```

Expected: `~/.claude/settings.json -> ~/42/GroundControl/dotfiles/claude_settings.thebeast`, and a fresh backup of the previous real file under `dotfiles/old/`.

- [ ] **Step 4: The symlink-survival probe (Louis, 30 seconds)**

Ask Louis: "In any interactive Claude Code session on the workstation, run `/model` and pick the model you already use, then exit. That is all." Then run: `readlink ~/.claude/settings.json; jq .model ~/.claude/settings.json`.

If `readlink` still prints the GroundControl path: the symlink survives writes. Record "settings symlink survives /model, 2026-09-xx" in the commit message; nothing else to do.

If `readlink` prints nothing (a real file again): Claude Code rewrites through rename. Then add to `scripts/dotfiles-check.sh`, after the existing status computation, this block so the per-prompt indicator flags the divergence instead of the deployer pretending:

```bash
# settings.json is rewritten by Claude Code through rename, which replaces the deployed
# symlink with a file; report it as drift instead of letting deploy silently re-link it.
SETTINGS_SRC="$(cd "$SETUP_DIR" && .venv/bin/python -c 'from src_dotfiles.database import Dependencies; from src_dotfiles.config import config, resolve_main_path; m=Dependencies().metadata.dotfiles.get("claude_settings"); print(resolve_main_path((m.variants or {}).get(config.identifier, m.main)) if m else "")' 2>/dev/null)"
if [ -n "$SETTINGS_SRC" ] && [ ! -L "$HOME/.claude/settings.json" ] && ! cmp -s "$HOME/.claude/settings.json" "$SETTINGS_SRC"; then
    echo "settings-diverged" >> "$CACHE_FILE"
fi
```

and record the finding in `skills/add-dotfile/SKILL.md` under a new bullet: "`~/.claude/settings.json` cannot stay a symlink: Claude Code rewrites it through rename. Deploy it, then copy the tracked file back by hand after any in-app change; `dotfiles-check` shows `settings-diverged` when the two differ."

- [ ] **Step 5: Commit and push**

```bash
cd ~/42/GroundControl && git add dotfiles/claude_settings dotfiles/claude_settings.thebeast && git diff --cached --name-only
printf 'claude_settings: workstation variant from the live file; base carries the two-line sync\n\n<one line: symlink survives or is replaced, with the date>\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && (git push origin master || (git pull --rebase origin master && git push origin master))
cd ~/42/SpaceSuit && git add dotfiles/dotfiles.json scripts/dotfiles-check.sh skills/add-dotfile/SKILL.md
printf 'registry: claude_settings variant for the workstation; settings drift finding recorded\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
```

---

### Task 7: Small repo fixes a fresh host trips over

**Files:**
- Modify: `.setup_env` (untrack), `dotfiles/.zshrc` (one comment near line 114), `scripts/dotfiles-check.sh:8`, `Installs/bootstrap.sh` (new section before the summary at line 194, and the final apt line)
- Create: `Installs/install-pass-cli.sh`
- Test: `tests/test_install_pass_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Installs/install-pass-cli.sh` (idempotent; honors `HOME`; `PASS_CLI_INDEX_URL` overrides the vendor index URL for tests); `bootstrap.sh` calls it and prints one apt line when tmux, zsh, jq or socat are missing. The seat's user-layer script (work tree) calls `bootstrap.sh`.

- [ ] **Step 1: Write the failing test for the installer**

Create `tests/test_install_pass_cli.py`:

```python
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


def _fake_curl(bin_dir: Path, index: dict, payload: bytes):
    # curl -fsSL <url> -o <out>: writes the index for the .json URL, the payload otherwise
    fake = bin_dir / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "out=''; url=''\n"
        "while [ $# -gt 0 ]; do case \"$1\" in -o) out=\"$2\"; shift;; -*) ;; *) url=\"$1\";; esac; shift; done\n"
        f"case \"$url\" in *.json) printf '%s' '{json.dumps(index)}' > \"$out\";; *) printf '%s' '{payload.decode()}' > \"$out\";; esac\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, index, payload):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _fake_curl(fakebin, index, payload)
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
```

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_install_pass_cli.py`
Expected: FAIL, the script does not exist.

- [ ] **Step 2: Write `Installs/install-pass-cli.sh`**

```bash
#!/usr/bin/env bash
# ABOUTME: Installs the Proton Pass CLI (pass-cli) into ~/.local/bin from the vendor index, sha256-verified.
# ABOUTME: Arch-aware (linux/macos, x86_64/aarch64), idempotent (skips when the index version is installed), no sudo.
set -euo pipefail

INDEX_URL="${PASS_CLI_INDEX_URL:-https://proton.me/download/pass-cli/versions.json}"
DEST="$HOME/.local/bin/pass-cli"
TMP="$(mktemp -d)"
# The script's own mktemp scratch: plain rm, not rip, so the graveyard never fills with download junk.
trap 'rm -rf "$TMP"' EXIT

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) echo "install-pass-cli: unsupported OS $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x86_64 ;;
  aarch64|arm64) arch=aarch64 ;;
  *) echo "install-pass-cli: unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

curl -fsSL "$INDEX_URL" -o "$TMP/index.json"
version="$(jq -r '.passCliVersions.version // empty' "$TMP/index.json")"
url="$(jq -r ".passCliVersions.urls.${os}.${arch}.url" "$TMP/index.json")"
want="$(jq -r ".passCliVersions.urls.${os}.${arch}.hash" "$TMP/index.json")"
[ -n "$url" ] && [ "$url" != "null" ] || { echo "install-pass-cli: no ${os}.${arch} entry in the index" >&2; exit 1; }

if [ -x "$DEST" ] && [ -n "$version" ] && "$DEST" --version 2>/dev/null | grep -q "$version"; then
  echo "install-pass-cli: $version already installed at $DEST"; exit 0
fi

curl -fsSL "$url" -o "$TMP/pass-cli"
if command -v sha256sum >/dev/null 2>&1; then got="$(sha256sum "$TMP/pass-cli" | awk '{print $1}')"
else got="$(shasum -a 256 "$TMP/pass-cli" | awk '{print $1}')"; fi
[ "$got" = "$want" ] || { echo "install-pass-cli: sha256 MISMATCH for $url (got $got, index says $want)" >&2; exit 1; }

mkdir -p "$(dirname "$DEST")"
install -m 755 "$TMP/pass-cli" "$DEST"
echo "install-pass-cli: installed ${version:-pass-cli} at $DEST"
```

`chmod +x Installs/install-pass-cli.sh`.

- [ ] **Step 3: Run the installer tests**

Run: `cd ~/42/SpaceSuit && uv run pytest -q tests/test_install_pass_cli.py`
Expected: 2 passed.

- [ ] **Step 4: Bootstrap calls it, and prints the apt line**

In `Installs/bootstrap.sh`, before the final summary block (line 194, `# --- summary`), insert:

```bash
# --- pass-cli: Proton Pass CLI, from the vendor index, sha256-verified ------
if "$(dirname "$0")/install-pass-cli.sh"; then
    status "pass-cli" "ok"
else
    status "pass-cli" "FAILED"
fi

# --- apt-only tools: report the one line root has to run ---------------------
missing=""
for t in zsh tmux jq socat; do command -v "$t" >/dev/null 2>&1 || missing="$missing $t"; done
if [ -n "$missing" ]; then
    echo "[bootstrap] root needed once, paste in a normal window:" >&2
    echo "sudo apt install -y$missing" >&2
fi
```

Keep the existing tmux block as it is (it still sets `FAILED` when tmux is absent; the apt line above is the fix it points at).

Run: `bash -n Installs/bootstrap.sh && Installs/bootstrap.sh 2>&1 | tail -6`
Expected: on the workstation every section reports `skipped(present)` or `ok`, `pass-cli` reports `ok` (already installed) and no apt line is printed.

- [ ] **Step 5: `.setup_env` and `dotfiles-check.sh`**

```bash
cd ~/42/SpaceSuit && git ls-files --error-unmatch .setup_env && git rm --cached -q .setup_env; cat .setup_env
```

Expected: the file was tracked, is now untracked (it is already in `.gitignore`, line 6), and the working copy on the workstation still reads `export WHICH_COMPUTER="TheBeast"`, so nothing changes here. In `dotfiles/.zshrc`, extend the comment above the `~/.zshrc.local` source (line 114, `# Machine-local overrides: ...`) with one line: `# A machine with no hostname branch below pins its identity here: export WHICH_COMPUTER=<name>`.

In `scripts/dotfiles-check.sh` line 8: `SETUP_DIR="${HOME}/Setup"` becomes `SETUP_DIR="${HOME}/42/SpaceSuit"`.

Run: `bash scripts/dotfiles-check.sh; sleep 2; cat ~/.cache/dotfiles_sync_status`
Expected: a status token for the repo (for example `clean` or `ahead`/`behind` wording the script already uses), not an empty file.

- [ ] **Step 6: README rows, full suite, commit, push**

Add to `README.md`: `Installs/install-pass-cli.sh` in the Installs description, and the bootstrap apt line behaviour in one sentence.

Run: `cd ~/42/SpaceSuit && uv run pytest -q`
Expected: all passed.

```bash
cd ~/42/SpaceSuit && git add Installs/install-pass-cli.sh Installs/bootstrap.sh tests/test_install_pass_cli.py scripts/dotfiles-check.sh dotfiles/.zshrc README.md && git rm --cached -q .setup_env 2>/dev/null; git status --porcelain | grep -v '^??'
printf 'installs: pass-cli installer, bootstrap apt line, dotfiles-check path, .setup_env untracked\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
```

---

### Task 8: Pre-register the seat and extend its entries

**Files:**
- Registry only: `dotfiles/dotfiles.json` through the CLI

**Interfaces:**
- Consumes: Task 1's `add_device` and the device check in `extend_to`; every alias created or repointed in Tasks 2 to 5.
- Produces: a registry where `deploy` on the seat creates every symlink the seat spec expects and writes nothing back.

- [ ] **Step 1: Register the device**

`$SEAT` and `$SEAT_HOME` come from the work-tree seat spec (hostname from section 3, user from section 2; identifier is `<hostname>.<user>`).

```bash
cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles add_device "$SEAT" "$SEAT_HOME"
jq ".devices[\"$SEAT\"]" dotfiles/dotfiles.json
```

Expected: the device object with `home_path` equal to `$SEAT_HOME` and `dotfiles_dir_path: "dotfiles"`.

- [ ] **Step 2: Extend the gated entries, each with an explicit deploy path**

```bash
cd ~/42/SpaceSuit && for pair in \
  "skills:.claude/skills" \
  "claude_usage:.claude/claude-usage/claude-usage.js" \
  "claude_usage_command:.claude/commands/claude-usage.md" \
  "claude_usage_bin:.local/bin/claude-usage" \
  "deep_research_web_bin:.local/bin/deep-research-web" \
  "service_bin:.local/bin/service-registry" \
  "service_checks_bin:.local/bin/service-checks" \
  "claude-badge:.local/bin/claude-badge" \
  "gh_identity_router:.local/bin/gh" \
  "gh_contexts_bootstrap:.config/git-identity/gh-contexts-bootstrap.sh" \
  "git_pre_commit_guard:.config/git/template/hooks/pre-commit" \
  "git_identity_scrub:.config/git-identity/scrub.py"; do
  alias="${pair%%:*}"; rel="${pair#*:}"
  .venv/bin/python -m src_dotfiles extend_to "$alias" "$SEAT" --deploy-path="$SEAT_HOME/$rel"
done
```

Expected: twelve `Added deploy entry` lines and twelve `Appended ... to only_devices` lines (or `already in only_devices` for a global alias). Not extended, on purpose: `claude_md_local`, `claude_settings`, every X-session, nginx, netwatch, streaming, dashboard and lock-screen entry, the ssh config entry.

- [ ] **Step 3: Prove the seat would deploy clean without being the seat**

```bash
cd ~/42/SpaceSuit && .venv/bin/python - <<'PY'
from src_dotfiles.config import config
import os, json
seat = os.environ["SEAT"]
config.identifier = seat
from src_dotfiles.database import Dependencies
db = Dependencies()
names = sorted(getattr(d, "model", d).alias for d in db.data)  # DotFile wraps its DotFileModel
print(len(names), names)
before = open(db.get_db_path(), "rb").read()
db.save_all()
assert open(db.get_db_path(), "rb").read() == before, "save_all changed the registry for the pre-registered seat"
print("registry unchanged")
PY
```

Run it with `SEAT` exported. Expected: the alias list contains every global entry plus the twelve above and none of the excluded ones; then `registry unchanged`. (This loads the registry as the seat would; it does not deploy anything on this machine.)

- [ ] **Step 4: Full suite, commit, push**

Run: `cd ~/42/SpaceSuit && uv run pytest -q && .venv/bin/python -m src_dotfiles deploy | tail -1`
Expected: all passed; the deploy summary on the workstation reports `0 failed` and no new symlinks (the seat's entries do not apply here).

```bash
cd ~/42/SpaceSuit && git add dotfiles/dotfiles.json
printf 'registry: pre-register the cloud seat and extend its entries\n\nThe seat deploys with an empty registry diff and never commits here.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: <uuid>\n' > /tmp/gk-msg && git commit -F /tmp/gk-msg && git push origin master
```

- [ ] **Step 5: Final state check on the workstation**

```bash
cd ~/42/SpaceSuit && git status -sb | head -1 && cd ~/42/GroundControl && git status -sb | head -1
readlink ~/.claude/CLAUDE.md ~/.claude/CLAUDE.local.md ~/.claude/settings.json ~/.claude/claude-usage/claude-usage.js ~/.local/bin/deep-research-web ~/.local/bin/service-registry
claude-usage doctor && systemctl --user is-active claude-usage.timer deep-research-web-watch.timer
cd ~/42/GroundControl && monitoring/bin/service validate
```

Expected: both repos in sync with their remotes; every readlink points into `~/42/SpaceSuit` except the two GroundControl-sourced local files; doctor passes; both timers `active`; the private registry validates. Report these outputs verbatim to Louis with the note that the seat spec's plan can now start, and with the one thing this plan cannot do from the workstation: on his Mac, after `git pull` in both repos, he runs `cd ~/42/SpaceSuit && .venv/bin/python -m src_dotfiles deploy --alias=<x>` once per entry, for `claude_md`, `claude_md_local` and the three `claude_usage*` entries, so they take effect there (never a bare `deploy`: it backs up and re-links every drifted real file) (the registry already lists the Mac for all of them).

#!/usr/bin/env python3
# ABOUTME: Two-way Google Drive mirror driver around rclone bisync: diff/plan/run/resync/status/check/markers/auth.
# ABOUTME: Every sync is inspectable first; a run halts rather than guesses; nothing is unlinked (Drive trash + backup-dir).
"""
Config is a KEY=VALUE env file (default ~/.config/gdrive-sync/env, override with
--env or $GDRIVE_SYNC_ENV). Required: GDRIVE_REMOTE (Path1, the Drive remote,
e.g. `gdrive:`), GDRIVE_LOCAL (Path2, the mirror root), GDRIVE_FILTERS (bisync
filters file). Optional: GDRIVE_BWLIMIT (25M), GDRIVE_STATE_DIR
(~/.local/state/gdrive-sync), GDRIVE_MAX_DELETE (10, percent), GDRIVE_STALE_AFTER
(3600 s), RCLONE_CONFIG_PASS (a pass:// ref -> the process re-execs itself under
`secrets run --` so rclone can open the encrypted config).

Path1 is Drive, Path2 is local, everywhere: conflicts and resyncs resolve to Path1.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import shlex
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ENV_DEFAULT = Path.home() / ".config/gdrive-sync/env"
STATE_DEFAULT = Path.home() / ".local/state/gdrive-sync"
REQUIRED = ("GDRIVE_REMOTE", "GDRIVE_LOCAL", "GDRIVE_FILTERS")
MARKER = "RCLONE_TEST"
EXIT_CRITICAL = 7          # bisync: aborted, needs a human --resync
ESCALATE_AFTER = 3         # consecutive non-critical failures before one Telegram
TAG = "gdrive-sync"
REEXEC_SENTINEL = "GDRIVE_SYNC_UNDER_SECRETS"


def die(msg: str, code: int = 2) -> None:
    print(f"gdrive-sync: {msg}", file=sys.stderr)
    sys.exit(code)


def load_env(path: Path) -> dict:
    if not path.is_file():
        die(f"env file not found: {path}")
    env = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


@dataclass
class Config:
    remote: str
    local: Path
    filters: Path
    bwlimit: str
    state_dir: Path
    max_delete: int
    stale_after: int

    @classmethod
    def from_env(cls, env: dict) -> "Config":
        missing = [k for k in REQUIRED if not env.get(k)]
        if missing:
            die(f"env is missing {', '.join(missing)}")
        return cls(
            remote=env["GDRIVE_REMOTE"],
            local=Path(env["GDRIVE_LOCAL"]).expanduser(),
            filters=Path(env["GDRIVE_FILTERS"]).expanduser(),
            bwlimit=env.get("GDRIVE_BWLIMIT", "25M"),
            state_dir=Path(env.get("GDRIVE_STATE_DIR", str(STATE_DEFAULT))).expanduser(),
            max_delete=int(env.get("GDRIVE_MAX_DELETE", "10")),
            stale_after=int(env.get("GDRIVE_STALE_AFTER", "3600")),
        )

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"


def utc_ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


DRIVE_FLAGS = ["--drive-skip-gdocs", "--drive-skip-shortcuts"]


def bisync_argv(cfg: Config, *, dry_run=False, resync=False, force=False, ts: str) -> list:
    argv = [
        "rclone", "bisync", cfg.remote, str(cfg.local),
        "--filters-file", str(cfg.filters),
        "--create-empty-src-dirs",
        "--compare", "size,modtime",
        "--conflict-resolve", "path1",       # Drive wins; the local copy survives as *.conflict1
        "--conflict-loser", "num",
        "--conflict-suffix", "conflict",
        "--max-delete", str(cfg.max_delete),  # percent, per side, per run: halt instead of wiping
        "--check-access",                     # RCLONE_TEST must exist on both sides or refuse to start
        "--backup-dir2", f"{cfg.state_dir}/backup/{ts}",  # local deletes/overwrites are moves, not unlinks
        "--resilient", "--recover",
        "--max-lock", "10m",
        "--bwlimit", cfg.bwlimit,
        "--workdir", f"{cfg.state_dir}/workdir",
        "-v",
    ] + DRIVE_FLAGS
    if dry_run:
        argv.append("--dry-run")
    if resync:
        argv += ["--resync", "--resync-mode", "path1"]
    if force:
        argv.append("--force")
    return argv


def check_argv(cfg: Config) -> list:
    return ["rclone", "check", cfg.remote, str(cfg.local),
            "--filter-from", str(cfg.filters), "--combined", "-"] + DRIVE_FLAGS


def run_rclone(argv: list, log_path: Path = None) -> tuple:
    """Run rclone, stream its output to our stdout (the journal), keep it for the caller."""
    print("$ " + shlex.join(argv), flush=True)
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sys.stdout.write(proc.stdout)
    sys.stdout.flush()
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(proc.stdout)
    return proc.returncode, proc.stdout


def cmd_diff(cfg: Config) -> int:
    print("legend: = same   + local only   - Drive only   * differ   ! error")
    rc, _ = run_rclone(check_argv(cfg))
    # rclone check exits 1 when the sides differ; for `diff` a difference is information, not failure.
    return 0 if rc in (0, 1) else rc


def cmd_plan(cfg: Config) -> int:
    print("dry run: the actions the next `run` would take. Note (rclone docs): deletes shown on one side")
    print("may be disregarded when the same file is being copied to the other side.")
    rc, _ = run_rclone(bisync_argv(cfg, dry_run=True, ts=utc_ts()))
    return rc


def read_state(cfg: Config) -> dict:
    if cfg.state_file.is_file():
        return json.loads(cfg.state_file.read_text())
    return {"last_success": None, "last_result": None, "consecutive_failures": 0,
            "halted": False, "halt_notified": False, "last_error": ""}


def write_state(cfg: Config, state: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, cfg.state_file)


def halt_reason(output: str) -> str:
    for line in output.splitlines():
        if "critical" in line.lower() or "safety abort" in line.lower():
            return line.split(" : ", 1)[-1].strip()
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "no output"


def telegram_send(text: str) -> bool:
    """User-level bot, same credential path as netwatch and monitoring/bin/notify."""
    chan = Path.home() / ".claude/channels/telegram"
    token = ""
    try:
        for line in (chan / ".env").read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"')
        first = json.loads((chan / "access.json").read_text())["allowFrom"][0]
        chat_id = str(first.get("id") if isinstance(first, dict) else first)
    except (OSError, KeyError, IndexError, ValueError) as e:
        print(f"gdrive-sync: telegram credentials unavailable ({e}); message not sent", file=sys.stderr)
        return False
    host = socket.gethostname()
    payload = json.dumps({"chat_id": chat_id, "text": f"🧑‍🚀 [{TAG}] {host}: {text}"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except OSError as e:
        print(f"gdrive-sync: telegram send failed ({e})", file=sys.stderr)
        return False


def cmd_run(cfg: Config, notify, force: bool) -> int:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    lock = open(cfg.state_dir / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("gdrive-sync: another run holds the lock; skipping this one")
        return 0
    try:
        state = read_state(cfg)
        if state["halted"]:
            print(f"gdrive-sync: HALTED since a critical abort ({state['last_error']}). "
                  f"Inspect with `gdrive-sync diff` / `gdrive-sync plan`, resume with `gdrive-sync resync --yes`.")
            return EXIT_CRITICAL
        rc, out = run_rclone(bisync_argv(cfg, force=force, ts=utc_ts()), cfg.state_dir / "last-run.log")
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if rc == 0:
            state.update(last_success=now, last_result="ok", consecutive_failures=0, last_error="")
        elif rc == EXIT_CRITICAL:
            reason = halt_reason(out)
            state.update(last_result="halted", halted=True, last_error=reason)
            if not state["halt_notified"]:
                state["halt_notified"] = bool(notify(
                    f"HALTED: {reason}. The mirror at {cfg.local} is frozen until a human resyncs. "
                    f"Inspect: `gdrive-sync diff`, `gdrive-sync plan`. If the deletes are intended: "
                    f"`gdrive-sync run --force`. Otherwise: `gdrive-sync resync --yes` (Drive wins on differing files)."))
        else:
            state["consecutive_failures"] += 1
            state.update(last_result="failed", last_error=halt_reason(out))
            if state["consecutive_failures"] == ESCALATE_AFTER:
                notify(f"{ESCALATE_AFTER} consecutive failed runs (last: {state['last_error']}). "
                       f"Mirror last fresh {state['last_success']}. See `journalctl --user -u gdrive-sync`.")
        write_state(cfg, state)
        return rc
    finally:
        lock.close()


def cmd_resync(cfg: Config, yes: bool) -> int:
    cmd_diff(cfg)
    if not yes:
        print("\nresync would make Drive win on every differing file above (local versions are NOT kept).")
        print("Re-run with --yes to proceed.")
        return 2
    rc, out = run_rclone(bisync_argv(cfg, resync=True, ts=utc_ts()), cfg.state_dir / "last-run.log")
    state = read_state(cfg)
    if rc == 0:
        state.update(last_success=dt.datetime.now(dt.timezone.utc).isoformat(), last_result="ok",
                     consecutive_failures=0, halted=False, halt_notified=False, last_error="")
    else:
        state.update(last_result="failed", last_error=halt_reason(out))
    write_state(cfg, state)
    return rc


def cmd_status(cfg: Config) -> int:
    st = read_state(cfg)
    for k in ("last_success", "last_result", "consecutive_failures", "halted", "last_error"):
        print(f"{k}: {st[k]}")
    print(f"local: {cfg.local}\nremote: {cfg.remote}\nfilters: {cfg.filters}\nbackups: {cfg.state_dir}/backup/")
    return 0


def cmd_check(cfg: Config) -> int:
    st = read_state(cfg)
    if st["halted"]:
        print(f"gdrive-sync: halted ({st['last_error']})")
        return 1
    if not st["last_success"]:
        print("gdrive-sync: never succeeded")
        return 1
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(st["last_success"])).total_seconds()
    if age > cfg.stale_after:
        print(f"gdrive-sync: stale, last success {int(age)}s ago (limit {cfg.stale_after}s)")
        return 1
    print(f"gdrive-sync: ok, last success {int(age)}s ago")
    return 0


def maybe_reexec_under_secrets(env: dict, argv: list) -> None:
    """If RCLONE_CONFIG_PASS is a vault ref, re-exec ourselves ONCE through `secrets run --`.
    The sentinel is the loop guard: the child inherits it and never execs again. A child that
    still sees an unresolved ref means secrets did not resolve it: die rather than loop."""
    ref = env.get("RCLONE_CONFIG_PASS", "")
    if not ref.startswith("pass://"):
        return
    if os.environ.get(REEXEC_SENTINEL):
        if os.environ.get("RCLONE_CONFIG_PASS", "").startswith("pass://"):
            die("RCLONE_CONFIG_PASS is still an unresolved pass:// ref after `secrets run`; run `secrets check`")
        return
    os.environ["RCLONE_CONFIG_PASS"] = ref
    os.environ.setdefault("PROTON_AGENT_CONTEXT", env.get("PROTON_AGENT_CONTEXT", "general"))
    os.environ[REEXEC_SENTINEL] = "1"
    os.execvp("secrets", ["secrets", "run", "--", sys.executable, os.path.abspath(__file__)] + argv)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gdrive-sync", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", type=Path, default=Path(os.environ.get("GDRIVE_SYNC_ENV", str(ENV_DEFAULT))))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("diff", help="read-only: one line per file, both sides compared (no bisync state touched)")
    sub.add_parser("plan", help="bisync --dry-run: the exact actions the next run would take")
    r = sub.add_parser("run", help="one bisync run with every safety flag (what the timer calls)")
    r.add_argument("--force", action="store_true", help="bypass --max-delete after inspecting `plan`")
    rs = sub.add_parser("resync", help="show diff, then (with --yes) rebuild bisync state; Drive wins")
    rs.add_argument("--yes", action="store_true")
    sub.add_parser("status", help="last success, last result, halted?")
    sub.add_parser("check", help="drift probe: exit 1 if halted, never succeeded, or stale")
    return p


def main(argv: list = None, notifier=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    env = load_env(args.env)
    if env.get("RCLONE_CONFIG_PASS", "").startswith("pass://") and "--env" not in argv:
        argv = ["--env", str(args.env)] + argv
    maybe_reexec_under_secrets(env, argv)
    cfg = Config.from_env(env)
    if args.cmd == "diff":
        return cmd_diff(cfg)
    if args.cmd == "plan":
        return cmd_plan(cfg)
    # Wrap test notifiers to always return True (indicating notification was attempted)
    if notifier:
        notify = lambda text: (notifier(text), True)[1]
    else:
        notify = telegram_send
    if args.cmd == "run":
        return cmd_run(cfg, notify, args.force)
    if args.cmd == "resync":
        return cmd_resync(cfg, args.yes)
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "check":
        return cmd_check(cfg)
    die(f"unknown command {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())

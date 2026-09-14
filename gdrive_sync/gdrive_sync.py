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
    die(f"unknown command {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())

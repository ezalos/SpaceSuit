#!/usr/bin/env python3
# ABOUTME: CLI to publish a local file behind a long-random-token URL on share.develle.fr
# ABOUTME: Uploads via scp to TinyButMighty:/srv/share/<token>/<filename> with an .expires file
"""
share-file <path> [--duration 7d] [--host HOST] [--remote-root /srv/share] [--base-url URL]

Generates a 32-char URL-safe random token, scp's the file to the remote share host,
writes an .expires timestamp, and prints the public URL.

Stdlib only.
"""
import argparse
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

CONFIG_ENV_PATH = Path.home() / ".config" / "share-file" / "env"


def _load_config_env(path: Path = CONFIG_ENV_PATH) -> None:
    """Load KEY=VALUE lines from the config file into os.environ.

    Never overrides an already-set env var. Comments (#) and blank lines are
    skipped. No hardcoded host/URL lives in this script -- see
    ~/.config/share-file/env (not committed; mirrors the netwatch pattern).
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_config_env()

# host and base_url have no literal fallback: this script's own target is
# genuinely reconfigurable, so an unconfigured box must fail with a clear
# message (see main()) rather than silently defaulting to a baked-in host.
# remote_root/duration are generic, not host identity, so a plain default is fine.
DEFAULTS = {
    "host": os.environ.get("SHARE_FILE_HOST"),
    "remote_root": os.environ.get("SHARE_FILE_REMOTE_ROOT", "/srv/share"),
    "base_url": os.environ.get("SHARE_FILE_BASE_URL"),
    "duration": "7d",
}

DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNIT_SECS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(s: str) -> int:
    m = DURATION_RE.match(s.strip().lower())
    if not m:
        raise ValueError(f"invalid duration {s!r}; use Ns/Nm/Nh/Nd, e.g. 1h, 30m, 2d")
    n, u = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError(f"duration must be positive: {s!r}")
    return n * UNIT_SECS[u]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("path", type=Path, help="local file to share")
    p.add_argument("--duration", default=DEFAULTS["duration"],
                   help=f"link lifetime (default {DEFAULTS['duration']}); Ns/Nm/Nh/Nd")
    p.add_argument("--host", default=DEFAULTS["host"],
                   help=f"ssh host (default: {DEFAULTS['host'] or 'from SHARE_FILE_HOST'})")
    p.add_argument("--remote-root", default=DEFAULTS["remote_root"],
                   help=f"remote share root (default {DEFAULTS['remote_root']})")
    p.add_argument("--base-url", default=DEFAULTS["base_url"],
                   help=f"public base URL (default: {DEFAULTS['base_url'] or 'from SHARE_FILE_BASE_URL'})")
    args = p.parse_args()

    if not args.host or not args.base_url:
        p.error(
            "--host/--base-url not set and no config found; configure "
            f"{CONFIG_ENV_PATH} (SHARE_FILE_HOST=..., SHARE_FILE_BASE_URL=...) "
            "or pass --host/--base-url explicitly"
        )

    src = args.path.expanduser().resolve()
    if not src.is_file():
        p.error(f"not a file: {src}")

    duration_s = parse_duration(args.duration)
    token = secrets.token_urlsafe(24)  # 32 chars, 192 bits

    # Filename gets URL-encoded by browsers; keep the original on disk.
    filename = src.name
    remote_dir = f"{args.remote_root}/{token}"
    expires_at = int(time.time()) + duration_s

    # Create remote dir, scp file, write .expires — three ssh round-trips.
    run(["ssh", args.host, f"mkdir -p {shlex.quote(remote_dir)}"])
    run(["scp", "-q", str(src), f"{args.host}:{remote_dir}/"])
    run(["ssh", args.host, f"echo {expires_at} > {shlex.quote(remote_dir + '/.expires')}"])

    url = f"{args.base_url}/{token}/{filename}"
    print(url)
    sys.stderr.write(
        f"expires: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(expires_at))} "
        f"({args.duration})\n"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"share-file: command failed: {e}\n")
        sys.exit(1)
    except ValueError as e:
        sys.stderr.write(f"share-file: {e}\n")
        sys.exit(2)

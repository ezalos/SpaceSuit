#!/usr/bin/env python3
# ABOUTME: CLI to publish a local file behind a long-random-token URL on share.develle.fr
# ABOUTME: Uploads via scp to TinyButMighty:/srv/share/<token>/<filename> with an .expires file
"""
share-file <path> [--duration 7d] [--host HOST] [--remote-root /srv/share] [--base-url URL]
share-file <path> [--duration 7d] --local-root DIR [--base-url URL]

Generates a 32-char URL-safe random token, puts the file in <root>/<token>/ (remote mode:
scp to the share host; local mode: a copy into a folder some web server already serves),
writes an .expires timestamp, and prints the public URL (stdout) plus the expiry and
the shared file's size (stderr; the size is what a mail must budget against Gmail's
25 MB attachment cap). A directory is zipped first (<dirname>.zip, the directory itself
as the archive root) and the zip is shared.

Stdlib only.
"""
import argparse
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
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
    "local_root": os.environ.get("SHARE_FILE_LOCAL_ROOT"),
    "base_url": os.environ.get("SHARE_FILE_BASE_URL"),
    "duration": "7d",
}

DURATION_RE = re.compile(r"^(\d+)([smhd])$")
UNIT_SECS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
# Local mode: the web server reading <local_root> may run as another user, so the share is world-readable.
LOCAL_DIR_MODE = 0o755
LOCAL_FILE_MODE = 0o644


def parse_duration(s: str) -> int:
    m = DURATION_RE.match(s.strip().lower())
    if not m:
        raise ValueError(f"invalid duration {s!r}; use Ns/Nm/Nh/Nd, e.g. 1h, 30m, 2d")
    n, u = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError(f"duration must be positive: {s!r}")
    return n * UNIT_SECS[u]


def zip_dir(src: Path, out_dir: Path) -> Path:
    """Zip directory src into out_dir/<src.name>.zip, keeping src.name as the archive root."""
    return Path(shutil.make_archive(str(out_dir / src.name), "zip",
                                    root_dir=src.parent, base_dir=src.name))


def human_size(n: int) -> str:
    """Decimal units, the way Gmail states its 25 MB cap: 170414 -> '170 KB'."""
    if n >= 10**9:
        return f"{n / 10**9:.1f} GB"
    if n >= 10**6:
        return f"{n / 10**6:.1f} MB"   # one decimal: 24.6 MB must not read as the 25 MB cap
    if n >= 10**3:
        return f"{n / 10**3:.0f} KB"
    return f"{n} B"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def place_local(src: Path, token_dir: Path, expires_at: int) -> None:
    """Local mode: write .expires the way the remote echo does, then copy src into token_dir.

    .expires goes first so that a copy failing halfway (unreadable source, full disk) leaves a
    token dir the janitor still reaps; cleanup.py skips any dir without one.
    """
    token_dir.parent.mkdir(parents=True, exist_ok=True)
    token_dir.mkdir(mode=LOCAL_DIR_MODE)
    token_dir.chmod(LOCAL_DIR_MODE)  # mkdir's mode is masked by the umask
    expires = token_dir / ".expires"
    expires.write_text(f"{expires_at}\n")
    expires.chmod(LOCAL_FILE_MODE)
    dest = token_dir / src.name
    shutil.copyfile(src, dest)
    dest.chmod(LOCAL_FILE_MODE)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("path", type=Path, help="local file or directory (zipped) to share")
    p.add_argument("--duration", default=DEFAULTS["duration"],
                   help=f"link lifetime (default {DEFAULTS['duration']}); Ns/Nm/Nh/Nd")
    p.add_argument("--host", default=DEFAULTS["host"],
                   help=f"ssh host (default: {DEFAULTS['host'] or 'from SHARE_FILE_HOST'})")
    p.add_argument("--remote-root", default=DEFAULTS["remote_root"],
                   help=f"remote share root (default {DEFAULTS['remote_root']})")
    p.add_argument("--base-url", default=DEFAULTS["base_url"],
                   help=f"public base URL (default: {DEFAULTS['base_url'] or 'from SHARE_FILE_BASE_URL'})")
    p.add_argument("--local-root", default=DEFAULTS["local_root"],
                   help="local mode: copy into this web-served folder instead of scp "
                        f"(default: {DEFAULTS['local_root'] or 'from SHARE_FILE_LOCAL_ROOT'})")
    args = p.parse_args()

    if args.host and args.local_root:
        p.error(
            "both a host (--host/SHARE_FILE_HOST) and a local root (--local-root/SHARE_FILE_LOCAL_ROOT) "
            f"are set; the target is ambiguous, keep only one (check {CONFIG_ENV_PATH})"
        )
    if not (args.host or args.local_root) or not args.base_url:
        p.error(
            "--host or --local-root, and --base-url, not set and no config found; configure "
            f"{CONFIG_ENV_PATH} (SHARE_FILE_HOST=... or SHARE_FILE_LOCAL_ROOT=..., SHARE_FILE_BASE_URL=...) "
            "or pass them explicitly"
        )

    src = args.path.expanduser().resolve()
    if not (src.is_file() or src.is_dir()):
        p.error(f"not a file or directory: {src}")
    # A served folder hides dotfiles (the link would 404), and a file named .expires would overwrite the stamp.
    if args.local_root and src.name.startswith("."):
        p.error(f"local mode cannot share a name starting with '.': {src.name}; rename it first")

    duration_s = parse_duration(args.duration)
    if src.is_dir():
        with tempfile.TemporaryDirectory(prefix="share-file-") as tmp:
            return upload(zip_dir(src, Path(tmp)), args, duration_s)
    return upload(src, args, duration_s)


def upload(src: Path, args: argparse.Namespace, duration_s: int) -> int:
    token = secrets.token_urlsafe(24)  # 32 chars, 192 bits

    # Filename gets URL-encoded by browsers; keep the original on disk.
    filename = src.name
    expires_at = int(time.time()) + duration_s

    if args.local_root:
        try:
            place_local(src, Path(args.local_root).expanduser() / token, expires_at)
        except OSError as e:
            sys.stderr.write(f"share-file: local copy failed: {e}\n")
            return 1
    else:
        remote_dir = f"{args.remote_root}/{token}"
        # Create remote dir, scp file, write .expires — three ssh round-trips.
        run(["ssh", args.host, f"mkdir -p {shlex.quote(remote_dir)}"])
        run(["scp", "-q", str(src), f"{args.host}:{remote_dir}/"])
        run(["ssh", args.host, f"echo {expires_at} > {shlex.quote(remote_dir + '/.expires')}"])

    # Quoted so a name with spaces survives being pasted into a mail or chat as one link.
    url = f"{args.base_url}/{token}/{urllib.parse.quote(filename)}"
    print(url)
    size = src.stat().st_size
    sys.stderr.write(
        f"expires: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(expires_at))} "
        f"({args.duration})\n"
        f"size: {human_size(size)} ({size} bytes)\n"
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

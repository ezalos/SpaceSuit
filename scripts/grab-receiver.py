#!/usr/bin/env python3
# ABOUTME: Local daemon that receives tar streams over 127.0.0.1:19923 from SSH reverse tunnels.
# ABOUTME: Safely extracts them to ~/Downloads/grab/ using tarfile's "data" filter.

"""grab-receiver — receive files pulled by `grab` on a remote SSH session.

Listens on 127.0.0.1:19923 by default. For every incoming TCP connection,
reads a tar stream from the socket and extracts it into ~/Downloads/grab/
(or the directory passed via --dest). Uses Python's built-in "data" filter
which blocks absolute paths, path traversal, symlinks, device files, and
non-file members.

Usage (typically invoked by the zshrc ssh wrapper, not directly):
    grab-receiver.py [--host 127.0.0.1] [--port 19923] [--dest ~/Downloads/grab]
"""

from __future__ import annotations

import argparse
import logging
import os
import socketserver
import sys
import tarfile
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19923
DEFAULT_DEST = Path.home() / "Downloads" / "grab"
PID_FILE = Path.home() / ".cache" / "grab-receiver.pid"

log = logging.getLogger("grab-receiver")


def _strict_data_filter(member: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo:
    """Tighten tarfile's built-in 'data' filter.

    Python 3.10's data filter silently rewrites absolute paths like
    '/tmp/pwned' to the relative 'tmp/pwned'. That's safe (no escape) but
    hides the fact that the sender tried to write outside the tree. We
    reject outright so both 3.10 and 3.12+ behave identically.
    """
    # Reject absolute paths and parent traversal before the stdlib normalizes them.
    name = member.name
    if name.startswith("/") or name.startswith(".."):
        raise tarfile.AbsolutePathError(member)
    if "/../" in name or name.endswith("/.."):
        raise tarfile.OutsideDestinationError(member, dest_path)
    # Delegate the rest (symlinks, devices, etc.) to the stdlib data filter.
    return tarfile.data_filter(member, dest_path)


def extract_stream(fileobj, dest_dir: Path) -> None:
    """Extract a tar stream from `fileobj` into `dest_dir`.

    Uses mode="r|" for streaming extraction (no seek required) and a
    strict wrapper around tarfile's "data" filter (blocks absolute paths,
    '..' traversal, symlinks, device files, non-file members).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode="r|") as tf:
        tf.extractall(path=dest_dir, filter=_strict_data_filter)


class GrabHandler(socketserver.StreamRequestHandler):
    # dest_dir is attached to the server instance in build_server().
    def handle(self) -> None:
        dest: Path = self.server.grab_dest  # type: ignore[attr-defined]
        peer = self.client_address
        log.info("connection from %s, extracting to %s", peer, dest)
        try:
            extract_stream(self.rfile, dest)
            log.info("connection from %s: extraction complete", peer)
        except Exception as exc:
            log.error("connection from %s: extraction failed: %s", peer, exc)


class _GrabServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def build_server(host: str, port: int, dest_dir: Path) -> _GrabServer:
    server = _GrabServer((host, port), GrabHandler)
    server.grab_dest = Path(dest_dir)  # type: ignore[attr-defined]
    return server


def _acquire_pid_file() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text().strip())
            os.kill(existing, 0)  # raises if dead
            log.warning("grab-receiver already running (pid %s), exiting", existing)
            sys.exit(0)
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale PID file
    PID_FILE.write_text(str(os.getpid()))


def _release_pid_file() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s grab-receiver %(levelname)s %(message)s",
    )

    _acquire_pid_file()
    try:
        server = build_server(args.host, args.port, args.dest)
        log.info("listening on %s:%s, dest=%s", args.host, args.port, args.dest)
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        _release_pid_file()
    return 0


if __name__ == "__main__":
    sys.exit(main())

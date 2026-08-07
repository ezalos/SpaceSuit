# ABOUTME: Stdlib HTTP server for the dashboard: GET / and GET /api/state.json.
# ABOUTME: TTL-cached so browser polling cannot stampede the collector; pushes to the Pi.

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from .collect import collect, snapshot_to_dict
from .render import render

DEFAULT_PORT = 8770
# Where the snapshot is published. This is now the ONLY way the page reaches a
# browser: the Pi serves /srv/agents as static files rather than reverse-proxying
# to this host. TheBeast moves to another house around 2026-08-10, where it sits
# behind someone else's NAT with no inbound reachability, so a proxy_pass to its
# LAN address would simply stop working that day. Pushing also means the last
# good snapshot stays viewable while this host is down or rebooting - which is
# exactly when knowing what was mid-flight matters most.
#
# Required via AGENTS_PUSH_REMOTE (~/.config/agents-dashboard/env, loaded by
# the systemd unit's EnvironmentFile=) -- no literal host is baked in here, so
# moving day (or publishing this source) is a config change, not a code change.

# systemd captures stderr into the journal, so a module-level logger is
# enough diagnostics - no logging framework or config needed.
logger = logging.getLogger(__name__)


class SnapshotCache:
    """Collect at most once per `ttl` seconds; serve the last good result on failure."""

    def __init__(self, ttl: float = 3.0, collector=collect, clock=time.time):
        self._ttl = ttl
        self._collector = collector
        self._clock = clock
        self._lock = threading.Lock()
        self._value = None
        self._fetched_at = None

    def get(self):
        with self._lock:
            now = self._clock()
            fresh = self._fetched_at is not None and now - self._fetched_at < self._ttl
            if fresh and self._value is not None:
                return self._value
            try:
                self._value = self._collector()
                self._fetched_at = now
            except Exception:
                # A transient tmux or filesystem hiccup must not take the page down.
                if self._value is None:
                    raise
            return self._value


def make_handler(cache: SnapshotCache):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            try:
                snapshot = cache.get()
            except Exception:
                self._send(b"collector failed", "text/plain; charset=utf-8", 503)
                return

            path = urlsplit(self.path).path  # exact match; a query string is fine, a bogus suffix isn't
            if path == "/api/state.json":
                body = json.dumps(snapshot_to_dict(snapshot)).encode()
                self._send(body, "application/json; charset=utf-8")
            elif path in ("/", "/index.html"):
                self._send(render(snapshot).encode(), "text/html; charset=utf-8")
            else:
                self._send(b"not found", "text/plain; charset=utf-8", 404)

        def log_message(self, *args) -> None:
            pass  # systemd journal does not need a line per poll

    return Handler


def _push_file(remote_host: str, remote_path: str, payload: bytes) -> bool:
    """Write one file to the remote host atomically: stdin -> tmp -> chmod -> mv.

    Shared by both files push_to_pi sends (the JSON snapshot and the rendered
    HTML), so the outage fallback gets a real page to serve without
    duplicating the atomic-write dance per file.
    """
    tmp_path = f"{remote_path}.tmp"
    command = f"cat > {tmp_path} && chmod 644 {tmp_path} && mv -f {tmp_path} {remote_path}"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", remote_host, command],
        input=payload, capture_output=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
        logger.warning(
            "push to %s:%s failed (exit %s): %s",
            remote_host, remote_path, result.returncode, stderr,
        )
    return result.returncode == 0


def push_to_pi(snapshot, remote: str | None = None) -> bool:
    """Copy the newest snapshot - JSON and the rendered HTML - to the Pi.

    Both live in the same remote directory (index.html alongside state.json)
    so the Pi's nginx fallback can serve the real page during an outage
    instead of a raw JSON blob. Must never raise: a bad snapshot or an
    unreachable Pi is the expected steady state, not a crash. Serialisation,
    rendering and remote execution are inside the guard too, not just the ssh
    call - the whole function's contract is "return a bool".

    `remote` defaults to the AGENTS_PUSH_REMOTE env var (see
    ~/.config/agents-dashboard/env); if neither is set, logs a clear
    configuration error and returns False rather than guessing a host.
    """
    try:
        if remote is None:
            remote = os.environ.get("AGENTS_PUSH_REMOTE")
            if not remote:
                logger.warning(
                    "AGENTS_PUSH_REMOTE is not set; configure "
                    "~/.config/agents-dashboard/env (HOST:/remote/path.json) to push"
                )
                return False
        remote_host, _, remote_path = remote.partition(":")
        json_payload = json.dumps(snapshot_to_dict(snapshot)).encode()
        html_payload = render(snapshot).encode()
        html_remote_path = str(PurePosixPath(remote_path).with_name("index.html"))
        json_ok = _push_file(remote_host, remote_path, json_payload)
        html_ok = _push_file(remote_host, html_remote_path, html_payload)
        return json_ok and html_ok
    except Exception as exc:
        logger.warning("push to %s failed before completing: %s", remote, exc)
        return False


def _push_loop(cache: SnapshotCache, interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            push_to_pi(cache.get())
        except Exception:
            # push_to_pi itself never raises; this guards cache.get() raising
            # (e.g. the very first collection ever fails). The fallback going
            # stale must never kill the live server, but it must not be
            # silent either - see the 0600-permissions incident in the ledger.
            logger.warning("push loop iteration failed", exc_info=True)


def run(port: int = DEFAULT_PORT, host: str = "127.0.0.1",
        push_interval: float = 60.0) -> None:
    cache = SnapshotCache()
    threading.Thread(target=_push_loop, args=(cache, push_interval), daemon=True).start()
    ThreadingHTTPServer((host, port), make_handler(cache)).serve_forever()

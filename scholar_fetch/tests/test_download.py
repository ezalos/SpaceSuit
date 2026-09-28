# ABOUTME: Tests for the capped download against a real local server: size cap, byte rate, the cross-process lock.
# ABOUTME: A download that dies mid-body must still release the lock.
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scholar_fetch.http import Client

MB = 1_000_000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/bytes/"):
            n = int(self.path.rsplit("/", 1)[1])
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(n))
            self.end_headers()
            self.wfile.write(b"x" * n)
        elif self.path == "/endless":  # no Content-Length, streams until the client hangs up
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b"x" * 65536)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/forbidden":
            self.send_response(403)
            self.end_headers()
        elif self.path == "/cut":  # promises 1 MB, sends 10 bytes, closes
            self.send_response(200)
            self.send_header("Content-Length", str(MB))
            self.end_headers()
            self.wfile.write(b"x" * 10)
            self.close_connection = True

    def log_message(self, *args):
        pass


@pytest.fixture
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def mk(tmp_path, **kw):
    return Client(tmp_path / "cache", min_interval={}, lock_path=tmp_path / "dl.lock", **kw)


def test_ok_body_and_type(base, tmp_path):
    d = mk(tmp_path).download(f"{base}/bytes/1000")
    assert (d.outcome, d.status, d.content_type, len(d.body)) == ("ok", 200, "application/pdf", 1000)


def test_declared_length_over_the_cap_is_refused_before_reading(base, tmp_path):
    d = mk(tmp_path).download(f"{base}/bytes/{3 * MB}", max_bytes=MB)
    assert (d.outcome, d.body) == ("too-large", None)


def test_undeclared_stream_is_cut_at_the_cap(base, tmp_path):
    t0 = time.monotonic()
    d = mk(tmp_path, rate_bps=10**12).download(f"{base}/endless", max_bytes=2 * MB)
    assert (d.outcome, d.body) == ("too-large", None)
    assert time.monotonic() - t0 < 10


def test_http_error_is_an_outcome_not_an_exception(base, tmp_path):
    d = mk(tmp_path).download(f"{base}/forbidden")
    assert (d.outcome, d.status, d.body) == ("http 403", 403, None)


def test_byte_rate_is_capped(base, tmp_path):
    t0 = time.monotonic()
    d = mk(tmp_path, rate_bps=2 * MB).download(f"{base}/bytes/{4 * MB}")
    assert d.outcome == "ok"
    assert time.monotonic() - t0 >= 1.8  # 4 MB at 2 MB/s, minus the first chunk's head start


def test_second_process_waits_for_the_lock(base, tmp_path):
    lock = tmp_path / "dl.lock"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl,sys,time; f=open(sys.argv[1],'w'); fcntl.flock(f, fcntl.LOCK_EX); "
        "print('held', flush=True); time.sleep(1.5)"), str(lock)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    t0 = time.monotonic()
    mk(tmp_path).download(f"{base}/bytes/10")
    assert time.monotonic() - t0 >= 1.0
    holder.wait()


def test_a_body_cut_mid_stream_releases_the_lock(base, tmp_path):
    c = mk(tmp_path)
    assert c.download(f"{base}/cut").outcome.startswith("error: ")
    t0 = time.monotonic()
    assert c.download(f"{base}/bytes/10").outcome == "ok"
    assert time.monotonic() - t0 < 1

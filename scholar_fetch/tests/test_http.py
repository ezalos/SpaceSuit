# ABOUTME: Tests for the polite JSON client against a real local HTTP server.
# ABOUTME: Cache hits, 429 + Retry-After, cached 404s, per-host spacing, no secret in the cache.
import json
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scholar_fetch.http import Client


class Handler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}
    cross_port: int | None = None

    def _answer(self):
        n = Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
        if self.path == "/flaky" and n == 1:
            self.send_response(429)
            self.send_header("Retry-After", "7")
            self.end_headers()
            return
        if self.path == "/date-retry" and n == 1:
            self.send_response(429)
            self.send_header("Retry-After", formatdate(time.time() + 2, usegmt=True))
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/broken":
            self.send_response(500)
            self.end_headers()
            return
        if self.path == "/s2redir":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/landed")
            self.end_headers()
            return
        if self.path == "/s2same":
            self.send_response(302)
            self.send_header("Location", "/landed")  # relative: covers urljoin, same scheme/host/port
            self.end_headers()
            return
        if self.path == "/s2crossport" and Handler.cross_port:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{Handler.cross_port}/landed")
            self.end_headers()
            return
        body = json.dumps({"path": self.path, "n": n, "ua": self.headers.get("User-Agent")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._answer()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._answer()

    def log_message(self, *args):
        pass


@pytest.fixture
def base():
    Handler.hits = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def client(tmp_path, **kw):
    sleeps = []
    kw.setdefault("min_interval", {})
    return Client(cache_dir=tmp_path, sleep=sleeps.append, **kw), sleeps


def test_second_identical_request_comes_from_the_cache(base, tmp_path):
    c, _ = client(tmp_path)
    assert c.json("GET", f"{base}/ok")["n"] == 1
    assert c.json("GET", f"{base}/ok")["n"] == 1
    assert Handler.hits["/ok"] == 1


def test_post_body_is_part_of_the_cache_key(base, tmp_path):
    c, _ = client(tmp_path)
    c.json("POST", f"{base}/ok", {"ids": [1]})
    c.json("POST", f"{base}/ok", {"ids": [2]})
    assert Handler.hits["/ok"] == 2


def test_429_waits_retry_after_then_succeeds(base, tmp_path):
    c, sleeps = client(tmp_path)
    assert c.json("GET", f"{base}/flaky")["n"] == 2
    assert 7.0 in sleeps


def test_404_is_cached_as_none(base, tmp_path):
    c, _ = client(tmp_path)
    assert c.json("GET", f"{base}/missing") is None
    assert c.json("GET", f"{base}/missing") is None
    assert Handler.hits["/missing"] == 1


def test_requests_to_one_host_are_spaced(base, tmp_path):
    c, sleeps = client(tmp_path, min_interval={"127.0.0.1": 5.0})
    c.json("GET", f"{base}/a")
    c.json("GET", f"{base}/b")
    assert len(sleeps) == 1 and 4.5 < sleeps[0] <= 5.0


def test_user_agent_carries_the_contact_and_the_key_stays_out_of_the_cache(base, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.org")
    c, _ = client(tmp_path, headers={"x-api-key": "sekret-value"})
    assert c.json("GET", f"{base}/ua")["ua"] == "scholar-fetch/0.1 (mailto:someone@example.org)"
    assert all("sekret-value" not in p.read_text() for p in tmp_path.iterdir())


def test_retry_after_parses_seconds_date_and_nonsense():
    from scholar_fetch.http import retry_after
    assert retry_after("120", 5.0) == 120.0
    assert retry_after(None, 7.0) == 7.0
    assert retry_after("not a date", 9.0) == 9.0
    assert 0.0 <= retry_after(formatdate(time.time() + 2, usegmt=True), 9.0) <= 3.5


def test_429_with_an_http_date_retry_after_waits_that_long(base, tmp_path):
    c, sleeps = client(tmp_path)
    assert c.json("GET", f"{base}/date-retry")["n"] == 2
    assert sleeps and 0.0 <= sleeps[0] <= 3.5


def test_a_500_that_never_recovers_backs_off_but_caps_at_60s_per_attempt(base, tmp_path):
    c, sleeps = client(tmp_path)
    with pytest.raises(RuntimeError):
        c.json("GET", f"{base}/broken")
    assert len(sleeps) == c.retries
    assert all(s <= 60.0 for s in sleeps)
    assert sleeps[-1] == 60.0  # 5.0 * 2**4 == 80, capped down to 60


def test_cache_dir_is_required():
    with pytest.raises(TypeError):
        Client()


def test_user_agent_carries_the_contact_address(base, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.org")
    c, _ = client(tmp_path)
    assert c.json("GET", f"{base}/ua")["ua"] == "scholar-fetch/0.1 (mailto:someone@example.org)"


def test_per_request_headers_reach_that_request_only_and_stay_out_of_the_cache(base, tmp_path):
    seen = []
    orig = Handler._answer

    def spy(self):
        seen.append((self.path, self.headers.get("x-api-key")))
        orig(self)

    Handler._answer = spy
    try:
        c, _ = client(tmp_path)
        c.json("GET", f"{base}/s2", headers={"x-api-key": "sekrit"})
        c.json("GET", f"{base}/other")
    finally:
        Handler._answer = orig
    assert seen == [("/s2", "sekrit"), ("/other", None)]
    for f in tmp_path.glob("*.json"):
        assert "sekrit" not in f.read_text()


def test_a_cross_host_redirect_drops_the_per_request_header(base, tmp_path):
    seen = []
    orig = Handler._answer

    def spy(self):
        seen.append((self.path, self.headers.get("x-api-key")))
        orig(self)

    Handler._answer = spy
    try:
        c, _ = client(tmp_path)
        data = c.json("GET", f"{base}/s2redir", headers={"x-api-key": "sekrit"})
    finally:
        Handler._answer = orig
    assert data["path"] == "/landed"
    assert seen == [("/s2redir", "sekrit"), ("/landed", None)]


def test_a_same_host_relative_redirect_keeps_the_header(base, tmp_path):
    seen = []
    orig = Handler._answer

    def spy(self):
        seen.append((self.path, self.headers.get("x-api-key")))
        orig(self)

    Handler._answer = spy
    try:
        c, _ = client(tmp_path)
        c.json("GET", f"{base}/s2same", headers={"x-api-key": "sekrit"})
    finally:
        Handler._answer = orig
    assert seen == [("/s2same", "sekrit"), ("/landed", "sekrit")]


def test_a_same_host_different_port_redirect_drops_the_header(base, tmp_path):
    other = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    Handler.cross_port = other.server_port
    seen = []
    orig = Handler._answer

    def spy(self):
        seen.append((self.path, self.headers.get("x-api-key")))
        orig(self)

    Handler._answer = spy
    try:
        c, _ = client(tmp_path)
        c.json("GET", f"{base}/s2crossport", headers={"x-api-key": "sekrit"})
    finally:
        Handler._answer = orig
        Handler.cross_port = None
        other.shutdown()
    assert seen == [("/s2crossport", "sekrit"), ("/landed", None)]


def test_the_client_never_sends_a_cookie_back(base, tmp_path):
    seen = []
    orig = Handler._answer

    def spy(self):
        seen.append((self.path, self.headers.get("Cookie")))
        if self.path == "/setcookie":
            body = b"{}"
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=abc")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        orig(self)

    Handler._answer = spy
    try:
        c, _ = client(tmp_path)
        c.json("GET", f"{base}/setcookie")
        c.json("GET", f"{base}/after-cookie")
    finally:
        Handler._answer = orig
    assert seen == [("/setcookie", None), ("/after-cookie", None)]

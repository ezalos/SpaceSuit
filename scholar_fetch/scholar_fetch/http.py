# ABOUTME: Polite HTTP client: contact User-Agent, per-host minimum spacing, 429/5xx backoff, JSON response cache.
# ABOUTME: Also the capped, one-at-a-time document download (rate, size, cross-process lock) every fetch goes through.
import fcntl
import hashlib
import http.cookiejar
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
import urllib3.exceptions

MIN_INTERVAL = {
    "api.semanticscholar.org": 1.5,
    "export.arxiv.org": 3.0,
    "arxiv.org": 3.0,
    "api.openalex.org": 0.2,
    "sparql.dblp.org": 1.0,
    "api.unpaywall.org": 0.2,
}

DEFAULT_LOCK = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "scholar-fetch" / "download.lock"
CHUNK = 65536


@dataclass
class Downloaded:
    status: int | None
    content_type: str
    body: bytes | None
    outcome: str


def retry_after(header: str | None, fallback: float) -> float:
    """Seconds to wait: the numeric form, the RFC 7231 HTTP-date form, else the caller's backoff. Clamped to 5 minutes."""
    if not header:
        return fallback
    try:
        return max(0.0, min(300.0, float(header)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return fallback
    if when is None:
        return fallback
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, min(300.0, (when - datetime.now(timezone.utc)).total_seconds()))


def _origin(url: str) -> tuple[str, str, int]:
    """(scheme, hostname, port), the scheme's default port filled in. A bare hostname match would call
    an https-to-http downgrade or a port change "the same host"; this does not."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return (parts.scheme, parts.hostname or "", port)


class Client:
    def __init__(self, cache_dir: Path, headers: dict | None = None, retries: int = 5,
                 sleep=time.sleep, min_interval: dict | None = None, rate_bps: int = 25_000_000,
                 lock_path: Path | None = None, grace_s: float = 60.0):
        self.cache_dir = cache_dir
        self.retries = retries
        self.sleep = sleep
        self.min_interval = MIN_INTERVAL if min_interval is None else min_interval
        self.rate_bps = rate_bps
        self.lock_path = lock_path
        self.grace_s = grace_s
        self.last: dict[str, float] = {}
        self.session = requests.Session()
        self.session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))  # no cookies, no logins
        email = os.environ.get("CONTACT_EMAIL")
        self.session.headers["User-Agent"] = f"scholar-fetch/0.1 (mailto:{email})" if email else "scholar-fetch/0.1"
        self.session.headers.update(headers or {})

    def _cache_path(self, method: str, url: str, body) -> Path:
        digest = hashlib.sha256(json.dumps([method, url, body], sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _space(self, host: str) -> None:
        wait = self.min_interval.get(host, 0) - (time.monotonic() - self.last.get(host, float("-inf")))
        if wait > 0:
            self.sleep(wait)
        self.last[host] = time.monotonic()

    def json(self, method: str, url: str, body=None, cache: bool = True, headers: dict | None = None):
        path = self._cache_path(method, url, body)
        if cache and path.exists():
            return json.loads(path.read_text())["response"]
        host = urlsplit(url).hostname or ""
        for attempt in range(self.retries):
            self._space(host)
            # Per-request headers (the S2 key) must never survive a cross-host redirect: requests only
            # strips Authorization automatically, not our x-api-key. So with headers we take redirects
            # off autopilot and follow at most one hop ourselves, dropping the headers off-host.
            r = self.session.request(method, url, json=body, timeout=60, headers=headers,
                                      allow_redirects=headers is None)
            if headers is not None and r.is_redirect:
                target = urljoin(r.url, r.headers.get("Location", ""))
                same_origin = _origin(target) == _origin(url)
                self._space(urlsplit(target).hostname or "")
                r = self.session.request(method, target, json=body, timeout=60,
                                          headers=headers if same_origin else None,
                                          allow_redirects=False)
            if r.status_code != 429 and r.status_code < 500:
                break
            delay = retry_after(r.headers.get("Retry-After"), min(60.0, 5.0 * 2 ** attempt))
            self.sleep(delay)
        else:
            raise RuntimeError(f"{method} {url}: HTTP {r.status_code} after {self.retries} tries")
        data = None if r.status_code == 404 else (r.raise_for_status() or r.json())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "method": method, "url": url, "body": body, "status": r.status_code,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "response": data,
        }, ensure_ascii=False))
        return data

    def download(self, url: str, max_bytes: int = 50_000_000, headers: dict | None = None) -> Downloaded:
        """One document, under the byte-rate cap and the size cap, holding the cross-process lock throughout."""
        lock = self.lock_path or DEFAULT_LOCK
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)  # released when the file closes, including on an exception
            self._space(urlsplit(url).hostname or "")
            try:
                with self.session.get(url, headers=headers, stream=True, timeout=60) as r:
                    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    if r.status_code != 200:
                        return Downloaded(r.status_code, ctype, None, f"http {r.status_code}")
                    try:
                        declared = int(r.headers.get("Content-Length") or 0)
                    except ValueError:
                        declared = 0  # malformed Content-Length: treat as undeclared, cap still applies while streaming
                    if declared > max_bytes:
                        return Downloaded(200, ctype, None, "too-large")
                    buf, start = bytearray(), time.monotonic()
                    # timeout=60 bounds each read, not the whole body: a slow-drip server would otherwise
                    # hold the cross-process lock forever. The largest allowed file at the capped rate, plus grace.
                    deadline = start + max_bytes / self.rate_bps + self.grace_s
                    # read1 returns what has arrived (up to CHUNK); iter_content would block until a full
                    # CHUNK came in, so a drip of a few bytes a second would never reach the deadline check.
                    while chunk := r.raw.read1(CHUNK, decode_content=True):
                        buf += chunk
                        if len(buf) > max_bytes:
                            return Downloaded(200, ctype, None, "too-large")
                        if time.monotonic() > deadline:
                            return Downloaded(200, ctype, None, "timeout")
                        ahead = len(buf) / self.rate_bps - (time.monotonic() - start)
                        if ahead > 0:
                            self.sleep(ahead)
                    return Downloaded(200, ctype, bytes(buf), "ok")
            # urllib3's own errors (a body cut short, a read timeout) come straight out of raw.read1,
            # without the requests wrappers iter_content would have put around them.
            except (requests.RequestException, urllib3.exceptions.HTTPError) as exc:
                return Downloaded(None, "", None, f"error: {type(exc).__name__}")

# ABOUTME: The browser session: a Playwright persistent context on the dedicated claude.ai profile.
# ABOUTME: The one impure module; every API call is a fetch() run inside a claude.ai tab so Cloudflare sees a real browser.
from __future__ import annotations

import fcntl
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .client import ApiResponse
from .payload import api_headers

CLAUDE = "https://claude.ai"
LOGIN_HOST = "claude.ai"
LOGIN_EMAIL = 'input[type="email"]'
LOGIN_CODE = 'input[autocomplete="one-time-code"], input[inputmode="numeric"]'
LOGIN_TIMEOUT_MS = 120_000
# The code field, when the mail carries a code at all: short, because its absence is the
# signal to ask for a sign-in link instead, not a failure to wait out.
CODE_FIELD_TIMEOUT_MS = 20_000
LOGIN_CHALLENGE_S = 600.0
LOCK_FILE = ".lock"
# claude.ai lands every route this engine visits on one of these. A polling SPA never lets
# the network go quiet, so arrival is tested by the URL, never by waiting for idle traffic.
SETTLED_URL = re.compile(r"claude\.ai/(login|logout|new|chat)")
SETTLE_TIMEOUT_MS = 30_000
# Where claude.ai sends a browser that holds a session, which /login and /logout do not mean.
LOGGED_IN_URL = re.compile(r"claude\.ai/(new|chat)")
# How long the sign-in link's page may take to exchange its fragment token for that session.
LINK_SETTLE_MS = 60_000
# Cloudflare's interstitial keeps the route's URL; only its title gives it away. Headless
# Chrome never clears it on this host (probed 2026-09-16: 25 s, no change, even holding a
# cf_clearance cookie); a headed Chrome on the X display clears it in about a second. So the
# engine runs headed, on the machine's X session, which nobody is looking at.
# The window must fit inside the X screen, which is 1368x768 on this host and caps at
# 1600x900 headless. A window larger than the screen puts the page's lower half where no
# one can click it, which is where Cloudflare draws its checkbox.
WINDOW_W, WINDOW_H = 1340, 720
CHALLENGE_TITLE = "Just a moment"
CHALLENGE_POLL_S = 1.0
X_SOCKETS = Path("/tmp/.X11-unix")

# Runs inside the claude.ai tab. credentials: "include" carries the session cookies; the
# request therefore has the real browser's TLS fingerprint, header set and origin. For a
# stream the body is read until the terminal event or the timeout, then dropped: research
# continues server-side regardless.
FETCH_JS = """async ({method, path, body, headers, stream, timeoutMs}) => {
  // r and text are declared outside the try on purpose: when the AbortController fires
  // mid-stream, the catch must still report the status the server already sent. Losing a
  // 200 there would turn a launched run (research is running server-side and cannot be
  // cancelled by closing the stream) into an apparent failure with no run.json.
  let r = null;
  let text = "";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    r = await fetch(path, {
      method, headers, credentials: "include", signal: ctrl.signal,
      body: body === null ? undefined : JSON.stringify(body),
    });
    if (stream && r.body) {
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        text += dec.decode(value, {stream: true});
        if (text.includes("message_stop")) break;
      }
      try { await reader.cancel(); } catch (e) {}
    } else {
      text = await r.text();
    }
    return {status: r.status, text};
  } catch (e) {
    if (r) return {status: r.status, text, aborted: true};
    return {status: 0, text: String(e)};
  } finally {
    clearTimeout(timer);
  }
}"""


class SessionError(RuntimeError):
    pass


class LoggedOut(SessionError):
    pass


class Challenged(SessionError):
    """Cloudflare served its interstitial instead of the app; nothing behind it can be trusted."""


class BrowserError(SessionError):
    """Playwright failed or hung. The design's error table maps this to exit 1, not to exit 2."""


@contextmanager
def _browser_errors():
    """Turn Playwright's own errors into BrowserError so a hang surfaces as a message, not a traceback."""
    from playwright.sync_api import Error as PlaywrightError  # imported late, like sync_playwright

    try:
        yield
    except PlaywrightError as exc:
        raise BrowserError(f"browser: {exc}") from exc


def is_login_link(answer: str) -> bool:
    """Whether what the operator pasted is a sign-in link rather than a typed code.

    The link is a one-time credential: it is navigated to, never printed, logged or stored.
    Only an https claude.ai address qualifies. The browser it opens in holds a live claude.ai
    session, so a mistyped or hostile paste must be refused rather than visited.
    """
    try:
        parsed = urlparse(answer.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme.lower() == "https" and (host == LOGIN_HOST or host.endswith("." + LOGIN_HOST))


def x_display(sockets: Path = X_SOCKETS) -> str | None:
    """The first X display socket as ':N', or None when no X server is up.

    The display number is not stable across boots on this host (:0 autologin, :1 greeter),
    so it is read from the socket directory every time rather than pinned in a unit file.
    """
    if not sockets.is_dir():
        return None
    for entry in sorted(sockets.iterdir()):
        if entry.name.startswith("X") and entry.name[1:].isdigit():
            return f":{entry.name[1:]}"
    return None


def page_state(url: str, title: str) -> str:
    """'challenged' (Cloudflare interstitial), 'logged_out' (login or logout route) or 'logged_in'."""
    if CHALLENGE_TITLE in title:
        return "challenged"
    if "/login" in url or "/logout" in url:
        return "logged_out"
    return "logged_in"


LIVE_LINK_NAME = "chrome-profile"


def _login_hint(profile: Path) -> str:
    """What to run to fix a bad profile: `login <name>` for a named account, or `switch
    <name>` for the live link itself, which names no account to log in."""
    name = profile.resolve().name
    if name == LIVE_LINK_NAME:
        return "deep-research-web switch <name>"
    return f"deep-research-web login {name}"


def check_profile_permissions(profile: Path) -> None:
    if not profile.exists():
        raise SessionError(f"profile {profile} does not exist; run: {_login_hint(profile)}")
    mode = stat.S_IMODE(profile.stat().st_mode)
    if mode & 0o077:
        raise SessionError(f"profile {profile} is mode {mode:o}; it must be 0700 (chmod 700 {profile})")


class Session:
    def __init__(self, profile: Path, headless: bool = False, create: bool = False):
        self.profile = Path(profile)
        self.headless = headless
        self.create = create
        self._pw = None
        self._ctx = None
        self._page = None
        self._lock_fd: int | None = None

    def _take_lock(self) -> None:
        """One browser at a time on this profile: two Chromes on one profile corrupt it."""
        path = self.profile / LOCK_FILE
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise SessionError("browser profile busy: another deep-research-web command is running") from exc
        self._lock_fd = fd

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def __enter__(self) -> "Session":
        if self.create:
            self.profile.mkdir(parents=True, exist_ok=True)
            self.profile.chmod(0o700)
        check_profile_permissions(self.profile)
        self._take_lock()  # before Playwright is even imported: a busy profile costs nothing
        try:
            if not self.headless and not os.environ.get("DISPLAY"):
                display = x_display()
                if display is None:
                    raise BrowserError(
                        "no X display socket in /tmp/.X11-unix; the engine needs the X session "
                        "(headless Chrome is challenged by Cloudflare on claude.ai)"
                    )
                os.environ["DISPLAY"] = display
            with _browser_errors():
                from playwright.sync_api import sync_playwright  # imported late: tests never need it

                self._pw = sync_playwright().start()
                try:
                    # channel="chrome" is the system Google Chrome; Playwright drives it over a pipe,
                    # so no debugging port is opened. No extra args: any --remote-debugging-port here
                    # would violate the standing rule.
                    self._ctx = self._pw.chromium.launch_persistent_context(
                        str(self.profile),
                        channel="chrome",
                        headless=self.headless,
                        chromium_sandbox=True,
                        ignore_default_args=["--enable-automation"],
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            f"--window-size={WINDOW_W},{WINDOW_H}",
                            "--window-position=0,0",
                        ],
                        # The page takes the window's own size instead of an emulated viewport.
                        # A fixed viewport taller than the X screen cannot scroll, so a human on
                        # the streaming bridge could not reach Cloudflare's checkbox; it is also
                        # the shape bot checks look for, an inner height above the screen height.
                        no_viewport=True,
                    )
                except Exception:
                    self._pw.stop()
                    self._pw = None
                    raise
                self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        except BaseException:
            # __exit__ never runs when __enter__ raises, so the lock is released here.
            self._release_lock()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        finally:
            try:
                if self._pw is not None:
                    self._pw.stop()
            finally:
                self._release_lock()

    def open(self, path: str = "/new", challenge_timeout_s: float = SETTLE_TIMEOUT_MS / 1000) -> str:
        """Load a route and return the URL it settled on; a challenge that never clears raises.

        `challenge_timeout_s` is how long an interstitial may sit there: seconds for an
        unattended command, minutes for `login`, where a human may be ticking the box
        through the streaming bridge.
        """
        with _browser_errors():
            self._page.goto(CLAUDE + path, wait_until="load")
            self.wait_out_challenge(challenge_timeout_s)
            self._page.wait_for_url(SETTLED_URL, timeout=SETTLE_TIMEOUT_MS)
            return self._page.url

    def wait_out_challenge(self, challenge_timeout_s: float) -> None:
        """Sit through a Cloudflare interstitial, or raise when it is still there at the deadline.

        Every navigation this engine makes goes through here, the sign-in link included: a
        challenge drawn after that navigation is the same interstitial as any other.
        """
        deadline = time.monotonic() + challenge_timeout_s
        while CHALLENGE_TITLE in self._page.title() and time.monotonic() < deadline:
            time.sleep(CHALLENGE_POLL_S)
        if CHALLENGE_TITLE in self._page.title():
            raise Challenged(
                "Cloudflare challenge did not clear; headless Chrome is refused on claude.ai, "
                "run the engine headed on the X session (the default)"
            )

    def logged_in(self, challenge_timeout_s: float = SETTLE_TIMEOUT_MS / 1000) -> bool:
        url = self.open("/new", challenge_timeout_s=challenge_timeout_s)
        return page_state(url, self._page.title()) == "logged_in"

    def require_login(self) -> None:
        if not self.logged_in():
            raise LoggedOut(f"profile is not logged in to claude.ai; run: {_login_hint(self.profile)}")

    def cookies(self) -> dict[str, str]:
        return {c["name"]: c["value"] for c in self._ctx.cookies(CLAUDE)}

    def api(self, method: str, path: str, body=None, stream: bool = False, timeout_ms: int = 120_000) -> ApiResponse:
        with _browser_errors():
            headers = api_headers(self.cookies(), stream=stream)
            result = self._page.evaluate(FETCH_JS, {
                "method": method, "path": path, "body": body, "headers": headers,
                "stream": stream, "timeoutMs": timeout_ms,
            })
        # An aborted stream still carries the status the server sent; callers need only
        # status and text, so the aborted marker the JS sets is ignored here.
        return ApiResponse(int(result["status"]), str(result["text"]))

    def _code_field_appeared(self) -> bool:
        """Whether claude.ai showed a one-time-code field for this account."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout  # late, like sync_playwright

        try:
            self._page.wait_for_selector(LOGIN_CODE, timeout=CODE_FIELD_TIMEOUT_MS)
        except PlaywrightTimeout:
            return False
        return True

    def login(self, email: str, code_prompt: Callable[[], str]) -> None:
        # A human may have to tick Cloudflare's checkbox on the X display through the
        # streaming bridge, so the interstitial gets minutes here, not seconds.
        self.open("/login", challenge_timeout_s=LOGIN_CHALLENGE_S)
        with _browser_errors():
            page = self._page
            page.fill(LOGIN_EMAIL, email)
            page.keyboard.press("Enter")
            # claude.ai answers with a code, a sign-in link, or both, and only a code has a
            # field to type into. Wait briefly for that field, then take whichever the mail
            # carried: a link has to open in THIS profile's browser, the one that asked for it.
            code_field = self._code_field_appeared()
            answer = code_prompt().strip()
            link = is_login_link(answer)
            try:
                if link:
                    page.goto(answer, wait_until="domcontentloaded")
                    # The link's own landing can be challenged like any other navigation, and
                    # a human is already at the screen here, so it gets the login deadline.
                    self.wait_out_challenge(LOGIN_CHALLENGE_S)
                    # A magic link carries its token in the URL fragment, which no server ever
                    # sees: the page's own JavaScript exchanges it for a session, and only then
                    # does claude.ai send the browser to /new. The URL leaves /login the moment
                    # the link opens, so waiting on that alone returned while the exchange was
                    # still running, and the next API call met "account_session_invalid".
                    page.wait_for_url(LOGGED_IN_URL, timeout=LINK_SETTLE_MS)
                    return
                elif code_field:
                    page.fill(LOGIN_CODE, answer)
                    page.keyboard.press("Enter")
                else:
                    raise SessionError(
                        "claude.ai showed no code field, and what you pasted is not an https "
                        "claude.ai sign-in link; open the mail, copy that link and paste it here"
                    )
                # The code path keeps the looser wait on purpose: claude.ai checks a typed code
                # on the server and only redirects once it holds, so leaving /login here does
                # mean a session. A link is the opposite, which is why it waits for the app.
                page.wait_for_url(lambda url: "/login" not in url, timeout=LOGIN_TIMEOUT_MS)
            except SessionError:
                raise
            except Exception as exc:
                # Playwright puts the URL in its error text and in the log it attaches, and
                # main() prints that and hands it to claude-log. A one-time sign-in link must
                # not reach either, so the failure is reported by its type alone, with the
                # original exception dropped rather than chained.
                if link:
                    raise SessionError(
                        f"the sign-in link did not complete the login ({type(exc).__name__}); "
                        "ask claude.ai for a fresh link and paste that"
                    ) from None
                raise

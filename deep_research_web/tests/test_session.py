# ABOUTME: Tests for the browser session: profile permission checks offline, one opt-in live smoke.
# ABOUTME: The live test runs only with DEEP_RESEARCH_WEB_LIVE=1 and touches nothing but /api/organizations.
import fcntl
import os
from pathlib import Path

import pytest

from deep_research_web import session
from deep_research_web.session import (
    FETCH_JS, BrowserError, LoggedOut, Session, SessionError, check_profile_permissions,
)


def test_missing_profile_is_refused_with_the_login_hint(tmp_path):
    with pytest.raises(SessionError, match="deep-research-web login nope"):
        check_profile_permissions(tmp_path / "nope")


def test_missing_live_link_is_refused_with_the_switch_hint(tmp_path):
    # No account name is knowable for a live link that was never pointed anywhere: the fix
    # is to switch to a saved account, not to log an unnamed one in.
    with pytest.raises(SessionError, match=r"deep-research-web switch <name>"):
        check_profile_permissions(tmp_path / "chrome-profile")


def test_require_login_names_the_account_in_the_hint(tmp_path):
    profile = tmp_path / "profiles" / "work5"
    profile.mkdir(parents=True, mode=0o700)
    s = Session(profile)
    s.logged_in = lambda **kw: False
    with pytest.raises(LoggedOut, match="deep-research-web login work5"):
        s.require_login()


def test_require_login_on_the_live_link_names_switch(tmp_path):
    profile = tmp_path / "chrome-profile"
    s = Session(profile)
    s.logged_in = lambda **kw: False
    with pytest.raises(LoggedOut, match=r"deep-research-web switch <name>"):
        s.require_login()


def test_loose_permissions_are_refused(tmp_path):
    p = tmp_path / "profile"
    p.mkdir(mode=0o750)
    with pytest.raises(SessionError, match="0700"):
        check_profile_permissions(p)


def test_strict_permissions_pass(tmp_path):
    p = tmp_path / "profile"
    p.mkdir(mode=0o700)
    check_profile_permissions(p)


def test_an_aborted_stream_keeps_the_status_the_server_sent():
    # The response object is declared outside the try, so the catch can report the 200 the
    # server already sent instead of turning a launched run into an apparent failure.
    assert "let r = null" in FETCH_JS
    assert "aborted: true" in FETCH_JS


def test_the_session_never_waits_for_networkidle():
    # claude.ai polls forever; networkidle never settles and escapes as a Playwright timeout.
    assert "networkidle" not in Path(session.__file__).read_text(encoding="utf-8")


def test_browser_error_is_a_session_error():
    assert issubclass(BrowserError, SessionError)


def test_a_locked_profile_is_refused_before_playwright_is_touched(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    holder = os.open(profile / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(SessionError, match="busy"):
            Session(profile).__enter__()
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)


@pytest.mark.skipif(not os.environ.get("DEEP_RESEARCH_WEB_LIVE"), reason="live smoke; set DEEP_RESEARCH_WEB_LIVE=1")
def test_live_profile_reaches_a_chat_org():
    from deep_research_web.client import Client, choose_org
    from deep_research_web.config import load_config
    from deep_research_web.session import Session

    cfg = load_config()
    with Session(cfg.profile) as s:
        s.require_login()
        org = choose_org(Client(s.api).orgs())
    assert org["uuid"]


class _FakeKeyboard:
    def __init__(self):
        self.presses = []

    def press(self, key):
        self.presses.append(key)


class _FakePage:
    """A login page that either shows the one-time-code field or never does."""

    def __init__(self, code_field: bool, page_title: str = "Claude", never_settles: bool = False):
        self.code_field = code_field
        self.page_title = page_title
        self.never_settles = never_settles
        self.waited_for = None
        self.url = "https://claude.ai/magic-link"
        self.keyboard = _FakeKeyboard()
        self.filled: list[tuple[str, str]] = []
        self.visited: list[str] = []
        self.waited_for_url = False

    def fill(self, selector, value):
        self.filled.append((selector, value))

    def wait_for_selector(self, selector, timeout=None):
        if not self.code_field:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            raise PlaywrightTimeout(f"no {selector}")

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url

    def title(self):
        return self.page_title

    def wait_for_url(self, predicate, timeout=None):
        self.waited_for_url = True
        self.waited_for = predicate
        if self.never_settles:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            # Playwright's own timeout names the page's current URL, fragment and all.
            raise PlaywrightTimeout(f'Timeout: waiting for url; navigated to "{self.url}"')


def _session_on(tmp_path, page):
    profile = tmp_path / "profiles" / "work7"
    profile.mkdir(parents=True, mode=0o700)
    s = Session(profile)
    s.open = lambda *a, **kw: None      # the browser half; the login decision is what is tested
    s._page = page
    return s


def test_login_types_a_code_into_the_code_field(tmp_path):
    page = _FakePage(code_field=True)
    _session_on(tmp_path, page).login("me@example.test", lambda: " 123456 ")
    assert page.filled == [(session.LOGIN_EMAIL, "me@example.test"), (session.LOGIN_CODE, "123456")]
    assert page.visited == [] and page.waited_for_url


def test_login_follows_a_pasted_sign_in_link(tmp_path):
    # claude.ai sends a link instead of a code for some accounts; the link must open in THIS
    # profile's browser, which is the one that asked for it.
    page = _FakePage(code_field=False)
    link = "https://claude.ai/magic-link?token=xyz"
    _session_on(tmp_path, page).login("me@example.test", lambda: link)
    assert page.visited == [link]
    assert [sel for sel, _ in page.filled] == [session.LOGIN_EMAIL]
    assert page.waited_for_url


def test_login_takes_a_link_even_when_the_code_field_is_there(tmp_path):
    page = _FakePage(code_field=True)
    link = "https://claude.ai/magic-link?token=xyz"
    _session_on(tmp_path, page).login("me@example.test", lambda: link)
    assert page.visited == [link] and [sel for sel, _ in page.filled] == [session.LOGIN_EMAIL]


def test_login_says_what_to_do_when_no_code_field_and_no_link(tmp_path):
    page = _FakePage(code_field=False)
    with pytest.raises(SessionError, match="sign-in link"):
        _session_on(tmp_path, page).login("me@example.test", lambda: "123456")
    assert not page.waited_for_url


class _RecordingChromium:
    def __init__(self, record):
        self.record = record

    def launch_persistent_context(self, profile_dir, **kwargs):
        self.record.update(kwargs, profile_dir=profile_dir)
        return _RecordingContext()


class _RecordingContext:
    pages: list = []

    def new_page(self):
        return _FakePage(code_field=True)

    def close(self):
        pass


class _RecordingPlaywright:
    def __init__(self, record):
        self.chromium = _RecordingChromium(record)

    def start(self):
        return self

    def stop(self):
        pass


def _launch_kwargs(tmp_path, monkeypatch) -> dict:
    """What the session actually hands Chrome, captured instead of read out of the source."""
    import playwright.sync_api as pw_api

    record: dict = {}
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(pw_api, "sync_playwright", lambda: _RecordingPlaywright(record))
    profile = tmp_path / "profiles" / "work7"
    profile.mkdir(parents=True, mode=0o700)
    with Session(profile):
        pass
    return record


def test_the_page_takes_the_window_size_and_the_window_fits_the_screen(tmp_path, monkeypatch):
    # A fixed viewport taller than the X screen leaves the page unscrollable, so Cloudflare's
    # checkbox sits below the screen edge where nobody on the streaming bridge can click it.
    kwargs = _launch_kwargs(tmp_path, monkeypatch)
    assert kwargs["no_viewport"] is True
    assert "viewport" not in kwargs
    assert f"--window-size={session.WINDOW_W},{session.WINDOW_H}" in kwargs["args"]
    assert session.WINDOW_W <= 1368 and session.WINDOW_H <= 768


def test_the_browser_does_not_present_as_automated_and_opens_no_port(tmp_path, monkeypatch):
    # Cloudflare re-challenges a browser that presents as automated; this flag is why it does
    # not. A debugging port on loopback is readable by any local uid, so there must be none.
    kwargs = _launch_kwargs(tmp_path, monkeypatch)
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]
    assert kwargs["ignore_default_args"] == ["--enable-automation"]
    assert kwargs["channel"] == "chrome"
    assert not any("remote-debugging" in arg for arg in kwargs["args"])


def test_a_non_timeout_browser_error_while_waiting_for_the_code_field_is_wrapped(tmp_path):
    # _code_field_appeared catches only TimeoutError; every other Playwright error must still
    # reach the operator as BrowserError, never as a raw traceback.
    class _Broken(_FakePage):
        def wait_for_selector(self, selector, timeout=None):
            from playwright.sync_api import Error as PlaywrightError
            raise PlaywrightError("target crashed")

    with pytest.raises(BrowserError, match="target crashed"):
        _session_on(tmp_path, _Broken(code_field=False)).login("me@example.test", lambda: "123456")


def test_only_a_claude_ai_link_counts_as_a_sign_in_link():
    assert session.is_login_link("https://claude.ai/magic-link?token=x")
    assert session.is_login_link("https://www.claude.ai/magic-link?token=x")
    assert session.is_login_link("HTTPS://Claude.ai/magic-link?token=x")
    # Anything else is a typed code or a paste that must never be navigated to inside a
    # profile holding a live claude.ai session.
    assert not session.is_login_link("123456")
    assert not session.is_login_link("https://evil.test/magic-link?token=x")
    assert not session.is_login_link("https://claude.ai.evil.test/x")
    assert not session.is_login_link("http://claude.ai/magic-link?token=x")   # https only
    assert not session.is_login_link("javascript:alert(1)")


def test_a_failed_link_navigation_never_echoes_the_link(tmp_path):
    # The link is a one-time credential. Playwright puts the URL in its error text, and
    # main() prints that and hands it to claude-log, so the raise here must not carry it.
    link = "https://claude.ai/magic-link?token=SECRET"

    class _Exploding(_FakePage):
        def goto(self, url, wait_until=None):
            from playwright.sync_api import Error as PlaywrightError
            raise PlaywrightError(f'net::ERR_ABORTED at {url}')

    page = _Exploding(code_field=False)
    with pytest.raises(SessionError) as caught:
        _session_on(tmp_path, page).login("me@example.test", lambda: link)
    assert "SECRET" not in str(caught.value) and "magic-link" not in str(caught.value)
    assert "sign-in link" in str(caught.value)


def test_a_challenge_after_the_sign_in_link_is_waited_out_and_then_raises(tmp_path, monkeypatch):
    # The link's landing page gets the same interstitial handling as any other navigation.
    monkeypatch.setattr(session, "LOGIN_CHALLENGE_S", 0.0)   # the deadline is already past
    page = _FakePage(code_field=False, page_title=session.CHALLENGE_TITLE)
    with pytest.raises(session.Challenged):
        _session_on(tmp_path, page).login("me@example.test", lambda: "https://claude.ai/magic?t=1")
    assert page.visited == ["https://claude.ai/magic?t=1"] and not page.waited_for_url


def test_a_link_waits_for_the_session_not_merely_for_leaving_login(tmp_path):
    # The token rides in the URL fragment, which no server sees: the page exchanges it in
    # JavaScript and only then lands on /new. Waiting for "not /login" returned too early and
    # the next API call met account_session_invalid.
    page = _FakePage(code_field=False)
    _session_on(tmp_path, page).login("me@example.test", lambda: "https://claude.ai/magic?t=1")
    assert page.waited_for is session.LOGGED_IN_URL
    assert session.LOGGED_IN_URL.search("https://claude.ai/new?foo=1")
    assert session.LOGGED_IN_URL.search("https://claude.ai/chat/9b5f9863-b55f-ed93-c0b7-a13557651def")
    assert not session.LOGGED_IN_URL.search("https://claude.ai/login")
    assert not session.LOGGED_IN_URL.search("https://claude.ai/magic-link#token")


def test_a_link_that_never_reaches_the_app_fails_without_echoing_itself(tmp_path):
    page = _FakePage(code_field=False, never_settles=True)
    with pytest.raises(SessionError) as caught:
        _session_on(tmp_path, page).login("me@example.test", lambda: "https://claude.ai/magic?t=SECRET")
    assert "SECRET" not in str(caught.value) and "sign-in link" in str(caught.value)

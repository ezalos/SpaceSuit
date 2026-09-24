# ABOUTME: Tests for the headed-by-default session: X display discovery and page-state classification.
# ABOUTME: Pure helpers only; no browser is opened here.
from pathlib import Path

import pytest

from deep_research_web.session import (
    Challenged, Session, SessionError, page_state, x_display,
)


def test_x_display_reads_the_first_socket(tmp_path):
    (tmp_path / "X0").touch()
    (tmp_path / "X1").touch()
    assert x_display(tmp_path) == ":0"


def test_x_display_is_none_without_a_server(tmp_path):
    assert x_display(tmp_path) is None
    assert x_display(tmp_path / "missing") is None


def test_page_state_classifies_challenge_login_and_app():
    assert page_state("https://claude.ai/new", "Just a moment...") == "challenged"
    assert page_state("https://claude.ai/login?from=logout", "Claude") == "logged_out"
    assert page_state("https://claude.ai/logout?involuntary=1&returnTo=%2Flogin", "Claude") == "logged_out"
    assert page_state("https://claude.ai/new", "Claude") == "logged_in"
    assert page_state("https://claude.ai/chat/abc", "Claude") == "logged_in"


def test_session_is_headed_by_default(tmp_path):
    assert Session(tmp_path / "profile").headless is False


def test_login_gives_the_interstitial_minutes_not_seconds():
    import inspect

    from deep_research_web.session import LOGIN_CHALLENGE_S, SETTLE_TIMEOUT_MS

    assert LOGIN_CHALLENGE_S >= 300 > SETTLE_TIMEOUT_MS / 1000
    assert "challenge_timeout_s" in inspect.signature(Session.open).parameters
    assert "challenge_timeout_s" in inspect.signature(Session.logged_in).parameters


def test_challenged_is_a_session_error():
    assert issubclass(Challenged, SessionError)
    with pytest.raises(SessionError):
        raise Challenged("x")

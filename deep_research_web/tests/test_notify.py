# ABOUTME: Tests for the Telegram line format and the channel credential loader.
# ABOUTME: Nothing here talks to Telegram; the poster is a recording stub.
import json
import urllib.error

import pytest

from deep_research_web import notify
from deep_research_web.notify import NotifyError, done_line, format_message, load_channel, outcome_line, post_telegram, send
from deep_research_web.runs import RunRecord

REC = RunRecord("2026-09-16-1200-q", "Which vector db?", "running", "o", "c", "https://claude.ai/chat/c", "m", "c.md", "/out", "t")


def _chan(tmp_path, token="TOKEN123", allow=("4242",)):
    (tmp_path / ".env").write_text(f'TELEGRAM_BOT_TOKEN="{token}"\n')
    (tmp_path / "access.json").write_text(json.dumps({"allowFrom": list(allow)}))
    return tmp_path


def test_format_message_carries_emoji_tag_and_host():
    assert format_message("hello", host="host1") == "🖥️ [deep-research] host1: hello"


def test_load_channel_reads_token_and_first_allowed_chat(tmp_path):
    assert load_channel(_chan(tmp_path)) == ("TOKEN123", "4242")


def test_load_channel_errors_are_specific(tmp_path):
    with pytest.raises(NotifyError, match="telegram:configure"):
        load_channel(tmp_path)
    with pytest.raises(NotifyError, match="allowlist"):
        load_channel(_chan(tmp_path, allow=()))


def test_send_posts_the_formatted_line(tmp_path):
    posted = []
    send("done", poster=lambda t, c, text: posted.append((t, c, text)), chan_dir=_chan(tmp_path), host="host1")
    assert posted == [("TOKEN123", "4242", "🖥️ [deep-research] host1: done")]


def test_done_and_outcome_lines():
    line = done_line(REC, {"quoted": 3, "live": 2, "misquoted": 0, "dead": 1, "unverifiable": 0, "unchecked": 0}, "/out/report.md")
    assert line == 'research done: "Which vector db?" https://claude.ai/chat/c report: /out/report.md (3 quoted, 2 live, 1 dead)'
    assert outcome_line(REC, "stale", "no report after 90 min") == 'research stale: "Which vector db?" https://claude.ai/chat/c (no report after 90 min)'


def test_post_telegram_wraps_transport_failures_in_notify_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    with pytest.raises(NotifyError, match="down"):
        post_telegram("t", "c", "x")

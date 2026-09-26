# ABOUTME: Sends the watcher's one-line Telegram messages through the user-level channel bot.
# ABOUTME: Same credential as the notify-louis skill; the [deep-research] tag is registered in monitoring/telegram-senders.yaml.
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .runs import RunRecord

TAG = "[deep-research]"
EMOJI = "\U0001f5a5️"  # the console emoji of the private infra repo's naming rules
CHANNEL_DIR = Path.home() / ".claude/channels/telegram"
# A machine whose Telegram is not this channel (no .env there) names its own sender here, in the engine's env file:
# a command that takes the message as its LAST argument and exits 0 only when it was delivered.
NOTIFY_CMD_ENV = "DEEP_RESEARCH_WEB_NOTIFY_CMD"


class NotifyError(RuntimeError):
    pass


def load_channel(chan_dir: Path = CHANNEL_DIR) -> tuple[str, str]:
    env = chan_dir / ".env"
    access = chan_dir / "access.json"
    if not env.exists():
        raise NotifyError("telegram not configured: run /telegram:configure")
    token = None
    for line in env.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "TELEGRAM_BOT_TOKEN":
            token = value.strip().strip('"').strip("'")
    if not token:
        raise NotifyError("TELEGRAM_BOT_TOKEN unset: run /telegram:configure")
    allow = json.loads(access.read_text(encoding="utf-8")).get("allowFrom") if access.exists() else None
    if not allow:
        raise NotifyError("empty allowlist: run /telegram:access pair")
    return token, str(allow[0])


def format_message(body: str, host: str | None = None) -> str:
    host = host or socket.gethostname().split(".")[0].lower()
    return f"{EMOJI} {TAG} {host}: {body}"


def post_telegram(token: str, chat_id: str, text: str) -> None:
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status != 200:
                raise NotifyError(f"telegram API HTTP {r.status}")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        raise NotifyError(f"telegram API error: {exc}") from exc


def send(
    body: str, poster: Callable[[str, str, str], None] = post_telegram,
    chan_dir: Path = CHANNEL_DIR, host: str | None = None,
) -> None:
    text = format_message(body, host)
    command = os.environ.get(NOTIFY_CMD_ENV, "").strip()
    if command:
        try:
            done = subprocess.run([*shlex.split(command), text], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotifyError(f"the notify command could not run: {type(exc).__name__}") from exc
        if done.returncode != 0:
            # Its own output only, never the command line: it may carry arguments nobody should read in a log.
            raise NotifyError(f"the notify command exited {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}")
        return
    token, chat_id = load_channel(chan_dir)
    poster(token, chat_id, text)


def _counts_text(counts: dict[str, int]) -> str:
    return ", ".join(f"{v} {k}" for k, v in counts.items() if v) or "no citations"


def done_line(rec: RunRecord, counts: dict[str, int], report_path) -> str:
    return f'research done: "{rec.question}" {rec.chat_url} report: {report_path} ({_counts_text(counts)})'


def outcome_line(rec: RunRecord, outcome: str, reason: str) -> str:
    return f'research {outcome}: "{rec.question}" {rec.chat_url} ({reason})'

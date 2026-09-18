#!/usr/bin/env python3
# ABOUTME: Custom status line for Claude Code displaying model, context, and cost info.
# ABOUTME: See https://code.claude.com/docs/en/statusline for configuration details.

import json
import os
import shutil
import subprocess
import sys

data = json.load(sys.stdin)

# Core info -- every field defensive: a missing key must never kill the status line
model = (data.get("model") or {}).get("display_name") or "Claude"
directory = os.path.basename((data.get("workspace") or {}).get("current_dir") or os.getcwd())
version = data.get("version") or "?"

# Cost tracking. Claude Code's own total prices a model it does not recognise with its fallback table, so a session
# on a gateway model (a GPT or Grok row served through a local proxy) is billed at the wrong rate - measured at about
# half the real one. When the claude-usage cost tool is present it recomputes this session from its transcript against
# a sourced price table; if anything about that fails we keep Claude Code's number rather than show nothing.
cost = (data.get("cost") or {}).get("total_cost_usd", 0) or 0
_cost_exact = True
_session = data.get("session_id")
_tool = os.path.expanduser("~/.claude/claude-usage/claude-usage.js")
if _session and os.path.exists(_tool):
    try:
        # A status line inherits whatever PATH the client had, which on some setups has no node at all. Falling
        # back silently would leave the wrong figure on screen forever, so look in the usual places too.
        _node = shutil.which("node") or next(
            (c for c in ("/usr/local/bin/node", "/opt/homebrew/bin/node", "/usr/bin/node") if os.path.exists(c)),
            None,
        )
        if not _node:
            raise FileNotFoundError("node")
        _out = subprocess.run(
            [_node, _tool, "session-cost", _session],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if _out:
            _cost_exact = not _out.endswith("?")
            cost = float(_out.rstrip("?"))
    except Exception:
        pass  # a status line must never be the thing that breaks a prompt

# Context window metrics
ctx = data.get("context_window") or {}
pct = int(ctx.get("used_percentage", 0) or 0)
ctx_size = ctx.get("context_window_size", 200000) or 200000
input_tokens = ctx.get("total_input_tokens", 0) or 0
output_tokens = ctx.get("total_output_tokens", 0) or 0

# ANSI color codes for terminal styling
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Visual context bar: sized to match model name, green < 70%, yellow 70-90%, red >= 90%
# Bar width = emoji (2 chars) + space + model name length
bar_width = 2 + 1 + len(model)
bar_color = RED if pct >= 90 else YELLOW if pct >= 70 else GREEN
filled = int(bar_width * pct / 100)
bar = "█" * filled + "░" * (bar_width - filled)

# Git branch detection (subprocess is more reliable than reading .git/HEAD)
try:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], text=True, stderr=subprocess.DEVNULL
    ).strip()
    branch = f" | 🌿 {branch}" if branch else ""
except Exception:
    branch = ""

# Session title: what this session is about, the same title `tls` shows. Claude Code sends it as `session_name` -
# the /rename name when there is one, else its own AI-generated title - so there is no transcript to read here.
# Absent until the first title exists. Whitespace is collapsed because a newline in it would break the two-line layout.
TITLE_MAX = 48
title = " ".join((data.get("session_name") or "").split())
if len(title) > TITLE_MAX:
    title = title[: TITLE_MAX - 1].rstrip() + "…"
title = f" | 💬 {title}" if title else ""


def fmt_tokens(n: int) -> str:
    """Format token counts as human-readable strings (e.g., 123k, 1.2M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


ctx_k = f"{ctx_size // 1000}k"

# Line 1: Model with version inline, directory, git branch, session title
print(f"{CYAN}{BOLD}🧠 {model}{RESET} {DIM}v{version}{RESET} | 📁 {directory}{branch}{title}")

# Session id, whole and last on its line so it can be selected and pasted into `claude --resume <id>` as it stands.
session_tag = f" | 🆔 {DIM}{_session}{RESET}" if _session else ""

# Line 2: Visual context bar, token usage, cost, session id
print(
    f"{bar_color}{bar}{RESET} {pct}%"
    f" {DIM}({fmt_tokens(input_tokens)}↓ {fmt_tokens(output_tokens)}↑ / {ctx_k}){RESET}"
    f" | {YELLOW}💰 ${cost:.2f}{'?' if not _cost_exact else ''}{RESET}"
    f"{session_tag}"
)

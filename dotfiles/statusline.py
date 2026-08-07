#!/usr/bin/env python3
# ABOUTME: Custom status line for Claude Code displaying model, context, and cost info.
# ABOUTME: See https://code.claude.com/docs/en/statusline for configuration details.

import json
import os
import subprocess
import sys

data = json.load(sys.stdin)

# Core info -- every field defensive: a missing key must never kill the status line
model = (data.get("model") or {}).get("display_name") or "Claude"
directory = os.path.basename((data.get("workspace") or {}).get("current_dir") or os.getcwd())
version = data.get("version") or "?"

# Cost tracking
cost = (data.get("cost") or {}).get("total_cost_usd", 0) or 0

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


def fmt_tokens(n: int) -> str:
    """Format token counts as human-readable strings (e.g., 123k, 1.2M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


ctx_k = f"{ctx_size // 1000}k"

# Line 1: Model with version inline, directory, git branch
print(f"{CYAN}{BOLD}🧠 {model}{RESET} {DIM}v{version}{RESET} | 📁 {directory}{branch}")

# Line 2: Visual context bar, token usage, cost
print(
    f"{bar_color}{bar}{RESET} {pct}%"
    f" {DIM}({fmt_tokens(input_tokens)}↓ {fmt_tokens(output_tokens)}↑ / {ctx_k}){RESET}"
    f" | {YELLOW}💰 ${cost:.2f}{RESET}"
)

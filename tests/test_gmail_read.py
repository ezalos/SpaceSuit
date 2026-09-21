"""Tests for dotfiles/bin/gmail-read: the fresh-process Gmail connector bridge.

Real files only: a stub `claude` executable on PATH that records its own argv, so these
tests never touch the network or a real claude.ai connector — they only verify the argv
gmail-read constructs, in particular that a write tool never appears in --allowedTools.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "dotfiles" / "bin" / "gmail-read"


def stub_claude(bin_dir, record_path, exit_code=0):
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(record_path)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"sys.exit({exit_code})\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def run(tmp_path, *args):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "argv.json"
    stub_claude(bin_dir, record)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)
    argv = json.loads(record.read_text()) if record.exists() else None
    return result, argv


def test_read_tools_only_no_write_tools_ever(tmp_path):
    result, argv = run(tmp_path, "list unread from the last day")
    assert result.returncode == 0, result.stderr
    assert argv[0:2] == ["-p", "list unread from the last day"]
    allowed = argv[argv.index("--allowedTools") + 1]
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert allowed == (
        "mcp__claude_ai_Gmail__search_threads,mcp__claude_ai_Gmail__get_thread,"
        "mcp__claude_ai_Gmail__get_message,mcp__claude_ai_Gmail__list_labels"
    )
    for write_tool in ["delete_draft", "unmark_message_spam", "reply", "forward", "create_draft", "trash_message"]:
        assert write_tool not in allowed, f"{write_tool} must never be in --allowedTools"
        assert any(write_tool in t for t in disallowed.split(",")), f"{write_tool} must be explicitly disallowed"
    assert all(t in disallowed for t in ["Bash", "Write", "Edit", "Agent"])


def test_file_source_reads_the_brief_from_disk(tmp_path):
    brief = tmp_path / "brief.txt"
    brief.write_text("find the CNP claim email")
    result, argv = run(tmp_path, "--file", str(brief))
    assert result.returncode == 0, result.stderr
    assert argv[1] == "find the CNP claim email"


def test_empty_brief_is_refused(tmp_path):
    result, argv = run(tmp_path, "   ")
    assert result.returncode != 0
    assert argv is None, "claude must never be invoked with an empty brief"


def test_brief_and_file_are_mutually_exclusive(tmp_path):
    brief = tmp_path / "brief.txt"
    brief.write_text("x")
    result, argv = run(tmp_path, "inline", "--file", str(brief))
    assert result.returncode != 0
    assert argv is None

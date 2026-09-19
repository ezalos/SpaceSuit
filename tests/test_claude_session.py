"""Tests for dotfiles/bin/claude-session, the session-id-prefix to peer-name resolver.

Real files only: a temporary ~/.claude look-alike with session records and transcripts, the
script run as a subprocess the way a shell would run it. The live pid check uses this test
process's own pid for the sessions that must count as alive.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "dotfiles" / "bin" / "claude-session"
ALIVE = os.getpid()
DEAD = 2 ** 22 - 7  # above any pid Linux hands out by default, and never this process


def record(sessions, pid, sid, name, status="idle", cwd="/work/repo"):
    (sessions / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": cwd, "name": name, "status": status,
        "tmux": "main:@1.%1", "updatedAt": 1_789_776_000_000}))


def transcript(projects, sid, lines):
    slug = projects / "-work-repo"
    slug.mkdir(exist_ok=True)
    (slug / f"{sid}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))


@pytest.fixture
def tree(tmp_path):
    sessions = tmp_path / "sessions"; projects = tmp_path / "projects"
    sessions.mkdir(); projects.mkdir()
    record(sessions, ALIVE, "61be5e73-0000-4000-8000-000000000001", "web-wm-onnx-13", "busy")
    record(sessions, DEAD, "61be5e73-0000-4000-8000-000000000002", "web-wm-onnx-dd")
    transcript(projects, "61be5e73-0000-4000-8000-000000000001", [
        {"type": "user", "sessionId": "x"},
        {"type": "ai-title", "aiTitle": "first  title"},
        {"type": "ai-title", "aiTitle": "B-wave07 launch doc"},
    ])
    return tmp_path


def run(tree, *args):
    env = dict(os.environ, CLAUDE_SESSION_SESSIONS_DIR=str(tree / "sessions"),
               CLAUDE_SESSION_PROJECTS_DIR=str(tree / "projects"))
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_a_prefix_resolves_to_the_live_peer_name_and_its_latest_title(tree):
    out = run(tree, "61b")
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("\n") == 1                       # the dead record is dropped, not listed
    assert "61be5e73  web-wm-onnx-13" in out.stdout and "busy" in out.stdout
    assert "💬 B-wave07 launch doc" in out.stdout and "first" not in out.stdout


def test_a_name_resolves_back_to_its_id_and_json_carries_the_fields(tree):
    out = run(tree, "--json", "web-wm-onnx-13")
    rows = json.loads(out.stdout)
    assert [r["id"][:8] for r in rows] == ["61be5e73"] and rows[0]["pid"] == ALIVE
    assert set(rows[0]) >= {"id", "name", "status", "cwd", "tmux", "title", "age"}


def test_no_match_and_ambiguity_are_exit_codes_not_guesses(tree):
    assert run(tree, "fff").returncode == 1
    # a second LIVE session: records are keyed by pid, so it needs a pid of its own (the parent is alive)
    record(tree / "sessions", os.getppid(), "61be0000-0000-4000-8000-000000000003", "web-wm-onnx-aa")
    out = run(tree, "61be")
    assert out.returncode == 2 and out.stdout.count("\n") == 2 and "ambiguous" in out.stderr
    assert run(tree, "61be5e73").returncode == 0                # a longer prefix disambiguates

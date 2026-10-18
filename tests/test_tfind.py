"""Tests for dotfiles/bin/tfind, the jump to a Claude session's tmux window by session-id prefix.

Real files only: a temporary ~/.claude look-alike read by the real claude-session, and a stand-in
tmux (a script that prints a fixed pane list and logs every call), so no tmux server is started
or touched. The live pid check uses this test process's own pid and its parent's.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "dotfiles" / "bin" / "tfind"
ALIVE, ALSO_ALIVE = os.getpid(), os.getppid()
ONE = "61be5e73-0000-4000-8000-000000000001"
TWO = "61be0000-0000-4000-8000-000000000002"
TMUX_STUB = """#!/bin/sh
here="$(dirname "$0")"
echo "$@" >> "$here/tmux-calls"
[ "$1" = list-panes ] && cat "$here/panes"
[ "$1" = display-message ] && [ -f "$here/size" ] && cat "$here/size"
exit 0
"""
LANE = """---
slug: old-lane
session: {session}
title: An old lane
tmux_session: lab
tmux_window: old-lane
state: closed
---

# old-lane
"""


def record(tree, pid, sid, tmux="work:@1.%1"):
    (tree / "sessions" / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": "/work/repo", "name": f"peer-{pid}", "status": "idle",
        "tmux": tmux, "updatedAt": 1_789_776_000_000}))
    slug = tree / "projects" / "-work-repo"
    slug.mkdir(exist_ok=True)
    (slug / f"{sid}.jsonl").write_text(json.dumps({"type": "ai-title", "aiTitle": f"title of {sid[:8]}"}) + "\n")


@pytest.fixture
def tree(tmp_path):
    for name in ("sessions", "projects", "proc", "lanes/lab"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "panes").write_text("%1\twork\tpaper\n%7\twork\tbench\n")
    stub = tmp_path / "tmux"
    stub.write_text(TMUX_STUB)
    stub.chmod(0o755)
    record(tmp_path, ALIVE, ONE)
    return tmp_path


def run(tree, *args, inside=True, registry=True, tmux=None):
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "LANE_REGISTRY")}
    env.update(CLAUDE_SESSION_SESSIONS_DIR=str(tree / "sessions"),
               CLAUDE_SESSION_PROJECTS_DIR=str(tree / "projects"),
               TFIND_TMUX=tmux or str(tree / "tmux"), TFIND_PROC=str(tree / "proc"))
    if inside:
        env["TMUX"] = "/nonexistent/socket,1,0"
    if registry:
        env["LANE_REGISTRY"] = str(tree / "lanes")
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def calls(tree):
    log = tree / "tmux-calls"
    return [line for line in log.read_text().splitlines() if not line.startswith("list-panes")] if log.exists() else []


def test_a_unique_prefix_switches_the_client_to_that_pane(tree):
    out = run(tree, "61b")
    assert out.returncode == 0, out.stderr
    assert "61be5e73  work:paper" in out.stdout and "title of 61be5e73" in out.stdout
    assert calls(tree) == ["switch-client -t %1"]


def test_outside_tmux_it_selects_the_window_then_attaches(tree):
    out = run(tree, "61be5e73", inside=False)
    assert out.returncode == 0, out.stderr
    assert "61be5e73  work:paper" in out.stdout              # flushed before tmux replaces the process
    assert calls(tree) == ["select-window -t %1", "select-pane -t %1", "attach-session -t work"]


def test_several_matches_are_listed_with_titles_and_nothing_moves(tree):
    record(tree, ALSO_ALIVE, TWO, tmux="work:@2.%7")
    out = run(tree, "61be")
    assert out.returncode == 2
    assert "61be5e73  work:paper  💬 title of 61be5e73" in out.stdout
    assert "61be0000  work:bench  💬 title of 61be0000" in out.stdout
    assert calls(tree) == []
    assert run(tree, "61be0").returncode == 0 and calls(tree) == ["switch-client -t %7"]


def test_a_record_without_a_tmux_field_is_found_through_its_process_environment(tree):
    record(tree, ALIVE, ONE, tmux="")
    environ(tree, ALIVE, b"HOME=/x\0TMUX_PANE=%7\0")
    out = run(tree, "61b")
    assert out.returncode == 0 and "work:bench" in out.stdout and calls(tree) == ["switch-client -t %7"]


def test_a_live_session_outside_tmux_is_said_and_nothing_moves(tree):
    record(tree, ALIVE, ONE, tmux="")
    out = run(tree, "61b")
    assert out.returncode == 3 and "not in a tmux pane" in out.stdout and calls(tree) == []
    record(tree, ALIVE, ONE, tmux="gone:@9.%99")            # a pane the server no longer has
    assert run(tree, "61b").returncode == 3 and calls(tree) == []


def environ(tree, pid, text):
    (tree / "proc" / str(pid)).mkdir(exist_ok=True)
    (tree / "proc" / str(pid) / "environ").write_bytes(text)


def test_the_process_says_where_it_runs_when_the_record_went_stale(tree):
    record(tree, ALIVE, ONE, tmux="renamed:@3.%1")         # the window was moved since the record was written
    environ(tree, ALIVE, b"TMUX=/nonexistent/socket,9,0\0TMUX_PANE=%7\0")
    out = run(tree, "61b")
    assert out.returncode == 0 and "work:bench" in out.stdout and calls(tree) == ["switch-client -t %7"]


def test_a_process_on_another_tmux_server_is_not_followed(tree):
    environ(tree, ALIVE, b"TMUX=/elsewhere/socket,9,0\0TMUX_PANE=%7\0")
    assert run(tree, "61b").returncode == 3 and calls(tree) == []          # the record's %1 is not trusted either
    assert run(tree, "61b", inside=False).returncode == 0                  # outside tmux there is no server to compare
    environ(tree, ALIVE, b"HOME=/x\0")                                      # a live process that is in no pane
    assert run(tree, "61b").returncode == 3


def test_an_uppercase_prefix_and_a_full_uuid_resolve(tree):
    assert run(tree, "61BE5").returncode == 0 and run(tree, ONE.upper()).returncode == 0
    assert calls(tree) == ["switch-client -t %1"] * 2


def test_a_broken_claude_session_or_tmux_is_its_own_exit_code_not_a_closed_session(tree):
    out = run(tree, "61b", tmux="/nonexistent/tmux")
    assert out.returncode == 4 and "cannot run" in out.stderr and "Traceback" not in out.stderr


def test_a_closed_session_gives_its_last_tmux_location_and_lane_file(tree):
    (tree / "lanes" / "lab" / "old-lane.md").write_text(LANE.format(session="0dd5e55a"))
    (tree / "lanes" / "lab" / "other.md").write_text(LANE.format(session="unset"))
    out = run(tree, "0dd5")
    assert out.returncode == 1 and "no live session" in out.stdout
    assert 'last in tmux lab:old-lane, lane state closed' in out.stdout and '"An old lane"' in out.stdout
    assert "lanes/lab/old-lane.md" in out.stdout and "other.md" not in out.stdout and calls(tree) == []
    assert "old-lane.md" in run(tree, "0dd5e55a-1111").stdout       # a prefix longer than the recorded short id


def test_lane_files_are_read_as_written_quoted_ids_full_ids_and_front_matter_only(tree):
    lab = tree / "lanes" / "lab"
    (lab / "digits.md").write_text(LANE.format(session="'12345678'"))
    assert "digits.md" in run(tree, "1234").stdout
    full = LANE.format(session="0dd5e55a").replace("title:", "session_full: 0dd5e55a-aaaa-4000-8000-000000000000\ntitle:")
    (lab / "full.md").write_text(full)
    assert "full.md" in run(tree, "0dd5e55a-aaaa").stdout and "full.md" not in run(tree, "0dd5e55a-bbbb").stdout
    (lab / "body.md").write_text("# notes\n\n" + LANE.format(session="0abc0abc"))   # a --- block that is not at the top
    assert "no lane file records it" in run(tree, "0abc").stdout


def test_a_closed_session_no_file_records_and_a_missing_registry_are_said(tree):
    out = run(tree, "fff")
    assert out.returncode == 1 and "no lane file records it" in out.stdout
    out = run(tree, "fff", registry=False)
    assert out.returncode == 1 and "LANE_REGISTRY is not set" in out.stdout


def test_a_bad_prefix_is_refused_before_anything_runs(tree):
    for bad in ([""], ["zzz"], [], ["61b", "61c"], ["--help"], ["--menu"], ["--menu", ""], ["--menu", "c", "zz"]):
        assert run(tree, *bad).returncode == 2
    assert calls(tree) == [] and not (tree / "tmux-calls").exists()


def test_escape_sequences_in_a_title_or_a_lane_file_never_reach_the_terminal(tree):
    slug = tree / "projects" / "-work-repo"
    (slug / f"{ONE}.jsonl").write_text(json.dumps({"type": "custom-title", "customTitle": "evil\x1b]0;x\x07\x1b[2Jtitle"}) + "\n")
    out = run(tree, "61b")
    assert out.returncode == 0 and "\x1b" not in out.stdout and "\x07" not in out.stdout and "evil]0;x[2Jtitle" in out.stdout
    (tree / "lanes" / "lab" / "old-lane.md").write_text(LANE.format(session="0dd5e55a").replace("An old lane", "An\x1b[31m old\x9b lane"))
    out = run(tree, "0dd5")
    assert "\x1b" not in out.stdout and "\x9b" not in out.stdout and "An[31m old lane" in out.stdout


def menu_items(tree):
    """The (label, key, command) items of the display-menu call, from the stand-in's log."""
    line = next(line for line in calls(tree) if line.startswith("display-menu"))
    return line


def test_one_or_two_characters_are_a_prefix(tree):
    out = run(tree, "6")
    assert out.returncode == 0 and calls(tree) == ["switch-client -t %1"]
    record(tree, ALSO_ALIVE, TWO, tmux="work:@2.%7")
    out = run(tree, "61")
    assert out.returncode == 2 and "title of 61be5e73" in out.stdout and "title of 61be0000" in out.stdout


def test_menu_mode_switches_the_named_client_silently_on_one_match(tree):
    out = run(tree, "--menu", "/dev/pts/9", "61b")
    assert out.returncode == 0 and out.stdout == ""
    assert calls(tree) == ["switch-client -c /dev/pts/9 -t %1"]


def test_menu_mode_opens_a_menu_with_titles_newest_first_when_several_match(tree):
    record(tree, ALSO_ALIVE, TWO, tmux="work:@2.%7")
    rec = tree / "sessions" / f"{ALSO_ALIVE}.json"
    rec.write_text(rec.read_text().replace("1789776000000", "1789776099000"))      # TWO was active last
    (tree / "projects" / "-work-repo" / f"{TWO}.jsonl").write_text(json.dumps({"type": "ai-title", "aiTitle": "issue #4"}) + "\n")
    out = run(tree, "--menu", "/dev/pts/9", "61")
    assert out.returncode == 0
    line = menu_items(tree)
    assert line.startswith("display-menu -c /dev/pts/9 -T  2 sessions 61* ")
    newest, older = line.index("61be0000  issue ##4  work:bench 1"), line.index("61be5e73  title of 61be5e73  work:paper 2")
    assert newest < older                                                     # a # in a title is escaped for tmux
    assert "switch-client -c '/dev/pts/9' -t '%7'" in line and "switch-client -c '/dev/pts/9' -t '%1'" in line


def test_menu_mode_with_no_prefix_lists_every_session_and_cuts_to_the_client_height(tree):
    (tree / "panes").write_text("".join(f"%{i}\twork\tw{i}\n" for i in range(1, 12)))
    for i in range(2, 12):                                                   # ten more live sessions, pids shared
        sid = f"aa{i:06d}-0000-4000-8000-000000000000"
        (tree / "sessions" / f"x{i}.json").write_text(json.dumps({
            "pid": ALIVE, "sessionId": sid, "cwd": "/w", "name": f"n{i}", "status": "idle", "tmux": f"work:@{i}.%{i}",
            "updatedAt": 1_789_776_000_000 + i}))
    (tree / "size").write_text("10 40\n")                                    # 10 rows: room for 6 lines
    out = run(tree, "--menu", "/dev/pts/9")
    assert out.returncode == 0
    line = menu_items(tree)
    assert "11 sessions" in line and line.count("switch-client") == 5 and "-6 more: type more characters" in line


def test_menu_mode_reports_a_closed_or_untmuxed_session_as_text_with_exit_0(tree):
    out = run(tree, "--menu", "/dev/pts/9", "fff")
    assert out.returncode == 0 and "no live session" in out.stdout and calls(tree) == []
    record(tree, ALIVE, ONE, tmux="")
    out = run(tree, "--menu", "/dev/pts/9", "61b")
    assert out.returncode == 0 and "not in a tmux pane" in out.stdout and calls(tree) == []

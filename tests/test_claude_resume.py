# ABOUTME: Tests the claude_resume package (candidates, columns, rendering) and tsnaps origin output.
# ABOUTME: Pure-logic tests run against synthetic snapshot and transcript fixtures, no tmux server.

import json
import os
import subprocess
from pathlib import Path

from claude_resume.candidates import assign_most_recent, build_candidates
from claude_resume.claude_paths import list_transcripts, project_slug, transcript_path
from claude_resume.columns import build_columns
from claude_resume.models import Column, PaneKey, SnapshotRef
from claude_resume.render import render
from claude_resume.snapshots import list_snapshots, read_panes
from claude_resume.titles import TitleResolver

REPO = Path(__file__).resolve().parent.parent
SNAPS = REPO / "scripts" / "tmux-snapshots.sh"

P0 = PaneKey("alfred", 0, 0, "/home/e/Alfred")
P1 = PaneKey("alfred", 1, 0, "/home/e/Alfred")

CLAUDE_ROW = ("setup", 1, "claude", "layout-a", 0, "/home/e/Setup", 1, 1, "aaaa1111")
SHELL_ROW = ("setup", 2, "zsh", "layout-b", 0, "/home/e/Setup", 0, 0, "")


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

def write_snapshot(root, name, saved_at, rows, origin=None):
    """rows: (session, win, win_name, layout, pane, cwd, is_claude, win_active, id)"""
    d = Path(root) / name
    (d / "pane_contents").mkdir(parents=True)
    (d / "state.tsv").write_text(
        "".join("\t".join(str(c) for c in r) + "\n" for r in rows))
    (d / "saved_at").write_text(saved_at + "\n")
    if origin is not None:
        (d / "origin").write_text(origin + "\n")
    return d


def make_transcripts(projects_dir, cwd, ids_and_mtimes):
    d = Path(projects_dir) / project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    for sid, mtime in ids_and_mtimes:
        f = d / f"{sid}.jsonl"
        f.write_text("{}\n")
        os.utime(f, (mtime, mtime))


def write_transcript(projects_dir, cwd, session_id, records):
    d = Path(projects_dir) / project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session_id}.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in records))
    return f


def snap(tmp_path, name, saved_at, pane_ids, origin="cron"):
    rows = [(p.session, p.window, "claude", "l", p.pane, p.cwd, 1, 1, sid)
            for p, sid in pane_ids.items()]
    return SnapshotRef(write_snapshot(str(tmp_path), name, saved_at, rows, origin=origin),
                       saved_at, origin)


class FakeTitles:
    def __init__(self, mapping):
        self.mapping = mapping

    def title_for(self, cwd, session_id):
        return self.mapping.get(session_id, "(untitled)")


# --------------------------------------------------------------------------
# claude_paths
# --------------------------------------------------------------------------

def test_project_slug_replaces_non_alphanumerics():
    assert project_slug("/home/ezalos/Setup") == "-home-ezalos-Setup"
    assert project_slug("/home/ezalos/42/monorepo_quater") == "-home-ezalos-42-monorepo-quater"
    assert project_slug("/home/ezalos/Work/web_wm_onnx") == "-home-ezalos-Work-web-wm-onnx"


def test_project_slug_agrees_with_agents_dashboard_on_real_paths():
    """The dashboard has its own copy of this rule. They must not drift on any
    path shape that actually occurs. Imported inside the test so the package
    itself keeps no dependency on agents_dashboard."""
    from agents_dashboard.claude_sessions import project_slug as other
    for p in ["/home/user/Setup", "/home/user/42/monorepo_quater",
              "/home/user/Work/web_wm_onnx", "/home/user/42/V-Jaygent",
              "/home/user/nested/dir-with-dashes"]:
        assert project_slug(p) == other(p), p


def test_transcript_path_finds_direct_hit(tmp_path):
    d = tmp_path / "-tmp-proj"
    d.mkdir()
    (d / "abc123.jsonl").write_text("{}\n")
    assert transcript_path("/tmp/proj", "abc123", tmp_path) == d / "abc123.jsonl"


def test_transcript_path_falls_back_to_glob(tmp_path):
    """If the slug rule is wrong for an exotic path, find the file by id anyway."""
    d = tmp_path / "some-other-encoding"
    d.mkdir()
    (d / "abc123.jsonl").write_text("{}\n")
    assert transcript_path("/tmp/proj", "abc123", tmp_path) == d / "abc123.jsonl"


def test_transcript_path_returns_none_when_absent(tmp_path):
    assert transcript_path("/tmp/proj", "gone999", tmp_path) is None


def test_list_transcripts_returns_ids_and_mtimes(tmp_path):
    make_transcripts(tmp_path, "/tmp/proj", [("aaa", 1000), ("bbb", 2000)])
    assert dict(list_transcripts("/tmp/proj", tmp_path)) == {"aaa": 1000.0, "bbb": 2000.0}


def test_list_transcripts_on_missing_dir_is_empty(tmp_path):
    assert list_transcripts("/nope", tmp_path) == []


def test_panekey_sort_key_orders_window_numerically():
    assert PaneKey("setup", 2, 0, "/x").sort_key < PaneKey("setup", 10, 0, "/x").sort_key


# --------------------------------------------------------------------------
# titles
# --------------------------------------------------------------------------

def test_title_prefers_ai_title(tmp_path):
    write_transcript(tmp_path, "/p", "s1", [
        {"type": "user", "message": {"content": "hello there"}},
        {"type": "ai-title", "aiTitle": "Investigate network losses"},
        {"type": "last-prompt", "lastPrompt": "and then what"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s1") == "Investigate network losses"


def test_title_uses_last_ai_title_when_several(tmp_path):
    write_transcript(tmp_path, "/p", "s1", [
        {"type": "ai-title", "aiTitle": "First guess"},
        {"type": "ai-title", "aiTitle": "Better title"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s1") == "Better title"


def test_title_falls_back_to_last_prompt(tmp_path):
    write_transcript(tmp_path, "/p", "s2", [
        {"type": "user", "message": {"content": "first message"}},
        {"type": "last-prompt", "lastPrompt": "what about the deploy"},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s2") == "what about the deploy"


def test_title_falls_back_to_first_user_message(tmp_path):
    write_transcript(tmp_path, "/p", "s3", [
        {"type": "assistant", "message": {"content": "hi"}},
        {"type": "user", "message": {"content": "please fix the flaky test"}},
    ])
    assert TitleResolver(tmp_path).title_for("/p", "s3") == "please fix the flaky test"


def test_title_untitled_when_nothing_usable(tmp_path):
    write_transcript(tmp_path, "/p", "s4", [{"type": "system", "content": "boot"}])
    assert TitleResolver(tmp_path).title_for("/p", "s4") == "(untitled)"


def test_title_untitled_when_file_missing(tmp_path):
    assert TitleResolver(tmp_path).title_for("/p", "nope") == "(untitled)"


def test_title_survives_corrupt_lines(tmp_path):
    """A truncated final line is normal for a live session being written to."""
    d = tmp_path / project_slug("/p")
    d.mkdir(parents=True)
    (d / "s5.jsonl").write_text(
        json.dumps({"type": "ai-title", "aiTitle": "Good title"}) + "\n" + '{"type": "assist')
    assert TitleResolver(tmp_path).title_for("/p", "s5") == "Good title"


def test_title_is_cached(tmp_path):
    f = write_transcript(tmp_path, "/p", "s6", [{"type": "ai-title", "aiTitle": "Original"}])
    r = TitleResolver(tmp_path)
    assert r.title_for("/p", "s6") == "Original"
    f.write_text(json.dumps({"type": "ai-title", "aiTitle": "Changed"}) + "\n")
    assert r.title_for("/p", "s6") == "Original", "second call must not re-read"


def test_title_is_collapsed_to_one_line(tmp_path):
    write_transcript(tmp_path, "/p", "s7", [
        {"type": "last-prompt", "lastPrompt": "line one\nline two\n\nline three"}])
    assert TitleResolver(tmp_path).title_for("/p", "s7") == "line one line two line three"


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------

def test_read_panes_keeps_only_claude_panes(tmp_path):
    d = write_snapshot(str(tmp_path), "s", "2026-08-03 16:00:00", [CLAUDE_ROW, SHELL_ROW])
    panes = read_panes(d)
    assert list(panes) == [PaneKey("setup", 1, 0, "/home/e/Setup")]
    assert panes[PaneKey("setup", 1, 0, "/home/e/Setup")] == "aaaa1111"


def test_read_panes_handles_missing_session_id(tmp_path):
    row = ("setup", 1, "claude", "layout-a", 0, "/home/e/Setup", 1, 1, "")
    d = write_snapshot(str(tmp_path), "s", "2026-08-03 16:00:00", [row])
    assert read_panes(d)[PaneKey("setup", 1, 0, "/home/e/Setup")] == ""


def test_read_panes_on_missing_file_is_empty(tmp_path):
    assert read_panes(tmp_path / "nope") == {}


def test_read_panes_ignores_malformed_rows(tmp_path):
    d = tmp_path / "s"
    (d / "pane_contents").mkdir(parents=True)
    (d / "state.tsv").write_text("setup\tnot-a-number\tclaude\tl\t0\t/x\t1\t1\tid\n")
    (d / "saved_at").write_text("2026-08-03 16:00:00\n")
    assert read_panes(d) == {}


def test_list_snapshots_live_first_then_history_newest_first(tmp_path):
    save_dir = tmp_path / "save"
    history = tmp_path / "history"
    history.mkdir()
    write_snapshot(str(tmp_path), "save", "2026-08-03 16:00:00", [CLAUDE_ROW], origin="manual")
    write_snapshot(str(history), "2026-08-01_12-00-00", "2026-08-01 12:00:00", [CLAUDE_ROW], origin="cron")
    write_snapshot(str(history), "2026-08-03_12-00-00", "2026-08-03 12:00:00", [CLAUDE_ROW], origin="cron")

    snaps = list_snapshots(save_dir, history)

    assert [s.path.name for s in snaps] == ["save", "2026-08-03_12-00-00", "2026-08-01_12-00-00"]
    assert snaps[0].origin == "manual"
    assert snaps[0].saved_at == "2026-08-03 16:00:00"


def test_list_snapshots_marks_unmarked_origin_unknown(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    write_snapshot(str(history), "2026-08-01_12-00-00", "2026-08-01 12:00:00", [CLAUDE_ROW])
    assert list_snapshots(tmp_path / "nosave", history)[0].origin == "unknown"


def test_list_snapshots_skips_incomplete_dirs(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "half-written").mkdir()
    write_snapshot(str(history), "2026-08-01_12-00-00", "2026-08-01 12:00:00", [CLAUDE_ROW])
    assert [s.path.name for s in list_snapshots(tmp_path / "nosave", history)] == ["2026-08-01_12-00-00"]


# --------------------------------------------------------------------------
# candidates + assignment  (the bug this feature exists to fix)
# --------------------------------------------------------------------------

def test_two_panes_in_one_cwd_do_not_swap(tmp_path):
    """The regression this whole feature exists to fix.

    Both panes live in /home/e/Alfred. cc is the newer file and belongs to P1.
    The old greedy walk gave cc to P0 (first in file order) and pushed P1 onto
    c1, swapping the two conversations.
    """
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("cc", 2000)])
    source = {P0: "c1", P1: "cc"}
    assert assign_most_recent(source, build_candidates(source, [], tmp_path)) == {P0: "c1", P1: "cc"}


def test_live_pane_keeps_its_own_conversation_over_a_newer_stranger(tmp_path):
    """A newer conversation nobody ever held must not displace a live pane.

    Project dirs fill with /security-review runs and orphans from closed panes.
    On the 2026-08-03 crash restore a 49-line security review took a pane and
    dropped a 1.4MB real conversation.
    """
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("brand-new", 5000)])
    source = {P0: "c1"}
    assert assign_most_recent(source, build_candidates(source, [], tmp_path)) == {P0: "c1"}


def test_stranger_is_used_when_the_panes_lineage_is_gone(tmp_path):
    """Last resort: better than dropping the pane at a bare shell."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("stranger", 5000)])
    source = {P0: "deleted"}
    assert assign_most_recent(source, build_candidates(source, [], tmp_path)) == {P0: "stranger"}


def test_own_history_beats_a_newer_stranger(tmp_path):
    """Tier 2 outranks tier 3: a conversation this pane held wins over one it never did."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("mine-old", 1000), ("stranger", 5000)])
    older = write_snapshot(str(tmp_path), "hist2", "2026-08-02 10:00:00",
                           [("alfred", 0, "claude", "l", 0, "/home/e/Alfred", 1, 1, "mine-old")])
    source = {P0: "deleted"}
    refs = [SnapshotRef(older, "2026-08-02 10:00:00", "cron")]
    assert assign_most_recent(source, build_candidates(source, refs, tmp_path)) == {P0: "mine-old"}


def test_newest_overall_cannot_displace_another_panes_conversation(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("cc", 9000)])
    source = {P0: "c1", P1: "cc"}
    got = assign_most_recent(source, build_candidates(source, [], tmp_path))
    assert got[P0] == "c1"
    assert got[P1] == "cc"


def test_pane_whose_conversation_is_gone_falls_back(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("survivor", 1000)])
    source = {P0: "deleted-id"}
    assert assign_most_recent(source, build_candidates(source, [], tmp_path)) == {P0: "survivor"}


def test_pane_with_no_candidates_at_all_gets_empty(tmp_path):
    source = {P0: "deleted-id"}
    assert assign_most_recent(source, build_candidates(source, [], tmp_path)) == {P0: ""}


def test_candidates_include_ids_from_older_snapshots(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("old", 500), ("now", 3000)])
    older = write_snapshot(str(tmp_path), "hist", "2026-08-02 10:00:00",
                           [("alfred", 0, "claude", "l", 0, "/home/e/Alfred", 1, 1, "old")])
    cands = build_candidates({P0: "now"}, [SnapshotRef(older, "2026-08-02 10:00:00", "cron")], tmp_path)
    assert [c.session_id for c in cands[P0]] == ["now", "old"]


def test_candidates_are_sorted_newest_first(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("a", 100), ("b", 300), ("c", 200)])
    assert [c.session_id for c in build_candidates({P0: "a"}, [], tmp_path)[P0]] == ["b", "c", "a"]


def test_missing_candidate_files_are_marked_not_existing(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("real", 100)])
    by_id = {c.session_id: c for c in build_candidates({P0: "ghost"}, [], tmp_path)[P0]}
    assert by_id["ghost"].exists is False
    assert by_id["real"].exists is True


def test_candidates_flag_lineage_vs_stranger(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("mine", 100), ("stranger", 200)])
    by_id = {c.session_id: c for c in build_candidates({P0: "mine"}, [], tmp_path)[P0]}
    assert by_id["mine"].lineage is True
    assert by_id["stranger"].lineage is False


def test_assignment_is_deterministic_regardless_of_dict_order(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000), ("cc", 2000)])
    forward = {P0: "c1", P1: "cc"}
    backward = {P1: "cc", P0: "c1"}
    a = assign_most_recent(forward, build_candidates(forward, [], tmp_path))
    b = assign_most_recent(backward, build_candidates(backward, [], tmp_path))
    assert a == b


# --------------------------------------------------------------------------
# columns
# --------------------------------------------------------------------------

def test_column_one_is_always_present_and_labelled_most_recent(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000)])
    source = {P0: "c1"}
    cols = build_columns(source, build_candidates(source, [], tmp_path), [])
    assert cols[0].key == "1"
    assert cols[0].label == "most recent"
    assert cols[0].assignment == {P0: "c1"}


def test_change_driven_column_is_the_newest_snapshot_that_differs(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now"}
    same = snap(tmp_path, "h1", "2026-08-03 12:00:00", {P0: "now"})
    differs = snap(tmp_path, "h2", "2026-08-02 12:00:00", {P0: "old"})
    cols = build_columns(source, build_candidates(source, [same, differs], tmp_path), [same, differs])
    assert [c.key for c in cols] == ["1", "2"]
    assert cols[1].assignment == {P0: "old"}
    assert "2026" not in cols[1].label and "08-02" in cols[1].label


def test_no_change_driven_column_when_history_all_agrees(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000)])
    source = {P0: "now"}
    same = snap(tmp_path, "h1", "2026-08-03 12:00:00", {P0: "now"})
    cols = build_columns(source, build_candidates(source, [same], tmp_path), [same])
    assert [c.key for c in cols] == ["1"], "an all-= column must not be rendered"


def test_manual_column_appears_when_a_manual_snapshot_differs(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("checkpoint", 1000)])
    source = {P0: "now"}
    manual = snap(tmp_path, "h1", "2026-08-02 18:45:00", {P0: "checkpoint"}, origin="manual")
    cols = build_columns(source, build_candidates(source, [manual], tmp_path), [manual])
    assert any("manual" in c.label for c in cols), [c.label for c in cols]


def test_no_manual_column_when_no_marked_snapshot_exists(tmp_path):
    """All pre-marker snapshots read as unknown, so this is the common case."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now"}
    unknown = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "old"}, origin="unknown")
    cols = build_columns(source, build_candidates(source, [unknown], tmp_path), [unknown])
    assert not any("manual" in c.label for c in cols)


def test_duplicate_columns_are_not_added_twice(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now"}
    a = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "old"})
    b = snap(tmp_path, "h2", "2026-08-01 12:00:00", {P0: "old"})
    cols = build_columns(source, build_candidates(source, [a, b], tmp_path), [a, b], max_history=5)
    assignments = [str(c.assignment) for c in cols]
    assert len(assignments) == len(set(assignments)), assignments


def test_max_history_adds_more_columns(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("a", 2000), ("b", 1000)])
    source = {P0: "now"}
    s1 = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "a"})
    s2 = snap(tmp_path, "h2", "2026-08-01 12:00:00", {P0: "b"})
    one = build_columns(source, build_candidates(source, [s1, s2], tmp_path), [s1, s2])
    two = build_columns(source, build_candidates(source, [s1, s2], tmp_path), [s1, s2], max_history=2)
    assert [c.key for c in one] == ["1", "2"]
    assert [c.key for c in two] == ["1", "2", "3"]


def test_pane_absent_from_a_snapshot_maps_to_empty(tmp_path):
    make_transcripts(tmp_path, "/home/e/Alfred", [("now", 3000), ("old", 1000)])
    source = {P0: "now", P1: "old"}
    partial = snap(tmp_path, "h1", "2026-08-02 12:00:00", {P0: "old"})
    cols = build_columns(source, build_candidates(source, [partial], tmp_path), [partial])
    assert cols[1].assignment[P1] == ""


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def test_render_collapses_agreeing_panes():
    cols = [Column("1", "most recent", {P0: "a", P1: "b"}),
            Column("2", "08-02 12:00", {P0: "a", P1: "b"})]
    out = render(cols, FakeTitles({"a": "Alpha work", "b": "Beta work"}))
    assert "2 panes agree" in out
    assert "Alpha work" not in out, "agreeing panes must be collapsed, not listed"


def test_render_expands_differing_panes_with_titles():
    cols = [Column("1", "most recent", {P0: "a", P1: "b"}),
            Column("2", "08-02 12:00", {P0: "a", P1: "older"})]
    out = render(cols, FakeTitles({"a": "Alpha", "b": "Beta", "older": "Yesterday work"}))
    assert "1 pane agrees" in out
    assert "Beta" in out
    assert "Yesterday work" in out
    assert "Alpha" not in out


def test_render_marks_identical_cells_with_equals():
    """P1 differs in column 2 but matches column 1 in column 3, so its column 3
    cell must collapse to '=' rather than repeating the id and title."""
    cols = [Column("1", "most recent", {P0: "a", P1: "b"}),
            Column("2", "08-02 12:00", {P0: "a", P1: "older"}),
            Column("3", "08-01 12:00", {P0: "a", P1: "b"})]
    out = render(cols, FakeTitles({}))
    block = out.split("differ")[-1]
    assert "[3]" in block and "=" in block, block


def test_render_marks_absent_pane_with_dash():
    cols = [Column("1", "most recent", {P0: "a"}), Column("2", "08-02 12:00", {P0: ""})]
    out = render(cols, FakeTitles({"a": "Alpha"}))
    assert "-" in out.split("differ")[-1]


def test_render_expand_lists_the_agreeing_panes():
    cols = [Column("1", "most recent", {P0: "a", P1: "b"})]
    out = render(cols, FakeTitles({"a": "Alpha", "b": "Beta"}), expand=True)
    assert "Alpha" in out and "Beta" in out


def test_render_shows_column_headers_and_keys():
    cols = [Column("1", "most recent", {P0: "a"}), Column("2", "08-02 12:00", {P0: "z"})]
    out = render(cols, FakeTitles({}))
    assert "[1]" in out and "[2]" in out
    assert "most recent" in out and "08-02 12:00" in out


def test_render_truncates_long_titles_to_width():
    cols = [Column("1", "most recent", {P0: "a"}), Column("2", "08-02 12:00", {P0: "z"})]
    out = render(cols, FakeTitles({"a": "x" * 400, "z": "y" * 400}), width=100)
    assert all(len(line) <= 100 for line in out.splitlines()), \
        [line for line in out.splitlines() if len(line) > 100]


def test_render_single_column_says_nothing_to_decide():
    out = render([Column("1", "most recent", {P0: "a"})], FakeTitles({"a": "Alpha"}))
    assert "1 pane agrees" in out


def test_render_leaves_no_trailing_whitespace():
    """Absent and same cells have no title; the line must not end in spaces."""
    cols = [Column("1", "most recent", {P0: "a"}), Column("2", "08-02 12:00", {P0: ""})]
    out = render(cols, FakeTitles({"a": "Alpha"}))
    assert not any(line != line.rstrip() for line in out.splitlines())


def test_render_handles_zero_panes():
    assert "No Claude panes" in render([Column("1", "most recent", {})], FakeTitles({}))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_resume_command_uses_the_chosen_id(tmp_path):
    from claude_resume.__main__ import resume_commands
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000)])
    assert dict(resume_commands({P0: "c1"}, tmp_path))[P0] == "claude --resume 'c1'"


def test_resume_command_degrades_to_picker_when_file_is_gone(tmp_path):
    from claude_resume.__main__ import resume_commands
    assert dict(resume_commands({P0: "ghost"}, tmp_path))[P0] == "claude --resume"


def test_resume_command_skips_panes_with_no_choice(tmp_path):
    from claude_resume.__main__ import resume_commands
    assert resume_commands({P0: ""}, tmp_path) == []


def test_batch_dry_run_prints_plan_and_exits_zero(tmp_path):
    """--batch --dry-run must not need a tmux server."""
    make_transcripts(tmp_path, "/home/e/Alfred", [("c1", 1000)])
    save = write_snapshot(str(tmp_path), "save", "2026-08-03 16:00:00",
                          [("alfred", 0, "claude", "l", 0, "/home/e/Alfred", 1, 1, "c1")])
    result = subprocess.run(
        ["python3", "-m", "claude_resume", "--snapshot", str(save), "--batch", "--dry-run"],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "CLAUDE_PROJECTS_DIR": str(tmp_path), "PYTHONPATH": str(REPO)})
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "claude --resume 'c1'" in result.stdout


def test_missing_snapshot_exits_one(tmp_path):
    result = subprocess.run(
        ["python3", "-m", "claude_resume", "--snapshot", str(tmp_path / "nope"), "--batch"],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": str(REPO)})
    assert result.returncode == 1
    assert "no" in (result.stdout + result.stderr).lower()


# --------------------------------------------------------------------------
# tsnaps origin column
# --------------------------------------------------------------------------

def test_tsnaps_list_shows_origin(tmp_path):
    save_dir = tmp_path / "save"
    history = tmp_path / "history"
    history.mkdir()
    write_snapshot(str(tmp_path), "save", "2026-08-03 16:00:00",
                   [("alpha", 0, "win", "layout", 0, "/tmp", 1, 1, "aaaa1111")], origin="manual")
    write_snapshot(str(history), "2026-08-03_12-00-00", "2026-08-03 12:00:00",
                   [("alpha", 0, "win", "layout", 0, "/tmp", 1, 1, "aaaa1111")], origin="cron")
    write_snapshot(str(history), "2026-08-02_12-00-00", "2026-08-02 12:00:00",
                   [("alpha", 0, "win", "layout", 0, "/tmp", 1, 1, "aaaa1111")])

    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TMUX_SAVE_DIR": str(save_dir), "TMUX_SAVE_HISTORY_DIR": str(history)}
    result = subprocess.run([str(SNAPS), "--list"], env=env,
                            capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 3
    assert "manual" in lines[0]
    assert "cron" in lines[1]
    assert "?" in lines[2], "unmarked snapshots must render as unknown, not blank"


def test_busy_pane_detection_reads_the_pane_tty(monkeypatch):
    """A pane already running Claude must be detected so we never type the
    resume command into a live session as a chat message."""
    import claude_resume.__main__ as m

    class R:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(m, "_tmux", lambda argv, socket: R("0 /dev/pts/9\n1 /dev/pts/7\n"))
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: R("zsh\nnode\n/usr/bin/claude\n"))
    assert m.pane_is_busy(P0, "") is True


def test_idle_pane_is_not_busy(monkeypatch):
    import claude_resume.__main__ as m

    class R:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(m, "_tmux", lambda argv, socket: R("0 /dev/pts/9\n"))
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: R("zsh\nps\n"))
    assert m.pane_is_busy(P0, "") is False


def test_pane_with_no_matching_tty_is_not_busy(monkeypatch):
    import claude_resume.__main__ as m

    class R:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(m, "_tmux", lambda argv, socket: R("5 /dev/pts/9\n"))
    assert m.pane_is_busy(P0, "") is False

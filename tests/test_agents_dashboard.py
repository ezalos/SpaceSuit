# ABOUTME: Tests for the agents.develle.fr dashboard collector, classifier and renderer.
# ABOUTME: Fixture-driven: recorded transcript tails and pane captures in, states out.
import json
import logging
import subprocess
import time
from pathlib import Path

import pytest

from agents_dashboard.classify import (
    URGENCY,
    classify_phase,
    map_activity,
    urgency_rank,
)
from agents_dashboard.models import (
    Activity,
    Phase,
    PhaseSignal,
    WaitingReason,
    PaneRecord,
    TaskProgress,
    PhaseEvidence,
    Snapshot,
    SessionCard,
    WindowRecord,
)
from agents_dashboard.termview import RESET, DIM


def sig(name, kind="skill"):
    return PhaseSignal(kind=kind, name=name)


class TestClassifyPhase:
    def test_plan_mode_overrides_everything(self):
        # Even with a wrap-up as the most recent event, plan mode wins outright.
        signals = [sig("brainstorming"), sig("wrap-up")]
        assert classify_phase(signals, mode="plan") == Phase.DESIGN

    def test_no_signals_is_unknown(self):
        assert classify_phase([], mode="normal") == Phase.UNKNOWN

    def test_a_skill_outranks_later_edits(self):
        # Measured: edits are constant and skills are rare, so letting edits win
        # on recency classified all 24 live sessions as implem. Wrapping up
        # involves editing, so the edits are part of the wrap-up.
        signals = [sig("wrap-up"), sig("edit_burst", kind="edit_burst")]
        assert classify_phase(signals, mode="normal") == Phase.WRAP_UP

    def test_wrap_up_after_implem_reads_as_wrap_up(self):
        signals = [sig("edit_burst", kind="edit_burst"), sig("wrap-up")]
        assert classify_phase(signals, mode="normal") == Phase.WRAP_UP

    def test_most_recent_skill_wins_among_skills(self):
        signals = [sig("brainstorming"), sig("requesting-code-review")]
        assert classify_phase(signals, mode="normal") == Phase.REVIEW

    def test_edits_are_the_fallback_when_no_skill_is_mapped(self):
        signals = [sig("share-file"), sig("edit_burst", kind="edit_burst")]
        assert classify_phase(signals, mode="normal") == Phase.IMPLEM

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("wrap-up", Phase.WRAP_UP),
            ("finishing-a-development-branch", Phase.WRAP_UP),
            ("requesting-code-review", Phase.REVIEW),
            ("code-review", Phase.REVIEW),
            ("security-review", Phase.REVIEW),
            ("brainstorming", Phase.DESIGN),
            ("writing-plans", Phase.DESIGN),
            ("executing-plans", Phase.IMPLEM),
            ("test-driven-development", Phase.IMPLEM),
            ("subagent-driven-development", Phase.IMPLEM),
        ],
    )
    def test_each_skill_maps_to_its_phase(self, name, expected):
        assert classify_phase([sig(name)], mode="normal") == expected

    def test_unmapped_skill_is_ignored_not_guessed(self):
        # A skill we have no rule for must not silently become a phase.
        assert classify_phase([sig("proton-drive")], mode="normal") == Phase.UNKNOWN

    def test_unmapped_skill_does_not_mask_an_earlier_real_signal(self):
        signals = [sig("brainstorming"), sig("proton-drive")]
        assert classify_phase(signals, mode="normal") == Phase.DESIGN

    def test_edit_burst_maps_to_implem(self):
        assert classify_phase([sig("edit_burst", kind="edit_burst")], "normal") == Phase.IMPLEM


class TestMapActivity:
    def test_busy_is_working(self):
        assert map_activity("busy") == Activity.WORKING

    def test_shell_is_working(self):
        # Decided in the spec: shell needs no attention, so it is never flagged.
        assert map_activity("shell") == Activity.WORKING

    def test_idle_is_waiting(self):
        assert map_activity("idle") == Activity.WAITING

    def test_unknown_status_is_working_so_it_is_never_falsely_flagged(self):
        # Fail safe: an unrecognised status must not invent a blocker.
        assert map_activity("something-new") == Activity.WORKING

    def test_non_string_status_is_working_not_a_crash(self):
        # A malformed session file can smuggle a non-string (e.g. a list) into
        # status. dict.get() on an unhashable key raises TypeError before its
        # own default applies, so this must be guarded explicitly rather than
        # relying on ACTIVITY_BY_STATUS.get(status, ...) alone.
        assert map_activity(["idle"]) == Activity.WORKING


class TestUrgency:
    def test_ordering_is_permission_question_unsent_idle(self):
        order = sorted(URGENCY, key=urgency_rank)
        assert order == [
            WaitingReason.PERMISSION,
            WaitingReason.QUESTION,
            WaitingReason.UNSENT_INPUT,
            WaitingReason.IDLE,
        ]


from agents_dashboard import transcripts


def write_jsonl(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def skill_entry(name, caller="direct"):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Skill",
                    "input": {"skill": name},
                    "caller": {"type": caller},
                }
            ]
        },
    }


def edit_entry(n=1):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": f"/tmp/f{i}"}}
                for i in range(n)
            ]
        },
    }


class TestReadTail:
    def test_reads_all_entries_of_a_small_file(self, tmp_path):
        p = write_jsonl(tmp_path / "t.jsonl", [{"type": "a"}, {"type": "b"}])
        assert [e["type"] for e in transcripts.read_tail(p)] == ["a", "b"]

    def test_never_reads_more_than_max_bytes(self, tmp_path):
        # 5 MB of filler, then the entries we care about. A full-file parse would
        # pass every other test in this class while being the actual bug.
        p = tmp_path / "big.jsonl"
        with p.open("w") as fh:
            filler = {"type": "filler", "pad": "x" * 1000}
            for _ in range(5000):
                fh.write(json.dumps(filler) + "\n")
            fh.write(json.dumps({"type": "wanted"}) + "\n")
        entries = transcripts.read_tail(p, max_bytes=8192)
        assert entries[-1]["type"] == "wanted"
        assert len(entries) < 100  # nowhere near the 5001 lines in the file

    def test_discards_leading_partial_line(self, tmp_path):
        # Two entries, 37 bytes total. A 25-byte window starts at offset 12,
        # mid-way through the first line, so that fragment must be dropped and
        # exactly the second entry survives.
        p = write_jsonl(tmp_path / "t.jsonl", [{"type": "first"}, {"type": "second"}])
        assert p.stat().st_size == 37
        entries = transcripts.read_tail(p, max_bytes=25)
        assert [e["type"] for e in entries] == ["second"]

    def test_skips_unparseable_lines_without_raising(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text(json.dumps({"type": "a"}) + "\nnot json\n" + json.dumps({"type": "b"}) + "\n")
        assert [e["type"] for e in transcripts.read_tail(p)] == ["a", "b"]

    def test_missing_file_returns_empty_not_raises(self, tmp_path):
        assert transcripts.read_tail(tmp_path / "nope.jsonl") == []


class TestExtract:
    def test_collects_skill_signals_in_file_order(self):
        info = transcripts.extract([skill_entry("superpowers:brainstorming"),
                                    skill_entry("superpowers:wrap-up")])
        assert [s.name for s in info.signals] == ["brainstorming", "wrap-up"]

    def test_strips_the_namespace_from_skill_names(self):
        info = transcripts.extract([skill_entry("mattpocock-skills:code-review")])
        assert info.signals[0].name == "code-review"

    def test_ignores_skills_invoked_by_a_subagent(self):
        # A subagent's choice of skill is not the main thread's phase.
        info = transcripts.extract([skill_entry("superpowers:brainstorming", caller="agent")])
        assert info.signals == []

    def test_three_edits_in_one_turn_is_an_edit_burst(self):
        info = transcripts.extract([edit_entry(3)])
        assert [s.kind for s in info.signals] == ["edit_burst"]

    def test_two_edits_is_not_a_burst(self):
        info = transcripts.extract([edit_entry(2)])
        assert info.signals == []

    def test_reads_mode_title_and_branch(self):
        # Real shape: plan mode is recorded as permission-mode/permissionMode,
        # never as type:mode/mode (the fabricated shape this replaces never
        # occurs on the live corpus - see extract()'s docstring evidence).
        info = transcripts.extract([
            {"type": "permission-mode", "permissionMode": "plan"},
            {"type": "ai-title", "aiTitle": "Build the dashboard"},
            {"type": "assistant", "gitBranch": "master", "message": {"model": "claude-opus-5"}},
        ])
        assert info.mode == "plan"
        assert info.title == "Build the dashboard"
        assert info.git_branch == "master"
        assert info.model == "claude-opus-5"

    def test_last_mode_entry_wins(self):
        # type:mode's real observed values are "normal" and "content" - never
        # "plan" - so this exercises last-one-wins with realistic values.
        info = transcripts.extract([{"type": "mode", "mode": "content"},
                                    {"type": "mode", "mode": "normal"}])
        assert info.mode == "normal"

    def test_last_mode_entry_wins_across_both_entry_types(self):
        # type:mode and permission-mode feed the same info.mode field, so the
        # last one by file order wins regardless of which entry type it is.
        info = transcripts.extract([{"type": "permission-mode", "permissionMode": "plan"},
                                    {"type": "mode", "mode": "normal"}])
        assert info.mode == "normal"

    def test_permission_mode_plan_yields_design_phase(self):
        # The deterministic override, exercised end-to-end: transcripts.extract
        # reads the real permission-mode/permissionMode shape and classify_phase
        # honours it as plan mode, even though a wrap-up skill is also present.
        info = transcripts.extract([
            {"type": "permission-mode", "permissionMode": "plan"},
            skill_entry("superpowers:wrap-up"),
        ])
        assert info.mode == "plan"
        assert classify_phase(info.signals, info.mode) == Phase.DESIGN

    def test_permission_mode_auto_returns_to_normal_classification(self):
        # A later permission-mode: auto entry must un-latch the plan override,
        # so classification falls through to the ordinary skill/edit rules.
        info = transcripts.extract([
            {"type": "permission-mode", "permissionMode": "plan"},
            skill_entry("superpowers:wrap-up"),
            {"type": "permission-mode", "permissionMode": "auto"},
        ])
        assert info.mode == "auto"
        assert classify_phase(info.signals, info.mode) == Phase.WRAP_UP

    def test_absent_caller_key_treated_as_direct(self):
        # When caller key is fully missing, it defaults to direct and signal is collected.
        info = transcripts.extract([{
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "superpowers:brainstorming"},
                    }
                ]
            },
        }])
        assert [s.name for s in info.signals] == ["brainstorming"]

    def test_edits_spread_across_entries_still_burst(self):
        # Claude emits roughly one tool call per entry, so counting edits only
        # within a single entry meant the burst essentially never fired. A live
        # session with 13 edits in its tail produced zero signals.
        info = transcripts.extract([edit_entry(1), edit_entry(1), edit_entry(1)])
        assert [s.kind for s in info.signals] == ["edit_burst"]

    def test_burst_sits_at_the_last_edit_so_a_later_skill_wins(self):
        entries = [edit_entry(1), edit_entry(1), edit_entry(1),
                   skill_entry("superpowers:wrap-up")]
        info = transcripts.extract(entries)
        assert [s.kind for s in info.signals] == ["edit_burst", "skill"]

    def test_skill_before_a_burst_is_ordered_first(self):
        entries = [skill_entry("superpowers:brainstorming"),
                   edit_entry(1), edit_entry(1), edit_entry(1)]
        info = transcripts.extract(entries)
        assert [s.kind for s in info.signals] == ["skill", "edit_burst"]

    def test_burst_position_reflects_the_last_edit_not_just_presence(self):
        # Discriminating case: the skill sits between edits rather than
        # entirely before or entirely after all of them, so only positioning
        # the burst at edit_indices[-1] (not the first or any middle edit)
        # produces this order.
        entries = [edit_entry(1), skill_entry("superpowers:wrap-up"),
                   edit_entry(1), edit_entry(1)]
        info = transcripts.extract(entries)
        assert [s.kind for s in info.signals] == ["skill", "edit_burst"]


class TestReadForPhase:
    def _write(self, path, entries):
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))

    def test_reads_deeper_than_the_tail_so_early_skills_are_found(self, tmp_path):
        # Signal at the very start, then enough filler to push it outside the
        # 256 KB tail - the real shape that left every live session unknown.
        p = tmp_path / "t.jsonl"
        filler = {"type": "user", "pad": "x" * 500}
        self._write(p, [skill_entry("superpowers:writing-plans")] + [filler] * 40)
        tail_only = transcripts.extract(transcripts.read_tail(p, max_bytes=2048))
        assert tail_only.signals == []  # the bug this fixes
        info = transcripts.read_for_phase(p, max_bytes=10_000_000)
        assert [s.name for s in info.signals] == ["writing-plans"]

    def test_a_window_too_small_gives_an_honest_unknown(self, tmp_path):
        p = tmp_path / "t.jsonl"
        filler = {"type": "user", "pad": "x" * 500}
        self._write(p, [skill_entry("superpowers:writing-plans")] + [filler] * 40)
        info = transcripts.read_for_phase(p, max_bytes=1024)
        assert info.signals == []  # bounded read, not an unbounded hunt

    def test_memoises_on_size_so_an_idle_session_is_read_once(self, tmp_path):
        p = tmp_path / "t.jsonl"
        self._write(p, [skill_entry("superpowers:brainstorming")])
        first = transcripts.read_for_phase(p, max_bytes=10_000_000)
        second = transcripts.read_for_phase(p, max_bytes=10_000_000)
        assert first is second  # same object: served from the cache


from agents_dashboard import claude_sessions


def session_file(dir_, pid, **overrides):
    payload = {
        "pid": pid,
        "sessionId": f"sid-{pid}",
        "cwd": "/home/ezalos/42/Alfred",
        "status": "idle",
        "statusUpdatedAt": 1785745647725,  # milliseconds, as Claude Code writes it
        "name": f"alfred-{pid}",
        "kind": "interactive",
    }
    payload.update(overrides)
    (dir_ / f"{pid}.json").write_text(json.dumps(payload))


class TestLoadAll:
    def test_loads_sessions_keyed_by_pid(self, tmp_path):
        session_file(tmp_path, 111)
        session_file(tmp_path, 222, status="busy")
        loaded = claude_sessions.load_all(tmp_path)
        assert set(loaded) == {111, 222}
        assert loaded[222].status == "busy"

    def test_converts_status_timestamp_from_ms_to_seconds(self, tmp_path):
        session_file(tmp_path, 111, statusUpdatedAt=1785745647725)
        assert claude_sessions.load_all(tmp_path)[111].status_updated_at == 1785745647.725

    def test_skips_corrupt_files_without_raising(self, tmp_path):
        session_file(tmp_path, 111)
        (tmp_path / "222.json").write_text("{ not json")
        assert set(claude_sessions.load_all(tmp_path)) == {111}

    def test_missing_directory_returns_empty(self, tmp_path):
        assert claude_sessions.load_all(tmp_path / "nope") == {}

    def test_non_numeric_status_updated_at_is_coerced_not_fatal(self, tmp_path):
        # Confirmed live: (data.get("statusUpdatedAt") or 0) / 1000.0 raised
        # TypeError for a string value, taking out every session in the file.
        session_file(tmp_path, 111, statusUpdatedAt="not-a-number")
        session_file(tmp_path, 222)
        loaded = claude_sessions.load_all(tmp_path)
        assert set(loaded) == {111, 222}
        assert loaded[111].status_updated_at == 0.0

    def test_non_string_status_is_coerced_not_fatal(self, tmp_path):
        # A status field that is a list (or any non-string) must not survive
        # into ClaudeSession.status, since map_activity would later crash on
        # an unhashable dict key.
        session_file(tmp_path, 111, status=["idle"])
        session_file(tmp_path, 222)
        loaded = claude_sessions.load_all(tmp_path)
        assert set(loaded) == {111, 222}
        assert loaded[111].status == ""

    def test_non_dict_json_payload_is_skipped_not_fatal(self, tmp_path):
        # Valid JSON, but the top-level value isn't a session object at all.
        (tmp_path / "333.json").write_text(json.dumps([1, 2, 3]))
        session_file(tmp_path, 111)
        loaded = claude_sessions.load_all(tmp_path)
        assert set(loaded) == {111}


class TestTranscriptPath:
    def _session(self, cwd="/home/ezalos/42/Alfred", sid="abc"):
        return claude_sessions.ClaudeSession(
            pid=1, session_id=sid, cwd=cwd,
            status="idle", status_updated_at=0.0, name="x",
        )

    def test_encodes_cwd_as_claude_project_slug(self, tmp_path):
        path = claude_sessions.transcript_path(self._session(), tmp_path)
        assert path == tmp_path / "-home-ezalos-42-Alfred" / "abc.jsonl"

    def test_underscores_become_dashes_like_slashes_do(self, tmp_path):
        # Verified against the live machine: cwd /home/ezalos/Work/web_wm_onnx
        # lives in the directory -home-ezalos-Work-web-wm-onnx. Treating only '/'
        # silently lost every session in that project.
        s = self._session(cwd="/home/ezalos/Work/web_wm_onnx")
        path = claude_sessions.transcript_path(s, tmp_path)
        assert path == tmp_path / "-home-ezalos-Work-web-wm-onnx" / "abc.jsonl"

    def test_project_slug_maps_both_separators(self):
        assert claude_sessions.project_slug("/a/b_c") == "-a-b-c"


class TestFindTranscript:
    def _session(self, cwd="/home/ezalos/42/Alfred", sid="abc"):
        return claude_sessions.ClaudeSession(
            pid=1, session_id=sid, cwd=cwd,
            status="idle", status_updated_at=0.0, name="x",
        )

    def test_uses_the_slug_path_when_it_exists(self, tmp_path):
        d = tmp_path / "-home-ezalos-42-Alfred"
        d.mkdir()
        (d / "abc.jsonl").write_text("{}\n")
        assert claude_sessions.find_transcript(self._session(), tmp_path) == d / "abc.jsonl"

    def test_falls_back_to_session_id_lookup_when_the_slug_is_wrong(self, tmp_path):
        # The session id is a UUID and globally unique, so this finds the
        # transcript however Claude Code chose to name the directory.
        odd = tmp_path / "-some-scheme-we-did-not-predict"
        odd.mkdir()
        (odd / "abc.jsonl").write_text("{}\n")
        assert claude_sessions.find_transcript(self._session(), tmp_path) == odd / "abc.jsonl"

    def test_returns_the_slug_path_when_nothing_is_found(self, tmp_path):
        # Callers treat a non-existent path as "no transcript" and degrade to
        # an unknown phase, so this must not raise.
        found = claude_sessions.find_transcript(self._session(), tmp_path)
        assert found == tmp_path / "-home-ezalos-42-Alfred" / "abc.jsonl"
        assert not found.exists()


from agents_dashboard import tmux


class FakeRunner:
    """Returns canned stdout per command, recording what was asked."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        for key, out in self.responses.items():
            if key in " ".join(argv):
                return out
        return ""


class TestListPanes:
    def test_parses_pane_records(self):
        runner = FakeRunner({
            "list-panes": (
                "alfred@2026:0:0:/home/ezalos/42/Alfred:/dev/pts/5:1785833806:1:1785833791:claude\n"
                "setup@2026:9:0:/home/ezalos/Setup:/dev/pts/9:1785833806:1:1785833791:zsh\n"
            )
        })
        panes = tmux.list_panes(runner=runner)
        assert [p.session for p in panes] == ["alfred@2026", "setup@2026"]
        assert panes[1].window_index == 9
        assert panes[1].tty == "/dev/pts/9"

    def test_no_tmux_server_returns_empty(self):
        assert tmux.list_panes(runner=FakeRunner({})) == []

    def test_ignores_malformed_lines(self):
        runner = FakeRunner({"list-panes": "garbage\nalfred:0:0:/tmp:/dev/pts/1:1785833806:1:1785833791:zsh\n"})
        assert len(tmux.list_panes(runner=runner)) == 1


class TestListPanesRicherFields:
    LINE = ("alfred@2026-08-01:1:0:/home/ezalos/42/Alfred:/dev/pts/5"
            ":1785833806:1:1785833791:claude")

    def test_parses_the_new_fields(self):
        panes = tmux.list_panes(runner=FakeRunner({"list-panes": self.LINE + "\n"}))
        assert len(panes) == 1
        p = panes[0]
        assert p.session == "alfred@2026-08-01"
        assert p.window_index == 1
        assert p.cwd == "/home/ezalos/42/Alfred"
        assert p.tty == "/dev/pts/5"
        assert p.command == "claude"
        assert p.quiet_since == 1785833806.0
        assert p.session_attached is True
        assert p.session_activity == 1785833791.0

    def test_detached_session_parses_as_false(self):
        line = self.LINE.replace(":1:1785833791:claude", ":0:1785833791:claude")
        assert tmux.list_panes(runner=FakeRunner({"list-panes": line + "\n"}))[0].session_attached is False

    def test_session_name_containing_colons_survives(self):
        # rsplit from the right: only the trailing fields have fixed positions.
        line = "we:ird:name:2:0:/tmp:/dev/pts/9:1785833806:0:1785833791:zsh"
        p = tmux.list_panes(runner=FakeRunner({"list-panes": line + "\n"}))[0]
        assert p.session == "we:ird:name"
        assert p.command == "zsh"

    def test_malformed_line_is_skipped_not_fatal(self):
        runner = FakeRunner({"list-panes": "garbage\n" + self.LINE + "\n"})
        assert len(tmux.list_panes(runner=runner)) == 1

    def test_defaults_keep_positional_construction_working(self):
        # Earlier tasks' tests build TmuxPane with five positional args.
        p = tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")
        assert p.command == "" and p.quiet_since == 0.0
        assert p.session_attached is False and p.session_activity == 0.0


class TestClaudePidForTty:
    def test_finds_claude_at_any_depth(self):
        runner = FakeRunner({"ps": "  1234 zsh\n  5678 claude\n"})
        assert tmux.claude_pid_for_tty("/dev/pts/5", runner=runner) == 5678

    def test_matches_an_absolute_path_to_claude(self):
        runner = FakeRunner({"ps": "  5678 /usr/local/bin/claude\n"})
        assert tmux.claude_pid_for_tty("/dev/pts/5", runner=runner) == 5678

    def test_returns_none_when_no_claude_on_the_tty(self):
        runner = FakeRunner({"ps": "  1234 zsh\n"})
        assert tmux.claude_pid_for_tty("/dev/pts/5", runner=runner) is None

    def test_does_not_match_a_lookalike_command(self):
        runner = FakeRunner({"ps": "  1234 claude-log\n"})
        assert tmux.claude_pid_for_tty("/dev/pts/5", runner=runner) is None

    def test_strips_dev_prefix_when_calling_ps(self):
        runner = FakeRunner({"ps": ""})
        tmux.claude_pid_for_tty("/dev/pts/5", runner=runner)
        assert "pts/5" in " ".join(runner.calls[0])
        assert "/dev/pts/5" not in " ".join(runner.calls[0])

    def test_does_not_match_path_shaped_lookalike(self):
        runner = FakeRunner({"ps": "  1234 /usr/bin/claude-log\n"})
        assert tmux.claude_pid_for_tty("/dev/pts/5", runner=runner) is None


class TestSubprocessRunner:
    def test_returns_stdout_on_success(self):
        result = tmux.subprocess_runner(["echo", "hi"])
        assert result == "hi\n"

    def test_returns_empty_on_non_zero_exit(self):
        result = tmux.subprocess_runner(["sh", "-c", "exit 3"])
        assert result == ""

    def test_returns_empty_on_command_not_found(self):
        result = tmux.subprocess_runner(["definitely-not-a-real-binary-xyz"])
        assert result == ""

    def test_returns_empty_on_timeout(self, monkeypatch):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("cmd", 10)
        monkeypatch.setattr(tmux.subprocess, "run", raise_timeout)
        result = tmux.subprocess_runner(["sleep", "1"])
        assert result == ""


class TestListSessions:
    def test_parses_session_names(self):
        runner = FakeRunner({
            "list-sessions": "alfred@2026\nsetup@2026\n"
        })
        sessions = tmux.list_sessions(runner=runner)
        assert sessions == ["alfred@2026", "setup@2026"]

    def test_drops_blank_lines(self):
        runner = FakeRunner({
            "list-sessions": "alfred@2026\n\nsetup@2026\n"
        })
        sessions = tmux.list_sessions(runner=runner)
        assert sessions == ["alfred@2026", "setup@2026"]


class TestCapturPane:
    def test_builds_target_and_passes_line_count(self):
        runner = FakeRunner({"capture-pane": "some output"})
        result = tmux.capture_pane("mysession", 3, 2, lines=50, runner=runner)
        assert result == "some output"
        assert runner.calls[0] == ["tmux", "capture-pane", "-t", "mysession:3.2", "-p", "-S", "-50"]

    def test_uses_default_line_count(self):
        runner = FakeRunner({"capture-pane": ""})
        tmux.capture_pane("s", 1, 0, runner=runner)
        assert "-30" in runner.calls[0]


from agents_dashboard import panescan

PERMISSION_PANE = """\
 Latest blocked action: Blocked by classifier

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for: cd *
   3. No

 Esc to cancel · Tab to amend · ctrl+e to explain
"""

IDLE_PANE = """\
✻ Cogitated for 1m 7s
────────────────────
❯
────────────────────
  \U0001f9e0 Opus 5 | \U0001f4c1 Setup | \U0001f33f master
"""

UNSENT_PANE = """\
✻ Cogitated for 1m 59s
────────────────────
❯ keep it as is, tighten the shared codec wrinkle
────────────────────
  \U0001f9e0 Opus 4.8 | \U0001f4c1 web_wm_onnx | \U0001f33f master
"""

MENU_CHOICE_ONLY_PANE = """\
❯ 2. No
"""

QUESTION_ONLY_PANE = """\
Do you want to proceed?
"""


class TestPaneScan:
    def test_detects_a_pending_permission_prompt(self):
        assert panescan.scan(PERMISSION_PANE) == WaitingReason.PERMISSION

    def test_detects_unsent_input(self):
        assert panescan.scan(UNSENT_PANE) == WaitingReason.UNSENT_INPUT

    def test_empty_prompt_box_is_not_unsent_input(self):
        assert panescan.scan(IDLE_PANE) is None

    def test_permission_outranks_unsent_input(self):
        assert panescan.scan(PERMISSION_PANE + UNSENT_PANE) == WaitingReason.PERMISSION

    def test_unrecognised_pane_returns_none_rather_than_guessing(self):
        assert panescan.scan("some completely different program\n") is None

    def test_empty_pane_returns_none(self):
        assert panescan.scan("") is None

    def test_menu_choice_without_question_is_not_unsent_input(self):
        # Catches deletion of: if re.match(r"^\d+\.\s", rest): continue
        # Menu lines also start with ❯, so without the guard this returns UNSENT_INPUT.
        assert panescan.scan(MENU_CHOICE_ONLY_PANE) is None

    def test_question_without_choices_is_not_permission(self):
        # Catches mutation of AND to OR in _has_permission_prompt.
        # With OR, the question line alone would return PERMISSION.
        assert panescan.scan(QUESTION_ONLY_PANE) is None


from agents_dashboard import collect
from agents_dashboard.claude_sessions import ClaudeSession
from agents_dashboard.tmux import TmuxPane


def build(panes, sessions, pid_lookup, transcripts_by_sid=None, panes_text=None, now=1000.0):
    transcripts_by_sid = transcripts_by_sid or {}
    panes_text = panes_text or {}
    return collect.build_snapshot(
        now=now,
        panes=panes,
        sessions=sessions,
        pid_lookup=lambda tty: pid_lookup.get(tty),
        transcript_reader=lambda s: transcripts_by_sid.get(
            s.session_id, transcripts.TranscriptInfo()
        ),
        pane_capturer=lambda s, w, p: panes_text.get((s, w, p), ""),
    )


class TestBuildSnapshot:
    def test_tmux_session_without_claude_is_a_not_started_card(self):
        snap = build([TmuxPane("w-0801", 0, 0, "/tmp", "/dev/pts/1")], {}, {})
        assert len(snap.cards) == 1
        assert snap.cards[0].not_started is True

    def test_claude_pane_becomes_a_record_on_its_card(self):
        panes = [TmuxPane("alfred@x", 1, 0, "/home/ezalos/42/Alfred", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/home/ezalos/42/Alfred", "idle", 900.0, "alfred-b1")}
        snap = build(panes, sessions, {"/dev/pts/5": 42})
        card = snap.cards[0]
        assert card.not_started is False
        assert card.panes[0].session_id == "sid-a"
        assert card.panes[0].attach == "tmux attach -t alfred@x:1.0"

    def test_working_session_is_never_flagged_even_with_unsent_input(self):
        # The governing rule: type-ahead while the agent works is not a blocker.
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "busy", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42},
                     panes_text={("s", 0, 0): UNSENT_PANE})
        rec = snap.cards[0].panes[0]
        assert rec.activity == Activity.WORKING
        assert rec.waiting_reason is None

    def test_shell_status_is_working_and_unflagged(self):
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "shell", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42})
        assert snap.cards[0].panes[0].waiting_reason is None

    def test_working_session_is_never_pane_captured(self):
        # Performance and fragility both depend on this: the most brittle source
        # must not run for the majority of sessions.
        captured = []
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "busy", 900.0, "n")}
        collect.build_snapshot(
            now=1000.0, panes=panes, sessions=sessions,
            pid_lookup=lambda tty: 42,
            transcript_reader=lambda s: transcripts.TranscriptInfo(),
            pane_capturer=lambda s, w, p: captured.append((s, w, p)) or "",
        )
        assert captured == []

    def test_idle_session_with_permission_prompt_is_flagged_permission(self):
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42},
                     panes_text={("s", 0, 0): PERMISSION_PANE})
        rec = snap.cards[0].panes[0]
        assert rec.waiting_reason == WaitingReason.PERMISSION
        assert rec.waiting_since == 900.0

    def test_idle_session_with_a_quiet_pane_is_flagged_idle(self):
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42},
                     panes_text={("s", 0, 0): IDLE_PANE})
        assert snap.cards[0].panes[0].waiting_reason == WaitingReason.IDLE

    def test_phase_comes_from_the_transcript(self):
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "n")}
        info = transcripts.TranscriptInfo(
            signals=[PhaseSignal("skill", "brainstorming")], title="Design the thing"
        )
        snap = build(panes, sessions, {"/dev/pts/5": 42}, transcripts_by_sid={"sid-a": info})
        rec = snap.cards[0].panes[0]
        assert rec.phase == Phase.DESIGN
        assert rec.title == "Design the thing"

    def test_cards_sort_most_urgent_first(self):
        panes = [
            TmuxPane("quiet", 0, 0, "/tmp", "/dev/pts/1"),
            TmuxPane("blocked", 0, 0, "/tmp", "/dev/pts/2"),
        ]
        sessions = {
            1: ClaudeSession(1, "sid-1", "/tmp", "idle", 900.0, "n"),
            2: ClaudeSession(2, "sid-2", "/tmp", "idle", 900.0, "n"),
        }
        snap = build(panes, sessions, {"/dev/pts/1": 1, "/dev/pts/2": 2},
                     panes_text={("blocked", 0, 0): PERMISSION_PANE})
        assert snap.cards[0].name == "blocked"

    def test_cards_tie_break_by_longest_wait(self):
        # Same urgency (idle) on both cards - only waiting_since distinguishes
        # them, and the card waiting since longer ago must sort first.
        panes = [
            TmuxPane("recent", 0, 0, "/tmp", "/dev/pts/1"),
            TmuxPane("longest", 0, 0, "/tmp", "/dev/pts/2"),
        ]
        sessions = {
            1: ClaudeSession(1, "sid-1", "/tmp", "idle", 900.0, "n"),
            2: ClaudeSession(2, "sid-2", "/tmp", "idle", 100.0, "n"),
        }
        snap = build(panes, sessions, {"/dev/pts/1": 1, "/dev/pts/2": 2},
                     panes_text={("recent", 0, 0): IDLE_PANE, ("longest", 0, 0): IDLE_PANE})
        assert snap.cards[0].name == "longest"

    def test_not_started_cards_sort_last(self):
        panes = [
            TmuxPane("empty", 0, 0, "/tmp", "/dev/pts/1"),
            TmuxPane("busy-one", 0, 0, "/tmp", "/dev/pts/2"),
        ]
        sessions = {2: ClaudeSession(2, "sid-2", "/tmp", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/2": 2})
        assert [c.name for c in snap.cards] == ["busy-one", "empty"]


class TestDetectQuestion:
    def test_ask_user_question_in_last_turn_is_a_question(self):
        entries = [{
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "AskUserQuestion", "input": {}}
            ]},
        }]
        assert collect.detect_question(entries) is True

    def test_ordinary_last_turn_is_not_a_question(self):
        entries = [{"type": "assistant", "message": {"content": [
            {"type": "text", "text": "done"}
        ]}}]
        assert collect.detect_question(entries) is False

    def test_only_the_newest_assistant_turn_counts(self):
        # An older turn asking a question must not leak through once a newer,
        # ordinary turn has happened. A regression that checked any assistant
        # turn instead of only the last one would wrongly return True here.
        entries = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "AskUserQuestion", "input": {}}
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "done"}
            ]}},
        ]
        assert collect.detect_question(entries) is False


class TestSnapshotToDict:
    def test_is_json_serialisable(self):
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42})
        json.dumps(collect.snapshot_to_dict(snap))  # must not raise


from agents_dashboard import render


class TestRender:
    def _snapshot(self, **kwargs):
        panes = [TmuxPane("alfred@x", 1, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "alfred-b1")}
        return build(panes, sessions, {"/dev/pts/5": 42}, **kwargs)

    def test_is_a_complete_html_document(self):
        html = render.render(self._snapshot())
        assert html.lstrip().startswith("<!doctype html>")
        assert "</html>" in html

    def test_shows_the_tmux_session_name_and_attach_target(self):
        html = render.render(self._snapshot())
        assert "alfred@x" in html
        assert "tmux attach -t alfred@x:1.0" in html

    def test_stale_banner_markup_is_present_but_hidden_by_default(self):
        # Staleness is now computed client-side (see render.render's
        # docstring), so the banner element always ships in the markup,
        # starting hidden - the inline script un-hides it if the embedded
        # timestamp turns out to be too old once the browser's clock runs.
        html = render.render(self._snapshot())
        assert 'id="stale-banner"' in html
        assert 'class="stale-banner"' in html
        assert "hidden" in html

    def test_page_embeds_generated_at_for_client_side_staleness_check(self):
        snap = self._snapshot()
        html = render.render(snap)
        assert f'data-generated-at="{snap.generated_at!r}"' in html

    def test_stale_threshold_is_120_seconds_in_the_inline_script(self):
        # Pins the threshold rationale in render.SCRIPT itself: live re-collects
        # within a 3s TTL, fallback is pushed every 60s, so 120s of age means
        # data has genuinely stopped updating.
        assert "STALE_THRESHOLD_SECONDS = 120" in render.SCRIPT

    def test_escapes_html_in_session_titles(self):
        # Titles are model-generated text and must never be injected raw.
        info = transcripts.TranscriptInfo(title="<script>alert(1)</script>")
        html = render.render(self._snapshot(transcripts_by_sid={"sid-a": info}))
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_html_in_card_names(self):
        # Tmux session names are arbitrary and can contain < > ".
        panes = [TmuxPane("session<>\"", 1, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "name")}
        snap = build(panes, sessions, {"/dev/pts/5": 42})
        html = render.render(snap)
        assert "session<>\"" not in html
        assert "session&lt;&gt;&quot;" in html

    def test_escapes_html_in_attach_targets(self):
        # Attach target comes from tmux session names which are arbitrary.
        panes = [TmuxPane("s<x>", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42})
        html = render.render(snap)
        raw_attach = "tmux attach -t s<x>:0.0"
        escaped_attach = "tmux attach -t s&lt;x&gt;:0.0"
        assert raw_attach not in html
        assert escaped_attach in html

    def test_escapes_html_in_model_and_branch(self):
        # Model and git_branch come from transcripts and are user-controlled.
        info = transcripts.TranscriptInfo(title="test")
        panes = [TmuxPane("s", 0, 0, "/tmp", "/dev/pts/5")]
        sessions = {42: ClaudeSession(42, "sid-a", "/tmp", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/5": 42}, transcripts_by_sid={"sid-a": info})
        # Manually patch the pane record to inject hostile values
        snap.cards[0].panes[0].model = "claude<script>"
        snap.cards[0].panes[0].git_branch = "feature\">"
        html = render.render(snap)
        assert "claude<script>" not in html
        assert "claude&lt;script&gt;" in html
        assert "feature\">" not in html
        assert "feature&quot;&gt;" in html

    def test_duration_boundary_seconds(self):
        from agents_dashboard.render import _duration
        assert _duration(0) == "0s"
        assert _duration(1) == "1s"
        assert _duration(59) == "59s"

    def test_duration_boundary_minutes(self):
        from agents_dashboard.render import _duration
        assert _duration(60) == "1m"
        assert _duration(3599) == "59m"

    def test_duration_boundary_hours(self):
        from agents_dashboard.render import _duration
        assert _duration(3600) == "1h"
        assert _duration(86399) == "23h"

    def test_duration_boundary_days(self):
        from agents_dashboard.render import _duration
        assert _duration(86400) == "1d"
        assert _duration(2_160_000) == "25d"

    def test_duration_negative_input_clamped_to_zero(self):
        from agents_dashboard.render import _duration
        # Clock skew could produce negative durations.
        assert _duration(-100) == "0s"

    def test_not_started_card_has_dimming_class(self):
        panes = [TmuxPane("empty", 0, 0, "/tmp", "/dev/pts/1")]
        snap = build(panes, {}, {})
        html = render.render(snap)
        assert 'class="card not-started"' in html

    def test_all_phase_values_have_css_rules(self):
        from agents_dashboard.render import CSS, STALE_BANNER_CSS
        # Every Phase enum value must have a corresponding .phase-<value> rule.
        for phase in Phase:
            css_rule = f".phase-{phase.value}"
            full_css = CSS + STALE_BANNER_CSS
            assert css_rule in full_css, f"Missing CSS rule: {css_rule}"

    def test_references_no_external_assets(self):
        html = render.render(self._snapshot())
        assert "http://" not in html
        assert "https://" not in html
        # Reject protocol-relative URLs (must not match // in comments or text)
        # Check for // used as URL prefix in src=, href=, or url(
        assert 'src="//' not in html
        assert 'href="//' not in html
        assert "url(//" not in html

    def test_wrapped_title_continuation_lines_are_dimmed_with_color(self):
        # When a title wraps onto continuation lines, those lines must be dim.
        from agents_dashboard.termview import DIM, render_terminal, display_width
        import re

        pane = PaneRecord(
            session_id="sid-1",
            tmux_session="test@2026",
            window_index=0,
            pane_index=0,
            cwd="/tmp",
            activity=Activity.WAITING,
            waiting_reason=WaitingReason.IDLE,
            title="Improve reproducible evals with visual inspection dashboard",
            phase=Phase.IMPLEM,
            phase_evidence=PhaseEvidence.NONE,
            tasks=TaskProgress(known=False),
        )
        window = WindowRecord(0, 0, "claude", "/tmp", time.time(), claude=pane)
        card = SessionCard("test@2026", [window], False)
        snap = Snapshot(generated_at=time.time(), cards=[card])

        output = render_terminal(snap, width=65, color=True, show_phase=False)
        lines = output.strip().split('\n')

        # Find lines that contain title text (wrapped continuation lines)
        # They should start with indent spaces then DIM code
        dim_escape = DIM
        title_lines = [l for l in lines if "Improve" in l or "dashboard" in l]
        assert len(title_lines) >= 2  # title wraps to at least 2 lines

        # Continuation lines (those that start with spaces for indentation)
        # should have DIM at the start
        for line in title_lines[1:]:  # skip first fragment if on row line
            if line.strip() and not line.startswith("WIN"):  # skip headers
                # If this is a continuation line with title text, it must be dimmed
                if "Improve" in line or "dashboard" in line:
                    # Must contain the DIM escape code
                    assert dim_escape in line, f"Continuation line missing DIM: {repr(line)}"
                    # Must have a matching RESET
                    assert RESET in line, f"Continuation line missing RESET: {repr(line)}"

    def test_wrapped_title_first_fragment_not_dimmed(self):
        # When a title wraps onto continuation lines, the first fragment on
        # the row line is NOT dimmed. Only continuation lines get DIM.
        from agents_dashboard.termview import DIM, render_terminal

        pane = PaneRecord(
            session_id="sid-1",
            tmux_session="test@2026",
            window_index=0,
            pane_index=0,
            cwd="/tmp",
            activity=Activity.WORKING,
            title="Improve reproducible evals with visual inspection dashboard",
            phase=Phase.IMPLEM,
            phase_evidence=PhaseEvidence.NONE,
            tasks=TaskProgress(known=False),
        )
        window = WindowRecord(0, 0, "claude", "/tmp", time.time(), claude=pane)
        card = SessionCard("test@2026", [window], False)
        snap = Snapshot(generated_at=time.time(), cards=[card])

        # At width 80, the title wraps and the first fragment fits on the row
        output = render_terminal(snap, width=80, color=True, show_phase=False)
        lines = output.strip().split('\n')

        # Find the data rows (skip header and blank lines)
        data_lines = [l for l in lines if l and not l.startswith("WIN") and "@" not in l]
        assert len(data_lines) >= 2

        # First data line has the row with columns and the first fragment
        row_line = data_lines[0]
        assert "Improve" in row_line

        # The first fragment should NOT have DIM applied to the entire title
        # The title part at the end should not start with DIM escape
        title_start = row_line.rfind("Improve")
        if title_start >= 0:
            # The "Improve" part should not be preceded immediately by DIM
            # (there might be spaces or column coloring, but not DIM for the title)
            title_fragment = row_line[title_start:]
            assert not title_fragment.startswith(DIM)

        # The second data line has the continuation (should be dimmed)
        continuation_line = data_lines[1]
        assert continuation_line.startswith(DIM)

    def test_wrapped_title_no_escape_codes_without_color(self):
        # With color=False, no escape codes should appear anywhere.
        from agents_dashboard.termview import render_terminal

        pane = PaneRecord(
            session_id="sid-1",
            tmux_session="test@2026",
            window_index=0,
            pane_index=0,
            cwd="/tmp",
            activity=Activity.WAITING,
            waiting_reason=WaitingReason.IDLE,
            title="Improve reproducible evals with visual inspection dashboard",
            phase=Phase.IMPLEM,
            phase_evidence=PhaseEvidence.NONE,
            tasks=TaskProgress(known=False),
        )
        window = WindowRecord(0, 0, "claude", "/tmp", time.time(), claude=pane)
        card = SessionCard("test@2026", [window], False)
        snap = Snapshot(generated_at=time.time(), cards=[card])

        output = render_terminal(snap, width=65, color=False, show_phase=False)

        # No escape codes at all
        assert '\x1b[' not in output

        # But the text must still be there, wrapped correctly
        assert "Improve" in output
        assert "dashboard" in output

    def test_wrapped_title_uses_same_dim_constant_as_timestamp_suffix(self):
        # The dim code for wrapped titles must be the same constant used for
        # the @timestamp suffix in session names, so they cannot drift apart.
        from agents_dashboard.termview import render_terminal, DIM, RESET

        pane = PaneRecord(
            session_id="sid-1",
            tmux_session="v_jaygent@2026-06-10-11h07",
            window_index=0,
            pane_index=0,
            cwd="/tmp",
            activity=Activity.WAITING,
            waiting_reason=WaitingReason.IDLE,
            title="Improve reproducible evals with visual inspection dashboard",
            phase=Phase.IMPLEM,
            phase_evidence=PhaseEvidence.NONE,
            tasks=TaskProgress(known=False),
        )
        window = WindowRecord(0, 0, "claude", "/tmp", time.time(), claude=pane)
        card = SessionCard("v_jaygent@2026-06-10-11h07", [window], False)
        snap = Snapshot(generated_at=time.time(), cards=[card])

        output = render_terminal(snap, width=65, color=True, show_phase=False)
        lines = output.strip().split('\n')

        # Find the session name line (contains @)
        session_lines = [l for l in lines if "@2026-06-10-11h07" in l]
        assert len(session_lines) > 0
        session_line = session_lines[0]

        # Find the wrapped title lines
        title_lines = [l for l in lines if "Improve" in l or "dashboard" in l]
        assert len(title_lines) > 0

        # Extract the DIM code used in session line
        # Should be something like: ...\x1b[2m@2026-06-10-11h07\x1b[0m...
        session_dim_start = session_line.find(f"{DIM}@")
        assert session_dim_start != -1, f"Session line should use DIM for @timestamp: {repr(session_line)}"

        # Extract the DIM code used in continuation lines
        continuation_line = [l for l in title_lines if l.startswith(DIM)]
        assert len(continuation_line) > 0, f"Continuation lines should start with DIM"

        # Both should use the exact same escape code
        for cont_line in continuation_line:
            assert cont_line.startswith(DIM)

    def test_no_wrapped_title_lines_exceed_width_at_various_sizes(self):
        # Escape sequences must not count toward the width budget.
        # If display_width is wrong, colored wrapped titles would overflow.
        from agents_dashboard.termview import render_terminal, display_width
        import re

        pane = PaneRecord(
            session_id="sid-1",
            tmux_session="test@2026",
            window_index=0,
            pane_index=0,
            cwd="/tmp",
            activity=Activity.WAITING,
            waiting_reason=WaitingReason.IDLE,
            title="Improve reproducible evals with visual inspection dashboard data systems",
            phase=Phase.IMPLEM,
            phase_evidence=PhaseEvidence.NONE,
            tasks=TaskProgress(known=False),
        )
        window = WindowRecord(0, 0, "claude", "/tmp", time.time(), claude=pane)
        card = SessionCard("test@2026", [window], False)
        snap = Snapshot(generated_at=time.time(), cards=[card])

        for width in [65, 80, 100, 200]:
            output = render_terminal(snap, width=width, color=True, show_phase=False)
            lines = output.strip().split('\n')

            for i, line in enumerate(lines):
                # Strip escape codes and measure
                clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
                line_width = display_width(clean)
                # Allow small tolerance for rounding, but generally should not exceed width
                assert line_width <= width + 1, (
                    f"Line {i} at width={width} exceeds budget: "
                    f"display_width={line_width}, width={width}, line={repr(line)}"
                )


from agents_dashboard import server


class TestSnapshotCache:
    def test_first_call_collects(self):
        calls = []
        cache = server.SnapshotCache(ttl=3.0, collector=lambda: calls.append(1) or "snap",
                                     clock=lambda: 100.0)
        assert cache.get() == "snap"
        assert len(calls) == 1

    def test_within_ttl_does_not_recollect(self):
        calls = []
        now = [100.0]
        cache = server.SnapshotCache(ttl=3.0, collector=lambda: calls.append(1) or "snap",
                                     clock=lambda: now[0])
        cache.get()
        now[0] = 102.0
        cache.get()
        assert len(calls) == 1

    def test_past_ttl_recollects(self):
        calls = []
        now = [100.0]
        cache = server.SnapshotCache(ttl=3.0, collector=lambda: calls.append(1) or "snap",
                                     clock=lambda: now[0])
        cache.get()
        now[0] = 104.0
        cache.get()
        assert len(calls) == 2

    def test_a_failing_collector_serves_the_previous_snapshot(self):
        # A transient tmux hiccup must not take the page down.
        state = {"fail": False}
        now = [100.0]

        def collector():
            if state["fail"]:
                raise RuntimeError("tmux went away")
            return "good"

        cache = server.SnapshotCache(ttl=1.0, collector=collector, clock=lambda: now[0])
        assert cache.get() == "good"
        state["fail"] = True
        now[0] = 200.0
        assert cache.get() == "good"


import io

from agents_dashboard.models import Snapshot


class TestMakeHandler:
    """Drives do_GET directly against a stubbed wfile - never binds a real socket."""

    def _drive(self, cache, path):
        handler_cls = server.make_handler(cache)
        handler = handler_cls.__new__(handler_cls)
        handler.path = path
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "GET"
        handler.close_connection = True
        handler.wfile = io.BytesIO()
        handler.do_GET()
        raw = handler.wfile.getvalue()
        head, _, body = raw.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n")[0].decode()
        headers = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                key, _, value = line.partition(b":")
                headers[key.decode().strip().lower()] = value.decode().strip()
        return status_line, headers, body

    def _cache(self, collector=None):
        if collector is None:
            def collector():
                return Snapshot(generated_at=1000.0, cards=[])
        return server.SnapshotCache(collector=collector, clock=lambda: 1.0)

    def test_root_serves_html_with_accurate_content_length(self):
        status, headers, body = self._drive(self._cache(), "/")
        assert status == "HTTP/1.1 200 OK"
        assert int(headers["content-length"]) == len(body)
        assert body.lstrip().startswith(b"<!doctype html>")

    def test_state_json_serves_json_with_accurate_content_length(self):
        status, headers, body = self._drive(self._cache(), "/api/state.json")
        assert status == "HTTP/1.1 200 OK"
        assert int(headers["content-length"]) == len(body)
        assert json.loads(body) == {"generated_at": 1000.0, "cards": []}

    def test_state_json_accepts_a_query_string(self):
        status, headers, body = self._drive(self._cache(), "/api/state.json?x=1")
        assert status == "HTTP/1.1 200 OK"
        assert int(headers["content-length"]) == len(body)

    def test_state_json_rejects_a_bogus_suffix(self):
        # Regression: a bare startswith() previously matched "/api/state.jsonBOGUS" too.
        status, headers, body = self._drive(self._cache(), "/api/state.jsonBOGUS")
        assert status == "HTTP/1.1 404 Not Found"
        assert int(headers["content-length"]) == len(body)

    def test_unknown_path_returns_404_with_accurate_content_length(self):
        status, headers, body = self._drive(self._cache(), "/nope")
        assert status == "HTTP/1.1 404 Not Found"
        assert int(headers["content-length"]) == len(body)

    def test_collector_failure_returns_503_with_accurate_content_length(self):
        def failing_collector():
            raise RuntimeError("tmux went away")

        status, headers, body = self._drive(self._cache(failing_collector), "/")
        assert status == "HTTP/1.1 503 Service Unavailable"
        assert int(headers["content-length"]) == len(body)


class TestPushToPi:
    @pytest.fixture(autouse=True)
    def _push_remote_env(self, monkeypatch):
        # push_to_pi now requires AGENTS_PUSH_REMOTE (no hardcoded fallback);
        # tests supply a test-only target with the same path shape the
        # assertions below check for.
        monkeypatch.setenv("AGENTS_PUSH_REMOTE", "test-pi:/srv/agents/state.json")

    def _snapshot(self):
        return Snapshot(generated_at=1000.0, cards=[])

    def test_returns_true_when_scp_succeeds(self, monkeypatch):
        monkeypatch.setattr(
            server.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0),
        )
        assert server.push_to_pi(self._snapshot()) is True

    def test_returns_false_when_scp_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(
            server.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1),
        )
        assert server.push_to_pi(self._snapshot()) is False

    def test_returns_false_when_scp_is_missing(self, monkeypatch):
        def raise_oserror(*a, **k):
            raise OSError("scp: command not found")

        monkeypatch.setattr(server.subprocess, "run", raise_oserror)
        assert server.push_to_pi(self._snapshot()) is False

    def test_returns_false_on_timeout(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="scp", timeout=30)

        monkeypatch.setattr(server.subprocess, "run", raise_timeout)
        assert server.push_to_pi(self._snapshot()) is False

    def test_unserialisable_snapshot_returns_false_rather_than_raising(self):
        # Pins the fix: json.dumps used to run outside the try/except, so a bad
        # payload propagated straight out of push_to_pi instead of returning False.
        bad_snapshot = Snapshot(generated_at={"not", "json-serialisable"}, cards=[])
        assert server.push_to_pi(bad_snapshot) is False

    def test_uses_atomic_mv_for_destination_write(self, monkeypatch):
        # The write is atomic: write to temp, then mv into place.
        # mv on same filesystem is atomic, preventing torn reads.
        # push_to_pi now sends two files (state.json, index.html), each
        # through the same atomic dance.
        captured_commands = []

        def stub_subprocess_run(argv, **kwargs):
            captured_commands.append(argv)
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        assert server.push_to_pi(self._snapshot()) is True
        assert len(captured_commands) == 2
        for argv in captured_commands:
            assert " mv -f " in " ".join(argv)

    def test_remote_command_sets_mode_644_on_temp_before_move(self, monkeypatch):
        # The temp file must be world-readable before moving, so www-data can
        # serve it - for both pushed files.
        captured_commands = []

        def stub_subprocess_run(argv, **kwargs):
            captured_commands.append(argv)
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        server.push_to_pi(self._snapshot())
        for argv in captured_commands:
            assert "chmod 644" in " ".join(argv)

    def test_temp_path_is_same_directory_as_destination(self, monkeypatch):
        # For mv to be atomic, temp must be in the same directory.
        # Derived as destination + ".tmp". The first push is state.json.
        captured_commands = []

        def stub_subprocess_run(argv, **kwargs):
            captured_commands.append(argv)
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        server.push_to_pi(self._snapshot())
        cmd_str = " ".join(captured_commands[0])
        assert "/srv/agents/state.json.tmp" in cmd_str
        assert "/srv/agents/state.json" in cmd_str

    def test_payload_passed_via_stdin_not_temp_file(self, monkeypatch):
        # No local temp file: payload goes via stdin to remote cat, for both
        # of the pushed files.
        captured_calls = []

        def stub_subprocess_run(argv, input=None, **kwargs):
            captured_calls.append({
                "argv": argv,
                "has_input": input is not None,
                "input_is_bytes": isinstance(input, bytes),
            })
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        server.push_to_pi(self._snapshot())
        assert len(captured_calls) == 2
        for call in captured_calls:
            assert call["has_input"] is True
            assert call["input_is_bytes"] is True

    def test_pushes_both_state_json_and_index_html(self, monkeypatch):
        # Resolves the outage promise: the Pi's fallback now has a real
        # rendered page to serve, not just the JSON snapshot.
        captured_commands = []

        def stub_subprocess_run(argv, **kwargs):
            captured_commands.append(" ".join(argv))
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        assert server.push_to_pi(self._snapshot()) is True
        assert len(captured_commands) == 2
        assert any(
            "/srv/agents/state.json.tmp" in c and c.endswith("/srv/agents/state.json")
            for c in captured_commands
        )
        assert any(
            "/srv/agents/index.html.tmp" in c and c.endswith("/srv/agents/index.html")
            for c in captured_commands
        )

    def test_failure_pushing_either_file_returns_false_without_raising(self, monkeypatch):
        calls = {"n": 0}

        def stub_subprocess_run(argv, **kwargs):
            calls["n"] += 1
            # state.json (first push) succeeds; index.html (second) fails.
            return subprocess.CompletedProcess(
                args=[], returncode=0 if calls["n"] == 1 else 1
            )

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        assert server.push_to_pi(self._snapshot()) is False
        assert calls["n"] == 2

    def test_failed_push_logs_remote_target_and_stderr(self, monkeypatch, caplog):
        def stub_subprocess_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stderr=b"permission denied"
            )

        monkeypatch.setattr(server.subprocess, "run", stub_subprocess_run)
        with caplog.at_level(logging.WARNING, logger="agents_dashboard.server"):
            server.push_to_pi(self._snapshot())
        messages = [r.getMessage() for r in caplog.records]
        assert any("permission denied" in m for m in messages)
        assert any("test-pi" in m and "state.json" in m for m in messages)

    def test_returns_false_with_clear_error_when_remote_not_configured(self, monkeypatch, caplog):
        # No hardcoded fallback host: an unconfigured box must fail loudly
        # (in the journal) rather than silently push to a baked-in address.
        monkeypatch.delenv("AGENTS_PUSH_REMOTE", raising=False)
        with caplog.at_level(logging.WARNING, logger="agents_dashboard.server"):
            assert server.push_to_pi(self._snapshot()) is False
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "AGENTS_PUSH_REMOTE" in m and "agents-dashboard/env" in m for m in messages
        )


class TestPushLoop:
    def test_a_raising_push_does_not_escape_the_loop(self, monkeypatch):
        calls = {"push": 0, "sleep": 0}

        def fake_push(snapshot):
            calls["push"] += 1
            raise RuntimeError("pi unreachable")

        def fake_sleep(interval):
            calls["sleep"] += 1
            if calls["sleep"] >= 2:
                raise StopIteration  # our own escape hatch out of the infinite loop

        monkeypatch.setattr(server, "push_to_pi", fake_push)
        monkeypatch.setattr(server.time, "sleep", fake_sleep)

        cache = server.SnapshotCache(collector=lambda: "snap", clock=lambda: 1.0)

        with pytest.raises(StopIteration):
            server._push_loop(cache, 1.0)

        # One push happened and raised - if that exception had escaped the
        # loop's own try/except, we would never have reached the second
        # time.sleep() call that raises StopIteration.
        assert calls["push"] == 1
        assert calls["sleep"] == 2

    def test_a_raising_push_logs_a_warning(self, monkeypatch, caplog):
        # The ledger records a push failure (a 0600 file nginx could not
        # read) that shipped and was caught only by manual live testing -
        # this pins that the loop no longer swallows the failure silently.
        calls = {"sleep": 0}

        def fake_push(snapshot):
            raise RuntimeError("pi unreachable")

        def fake_sleep(interval):
            calls["sleep"] += 1
            if calls["sleep"] >= 2:
                raise StopIteration

        monkeypatch.setattr(server, "push_to_pi", fake_push)
        monkeypatch.setattr(server.time, "sleep", fake_sleep)

        cache = server.SnapshotCache(collector=lambda: "snap", clock=lambda: 1.0)

        with caplog.at_level(logging.WARNING, logger="agents_dashboard.server"):
            with pytest.raises(StopIteration):
                server._push_loop(cache, 1.0)

        assert any(
            "pi unreachable" in (r.exc_text or "") or "pi unreachable" in r.getMessage()
            for r in caplog.records
        )


class TestRun:
    def test_wires_a_daemon_push_thread_sharing_the_same_cache_as_the_handler(self, monkeypatch):
        recorded = {}

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                recorded["thread_target"] = target
                recorded["thread_args"] = args
                recorded["thread_daemon"] = daemon

            def start(self):
                recorded["thread_started"] = True

        class FakeHTTPServer:
            def __init__(self, address, handler_cls):
                recorded["address"] = address
                recorded["handler_cls"] = handler_cls

            def serve_forever(self):
                recorded["served_forever"] = True

        def fake_make_handler(cache):
            recorded["handler_cache"] = cache
            return object

        monkeypatch.setattr(server.threading, "Thread", FakeThread)
        monkeypatch.setattr(server, "ThreadingHTTPServer", FakeHTTPServer)
        monkeypatch.setattr(server, "make_handler", fake_make_handler)

        server.run(port=9999, host="127.0.0.1", push_interval=5.0)

        assert recorded["thread_daemon"] is True
        assert recorded["thread_started"] is True
        assert recorded["thread_target"] is server._push_loop
        assert recorded["address"] == ("127.0.0.1", 9999)
        assert recorded["served_forever"] is True

        push_cache, push_interval = recorded["thread_args"]
        assert push_interval == 5.0
        assert push_cache is recorded["handler_cache"]


class TestServeHost:
    def test_serve_default_host_is_loopback(self, monkeypatch):
        # serve() must default to loopback for safety; binding 0.0.0.0 is opt-in
        captured_kwargs = {}

        def fake_run(**kwargs):
            captured_kwargs.update(kwargs)

        monkeypatch.setattr("agents_dashboard.server.run", fake_run)

        from agents_dashboard.__main__ import serve
        serve(port=8770)

        assert captured_kwargs["host"] == "127.0.0.1"

    def test_serve_host_parameter_is_passed_through(self, monkeypatch):
        # serve(host="0.0.0.0") must pass the explicit host to run()
        captured_kwargs = {}

        def fake_run(**kwargs):
            captured_kwargs.update(kwargs)

        monkeypatch.setattr("agents_dashboard.server.run", fake_run)

        from agents_dashboard.__main__ import serve
        serve(host="0.0.0.0")

        assert captured_kwargs["host"] == "0.0.0.0"


# --- phase evidence and outstanding work ------------------------------------
# Added after Louis pointed out the board showed sessions as `implem` when they
# were really just ongoing. Measurement: 14 of 25 live sessions reached `implem`
# through the edit fallback with no mapped skill anywhere in 4 MB.

from agents_dashboard.classify import classify_phase_with_evidence
from agents_dashboard.models import PaneRecord, PhaseEvidence, TaskProgress
from agents_dashboard import transcripts as _t


def _tool(name, **inp):
    return {"message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def test_edit_only_phase_is_marked_as_a_guess():
    phase, ev = classify_phase_with_evidence(
        [PhaseSignal(kind="edit_burst", name="edit_burst")], None)
    assert phase is Phase.IMPLEM
    assert ev is PhaseEvidence.EDITS


def test_skill_backed_phase_is_not_a_guess():
    phase, ev = classify_phase_with_evidence(
        [PhaseSignal(kind="skill", name="wrap-up")], None)
    assert phase is Phase.WRAP_UP
    assert ev is PhaseEvidence.SKILL


def test_plan_mode_and_no_signal_evidence():
    assert classify_phase_with_evidence([], "plan")[1] is PhaseEvidence.PLAN_MODE
    assert classify_phase_with_evidence([], None) == (Phase.UNKNOWN, PhaseEvidence.NONE)


def test_pane_phase_is_guess_only_for_edit_evidence():
    base = dict(session_id="s", tmux_session="t", window_index=0, pane_index=0, cwd="/")
    assert PaneRecord(**base, phase_evidence=PhaseEvidence.EDITS).phase_is_guess
    for ev in (PhaseEvidence.SKILL, PhaseEvidence.PLAN_MODE, PhaseEvidence.NONE):
        assert not PaneRecord(**base, phase_evidence=ev).phase_is_guess


def test_task_progress_counts_outstanding_work():
    info = _t.extract([
        _tool("TaskCreate", subject="a"), _tool("TaskCreate", subject="b"),
        _tool("TaskCreate", subject="c"),
        _tool("TaskUpdate", taskId="1", status="completed"),
        _tool("TaskUpdate", taskId="2", status="in_progress"),
    ])
    assert info.tasks.known and info.tasks.total == 3
    assert info.tasks.completed == 1 and info.tasks.outstanding == 2


def test_task_progress_unknown_when_session_never_used_the_tools():
    info = _t.extract([_tool("Bash", command="ls"), _tool("Edit", file_path="x")])
    assert not info.tasks.known
    assert info.tasks.outstanding == 0   # must not imply "nothing left to do"


def test_task_updates_without_visible_creates_do_not_exceed_total():
    # The create fell outside the scan window; "2 of 1 done" would be nonsense.
    info = _t.extract([
        _tool("TaskUpdate", taskId="7", status="completed"),
        _tool("TaskUpdate", taskId="8", status="completed"),
    ])
    assert info.tasks.total == 2 and info.tasks.completed == 2
    assert info.tasks.outstanding == 0


def test_render_marks_guessed_phase_and_shows_open_tasks():
    now = 1_000_000.0
    guess = PaneRecord(session_id="s", tmux_session="t", window_index=0, pane_index=0,
                       cwd="/", phase=Phase.IMPLEM,
                       phase_evidence=PhaseEvidence.EDITS,
                       tasks=TaskProgress(known=True, total=6, completed=3),
                       title="ongoing thing")
    solid = PaneRecord(session_id="s2", tmux_session="t", window_index=0, pane_index=1,
                       cwd="/", phase=Phase.IMPLEM,
                       phase_evidence=PhaseEvidence.SKILL, title="evidenced thing")
    html_guess = render._pane_row(guess, now)
    html_solid = render._pane_row(solid, now)
    assert "implem?" in html_guess and "weak" in html_guess
    assert "3 open" in html_guess
    assert "implem?" not in html_solid and "weak" not in html_solid
    # A session with no task data must not claim anything about tasks.
    assert "open" not in html_solid and "all done" not in html_solid


class TestClaudePidsByTty:
    def test_indexes_claude_processes_by_bare_tty(self):
        runner = FakeRunner({"ps": "  17313 pts/1    claude\n  17318 pts/2    claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/1": 17313, "pts/2": 17318}

    def test_ignores_non_claude_processes(self):
        runner = FakeRunner({"ps": "  1 ?        systemd\n  99 pts/1    zsh\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {}

    def test_matches_an_absolute_path_to_claude(self):
        runner = FakeRunner({"ps": "  42 pts/3    /usr/local/bin/claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/3": 42}

    def test_does_not_match_a_lookalike(self):
        runner = FakeRunner({"ps": "  42 pts/3    claude-log\n  43 pts/4    /usr/bin/claude-log\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {}

    def test_first_pid_wins_for_a_shared_tty(self):
        # Matches claude_pid_for_tty, which returns the first match. A nested
        # claude on the same tty must not displace the one already recorded.
        runner = FakeRunner({"ps": "  100 pts/1    claude\n  200 pts/1    claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/1": 100}

    def test_skips_malformed_lines_without_raising(self):
        runner = FakeRunner({"ps": "garbage\n\n  42 pts/3    claude\n"})
        assert tmux.claude_pids_by_tty(runner=runner) == {"pts/3": 42}

    def test_no_ps_output_returns_empty(self):
        assert tmux.claude_pids_by_tty(runner=FakeRunner({})) == {}


class TestNormaliseTty:
    def test_strips_the_dev_prefix_tmux_reports(self):
        # tmux says /dev/pts/5, ps says pts/5. Getting this wrong yields an
        # empty map and a dashboard showing no Claude sessions at all.
        assert tmux.normalise_tty("/dev/pts/5") == "pts/5"

    def test_leaves_an_already_bare_tty_alone(self):
        assert tmux.normalise_tty("pts/5") == "pts/5"

    def test_a_map_built_from_ps_is_reachable_with_a_tmux_tty(self):
        runner = FakeRunner({"ps": "  42 pts/5    claude\n"})
        pids = tmux.claude_pids_by_tty(runner=runner)
        assert pids.get(tmux.normalise_tty("/dev/pts/5")) == 42


# --- windows widen the model: every tmux window is a record, Claude or not --
from agents_dashboard.models import SessionCard, WindowRecord


class TestWindowRecordModel:
    def test_panes_property_returns_only_claude_windows(self):
        claude = PaneRecord(session_id="a", tmux_session="s", window_index=1,
                            pane_index=0, cwd="/tmp")
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "zsh", "/tmp", 100.0, None),
            WindowRecord(1, 0, "claude", "/tmp", 100.0, claude),
        ])
        assert card.panes == [claude]

    def test_not_started_is_true_when_no_window_runs_claude(self):
        card = SessionCard(name="s", windows=[WindowRecord(0, 0, "zsh", "/tmp", 100.0, None)])
        assert card.not_started is True

    def test_not_started_is_false_with_a_claude_window(self):
        claude = PaneRecord(session_id="a", tmux_session="s", window_index=0,
                            pane_index=0, cwd="/tmp")
        card = SessionCard(name="s", windows=[WindowRecord(0, 0, "claude", "/tmp", 100.0, claude)])
        assert card.not_started is False

    def test_panes_is_ordered_by_urgency_not_window_index(self):
        # The web dashboard depends on this ordering; the terminal view reads
        # `windows` instead, which stays in numeric order.
        idle = PaneRecord(session_id="i", tmux_session="s", window_index=0, pane_index=0,
                          cwd="/tmp", activity=Activity.WAITING,
                          waiting_reason=WaitingReason.IDLE, waiting_since=500.0)
        blocked = PaneRecord(session_id="b", tmux_session="s", window_index=9, pane_index=0,
                             cwd="/tmp", activity=Activity.WAITING,
                             waiting_reason=WaitingReason.PERMISSION, waiting_since=500.0)
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "claude", "/tmp", 100.0, idle),
            WindowRecord(9, 0, "claude", "/tmp", 100.0, blocked),
        ])
        assert [p.session_id for p in card.panes] == ["b", "i"]
        assert [w.window_index for w in card.windows] == [0, 9]

    def test_unknown_wait_sorts_after_a_known_wait_at_equal_urgency(self):
        # claude_sessions coerces a corrupt/missing statusUpdatedAt to 0,
        # which is falsy. `pane.waiting_since or 0.0` treated that the same
        # as "waiting since epoch 0" - the oldest possible wait - so a
        # session with a broken status file sorted to the TOP of its
        # urgency group instead of the bottom. float("inf") makes an
        # unknown wait sort last.
        known = PaneRecord(session_id="k", tmux_session="s", window_index=0,
                           pane_index=0, cwd="/tmp", activity=Activity.WAITING,
                           waiting_reason=WaitingReason.IDLE, waiting_since=500.0)
        unknown = PaneRecord(session_id="u", tmux_session="s", window_index=1,
                             pane_index=0, cwd="/tmp", activity=Activity.WAITING,
                             waiting_reason=WaitingReason.IDLE, waiting_since=0.0)
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "claude", "/tmp", 100.0, known),
            WindowRecord(1, 0, "claude", "/tmp", 100.0, unknown),
        ])
        assert [p.session_id for p in card.panes] == ["k", "u"]


class TestSnapshotCarriesAllWindows:
    def test_non_claude_windows_appear_in_the_snapshot(self):
        panes = [
            tmux.TmuxPane("s", 0, 0, "/home/ezalos/Setup", "/dev/pts/1",
                          command="zsh", quiet_since=900.0),
            tmux.TmuxPane("s", 1, 0, "/home/ezalos/Setup", "/dev/pts/2",
                          command="claude", quiet_since=950.0),
        ]
        sessions = {42: ClaudeSession(42, "sid", "/home/ezalos/Setup", "idle", 900.0, "n")}
        snap = build(panes, sessions, {"/dev/pts/2": 42})
        card = snap.cards[0]
        assert [w.command for w in card.windows] == ["zsh", "claude"]
        assert card.windows[0].claude is None
        assert card.windows[1].claude is not None
        assert len(card.panes) == 1

    def test_window_carries_its_own_cwd_and_quiet_time(self):
        panes = [tmux.TmuxPane("s", 0, 0, "/home/ezalos/42/Alfred", "/dev/pts/1",
                               command="zsh", quiet_since=900.0)]
        card = build(panes, {}, {}).cards[0]
        assert card.windows[0].cwd == "/home/ezalos/42/Alfred"
        assert card.windows[0].quiet_since == 900.0

    def test_windows_are_in_numeric_order(self):
        panes = [
            tmux.TmuxPane("s", 11, 0, "/tmp", "/dev/pts/3", command="zsh", quiet_since=1.0),
            tmux.TmuxPane("s", 2, 0, "/tmp", "/dev/pts/1", command="zsh", quiet_since=1.0),
        ]
        assert [w.window_index for w in build(panes, {}, {}).cards[0].windows] == [2, 11]

    def test_card_records_attached_state_and_activity(self):
        panes = [tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/1", command="zsh",
                               quiet_since=1.0, session_attached=True, session_activity=777.0)]
        card = build(panes, {}, {}).cards[0]
        assert card.attached is True
        assert card.activity == 777.0


def test_urgency_order_matches_classify():
    from agents_dashboard.classify import URGENCY, urgency_rank
    from agents_dashboard.models import _URGENCY_ORDER
    assert list(_URGENCY_ORDER) == sorted(URGENCY, key=urgency_rank)


from agents_dashboard import collect as collect_mod
from agents_dashboard import tmux


class TestCollectWithPhaseFlag:
    def _stub_sources(self, monkeypatch):
        """Stub every source so collect() is deterministic. Returns a list that
        records each read_for_phase call."""
        deep = []
        pane = tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/1",
                             command="claude", quiet_since=900.0)
        session = ClaudeSession(42, "sid", "/tmp", "idle", 900.0, "n")
        monkeypatch.setattr(collect_mod.tmux, "list_panes", lambda **k: [pane])
        monkeypatch.setattr(collect_mod.tmux, "claude_pids_by_tty",
                            lambda **k: {"pts/1": 42})
        monkeypatch.setattr(collect_mod.tmux, "capture_pane", lambda *a, **k: "")
        monkeypatch.setattr(collect_mod.claude_sessions, "load_all", lambda **k: {42: session})
        monkeypatch.setattr(collect_mod.claude_sessions, "find_transcript",
                            lambda *a, **k: Path("/nonexistent.jsonl"))
        monkeypatch.setattr(collect_mod.transcripts, "read_tail", lambda *a, **k: [])
        monkeypatch.setattr(collect_mod.transcripts, "read", lambda *a, **k: transcripts.TranscriptInfo())
        monkeypatch.setattr(collect_mod.transcripts, "read_for_phase",
                            lambda *a, **k: deep.append(1) or transcripts.TranscriptInfo())
        return deep

    def test_with_phase_false_skips_the_deep_scan(self, monkeypatch):
        deep = self._stub_sources(monkeypatch)
        collect_mod.collect(with_phase=False)
        assert deep == [], "the 4 MB scan must not run when phase is not shown"

    def test_with_phase_true_performs_the_deep_scan(self, monkeypatch):
        deep = self._stub_sources(monkeypatch)
        collect_mod.collect(with_phase=True)
        assert deep, "the phase scan must still run when phase is requested"

    def test_default_is_with_phase(self, monkeypatch):
        # The web dashboard relies on the default; flipping it would silently
        # blank its phase column.
        deep = self._stub_sources(monkeypatch)
        collect_mod.collect()
        assert deep

    def test_skipping_the_scan_does_not_invent_a_phase(self, monkeypatch):
        self._stub_sources(monkeypatch)
        snap = collect_mod.collect(with_phase=False)
        phases = {p.phase for c in snap.cards for p in c.panes}
        assert phases <= {Phase.UNKNOWN}

    def test_signals_are_cleared_when_phase_not_scanned(self, monkeypatch):
        # The previous test passes with empty stubs. This one verifies that even
        # when read() returns signals (which it does on the real machine), they
        # are cleared and the phase remains UNKNOWN. This catches the bug where
        # the shallow window produces false phases.
        deep = []
        pane = tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/1",
                             command="claude", quiet_since=900.0)
        session = ClaudeSession(42, "sid", "/tmp", "idle", 900.0, "n")
        monkeypatch.setattr(collect_mod.tmux, "list_panes", lambda **k: [pane])
        monkeypatch.setattr(collect_mod.tmux, "claude_pids_by_tty",
                            lambda **k: {"pts/1": 42})
        monkeypatch.setattr(collect_mod.tmux, "capture_pane", lambda *a, **k: "")
        monkeypatch.setattr(collect_mod.claude_sessions, "load_all", lambda **k: {42: session})
        monkeypatch.setattr(collect_mod.claude_sessions, "find_transcript",
                            lambda *a, **k: Path("/nonexistent.jsonl"))
        monkeypatch.setattr(collect_mod.transcripts, "read_tail", lambda *a, **k: [])
        # read() returns a TranscriptInfo with signals (as it does on the real machine)
        signal_info = transcripts.TranscriptInfo(
            signals=[sig("wrap-up")],
            title="Session Title"
        )
        monkeypatch.setattr(collect_mod.transcripts, "read", lambda *a, **k: signal_info)
        monkeypatch.setattr(collect_mod.transcripts, "read_for_phase",
                            lambda *a, **k: deep.append(1) or transcripts.TranscriptInfo())
        # Even though read() returned signals, with_phase=False should clear them
        snap = collect_mod.collect(with_phase=False)
        phases = {p.phase for c in snap.cards for p in c.panes}
        assert phases == {Phase.UNKNOWN}, "signals must be cleared when phase not scanned"

    def test_mode_is_cleared_when_phase_not_scanned(self, monkeypatch):
        # The phase scan checks mode == "plan" before signals, so mode leaks through
        # the same way signals did. This test verifies that mode is also cleared,
        # preventing plan-mode sessions from showing real phases under with_phase=False.
        deep = []
        pane = tmux.TmuxPane("s", 0, 0, "/tmp", "/dev/pts/1",
                             command="claude", quiet_since=900.0)
        session = ClaudeSession(42, "sid", "/tmp", "idle", 900.0, "n")
        monkeypatch.setattr(collect_mod.tmux, "list_panes", lambda **k: [pane])
        monkeypatch.setattr(collect_mod.tmux, "claude_pids_by_tty",
                            lambda **k: {"pts/1": 42})
        monkeypatch.setattr(collect_mod.tmux, "capture_pane", lambda *a, **k: "")
        monkeypatch.setattr(collect_mod.claude_sessions, "load_all", lambda **k: {42: session})
        monkeypatch.setattr(collect_mod.claude_sessions, "find_transcript",
                            lambda *a, **k: Path("/nonexistent.jsonl"))
        monkeypatch.setattr(collect_mod.transcripts, "read_tail", lambda *a, **k: [])
        # read() returns a TranscriptInfo with mode="plan" but no signals
        mode_info = transcripts.TranscriptInfo(
            mode="plan",
            title="Session Title"
        )
        monkeypatch.setattr(collect_mod.transcripts, "read", lambda *a, **k: mode_info)
        monkeypatch.setattr(collect_mod.transcripts, "read_for_phase",
                            lambda *a, **k: deep.append(1) or transcripts.TranscriptInfo())
        # Even though read() returned mode="plan", with_phase=False should clear it
        snap = collect_mod.collect(with_phase=False)
        phases = {p.phase for c in snap.cards for p in c.panes}
        assert phases == {Phase.UNKNOWN}, "mode must be cleared when phase not scanned"


def test_transcript_info_fields_are_each_classified_as_phase_or_display():
    """Fails when a field is added to TranscriptInfo, forcing whoever adds it
    to decide whether it feeds phase and so must be stripped. Without this,
    a new phase input silently leaks through with_phase=False."""
    import dataclasses
    from agents_dashboard.transcripts import PHASE_INPUT_FIELDS, DISPLAY_FIELDS, TranscriptInfo
    actual = {f.name for f in dataclasses.fields(TranscriptInfo)}
    assert actual == set(PHASE_INPUT_FIELDS) | set(DISPLAY_FIELDS), (
        f"Unclassified fields found: {actual - set(PHASE_INPUT_FIELDS) - set(DISPLAY_FIELDS)}. "
        "Update PHASE_INPUT_FIELDS or DISPLAY_FIELDS in transcripts.py."
    )


def test_strip_phase_inputs_is_driven_by_the_tuple():
    """The PHASE_INPUT_FIELDS tuple must actually control which fields get blanked.

    If a field is removed from PHASE_INPUT_FIELDS, strip_phase_inputs must leave
    it alone; if it's added, it must be blanked. This test verifies the function
    reads the tuple, not a hardcoded field list. Proves the earlier broken version
    (hardcoded constructor) is fixed."""
    import dataclasses
    from agents_dashboard.transcripts import (
        PHASE_INPUT_FIELDS, strip_phase_inputs, TranscriptInfo
    )

    # Create info with a phase input that we'll temporarily omit from the tuple
    info = TranscriptInfo(mode="plan", signals=[], title="Keep me")

    # Verify normal operation: mode should be blanked because it's in the tuple
    normal = strip_phase_inputs(info)
    assert normal.mode is None, "mode should be blanked when in PHASE_INPUT_FIELDS"
    assert normal.title == "Keep me", "display fields should survive"

    # Now monkeypatch to omit "mode" from PHASE_INPUT_FIELDS
    import agents_dashboard.transcripts as transcripts_mod
    original = transcripts_mod.PHASE_INPUT_FIELDS
    try:
        transcripts_mod.PHASE_INPUT_FIELDS = {"signals"}  # omit "mode"
        blanked = strip_phase_inputs(info)
        # With mode omitted from the tuple, it should NOT be blanked
        assert blanked.mode == "plan", (
            "mode should survive when omitted from PHASE_INPUT_FIELDS - "
            "this proves the function reads the tuple"
        )
        assert blanked.title == "Keep me", "display fields must survive"
    finally:
        transcripts_mod.PHASE_INPUT_FIELDS = original


def test_all_display_fields_survive_stripping():
    """Every field in DISPLAY_FIELDS must be preserved by strip_phase_inputs.

    This test is generic over DISPLAY_FIELDS, so a newly added display field
    is covered automatically — the test fails if someone adds a field without
    realizing it was wiped by the old hardcoded constructor."""
    import dataclasses
    from agents_dashboard.transcripts import (
        DISPLAY_FIELDS, strip_phase_inputs, TranscriptInfo
    )

    # Create info with distinctive values in every field
    info = TranscriptInfo(
        signals=[],
        mode=None,
        title="test-title",
        git_branch="test-branch",
        model="test-model",
        asked_question=True,
        tasks=transcripts.TaskProgress(known=True, total=5, completed=3),
    )

    stripped = strip_phase_inputs(info)

    # Every display field must have the same value
    for field_name in DISPLAY_FIELDS:
        original_value = getattr(info, field_name)
        stripped_value = getattr(stripped, field_name)
        assert stripped_value == original_value, (
            f"Display field {field_name} was not preserved: "
            f"{original_value!r} → {stripped_value!r}"
        )


# --- termview: the terminal grid renderer behind `tls` --------------------
from agents_dashboard import termview


class TestTermView:
    def _snap(self, windows, name="alfred", attached=True, now=1000.0):
        return Snapshot(generated_at=now,
                        cards=[SessionCard(name=name, windows=windows, attached=attached)])

    def _claude(self, **kw):
        base = dict(session_id="a", tmux_session="alfred", window_index=0, pane_index=0,
                    cwd="/tmp", title="Build personal agent")
        base.update(kw)
        return PaneRecord(**base)

    def test_non_claude_row_shows_cwd_with_home_collapsed(self):
        w = [WindowRecord(2, 0, "zsh", "/home/ezalos/42/Alfred", 940.0, None)]
        out = termview.render_terminal(self._snap(w), color=False, now=1000.0)
        assert "~/42/Alfred" in out
        assert "/home/ezalos" not in out

    def test_non_claude_row_has_no_claude_columns(self):
        w = [WindowRecord(2, 0, "zsh", "/tmp", 940.0, None)]
        line = [l for l in termview.render_terminal(self._snap(w), color=False).splitlines()
                if "zsh" in l][0]
        assert "idle" not in line and "working" not in line

    def test_claude_row_shows_title_state_and_both_clocks(self):
        pane = self._claude(activity=Activity.WAITING, waiting_reason=WaitingReason.UNSENT_INPUT,
                            waiting_since=1000.0 - 7 * 3600)
        w = [WindowRecord(3, 0, "claude", "/tmp", 1000.0 - 300, pane)]
        line = [l for l in termview.render_terminal(self._snap(w), color=False, now=1000.0)
                .splitlines() if "claude" in l][0]
        assert "5m" in line      # QUIET
        assert "unsent" in line  # STATE
        assert "7h" in line      # WAITING
        assert "Build personal agent" in line

    def test_task_progress_renders_as_completed_over_total(self):
        pane = self._claude(tasks=TaskProgress(known=True, total=9, completed=8))
        w = [WindowRecord(1, 0, "claude", "/tmp", 1000.0, pane)]
        assert "8/9" in termview.render_terminal(self._snap(w), color=False)

    def test_unknown_task_progress_renders_a_placeholder_not_a_zero(self):
        pane = self._claude(tasks=TaskProgress(known=False))
        w = [WindowRecord(1, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), color=False)
        assert "0/0" not in out

    def test_session_header_shows_attached_state(self):
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None)]
        assert "(attached)" in termview.render_terminal(self._snap(w, attached=True), color=False)
        assert "(detached)" in termview.render_terminal(self._snap(w, attached=False), color=False)

    def test_no_ansi_when_color_is_false(self):
        pane = self._claude(activity=Activity.WAITING, waiting_reason=WaitingReason.PERMISSION,
                            waiting_since=900.0)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        assert "\x1b[" not in termview.render_terminal(self._snap(w), color=False)

    def test_ansi_present_when_color_is_true(self):
        pane = self._claude(activity=Activity.WAITING, waiting_reason=WaitingReason.PERMISSION,
                            waiting_since=900.0)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        assert "\x1b[" in termview.render_terminal(self._snap(w), color=True)

    def test_columns_align_across_rows(self):
        pane = self._claude(title="short")
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None),
             WindowRecord(11, 0, "claude", "/tmp", 1000.0, pane)]
        rows = [l for l in termview.render_terminal(self._snap(w), color=False).splitlines()
                if "zsh" in l or "claude" in l]
        assert len({l.index("  ", 6) for l in rows}) == 1  # same first column boundary

    def test_title_is_truncated_to_the_given_width(self):
        pane = self._claude(title="x" * 400)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        for width in (80, 120, 200):
            out = termview.render_terminal(self._snap(w), width=width, color=False)
            assert all(len(line) <= width for line in out.splitlines())

    def test_wide_characters_do_not_break_the_width_budget(self):
        # Titles are model-generated and may contain CJK or emoji, which are
        # two columns wide but one character long. len() would overflow the row.
        pane = self._claude(title="日本語のタイトル" * 20)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), width=100, color=False)
        assert all(termview.display_width(line) <= 100 for line in out.splitlines())

    def test_blank_line_between_sessions(self):
        a = SessionCard(name="alfred", windows=[WindowRecord(0, 0, "zsh", "/tmp", 1.0, None)])
        b = SessionCard(name="setup", windows=[WindowRecord(0, 0, "zsh", "/tmp", 1.0, None)])
        out = termview.render_terminal(Snapshot(generated_at=1.0, cards=[a, b]), color=False)
        assert "\n\n" in out

    def test_phase_column_only_when_requested(self):
        pane = self._claude(phase=Phase.WRAP_UP)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        assert "wrap" not in termview.render_terminal(self._snap(w), color=False)
        assert "wrap" in termview.render_terminal(self._snap(w), color=False, show_phase=True)

    def test_guessed_phase_is_marked_with_a_question_mark(self):
        pane = self._claude(phase=Phase.IMPLEM, phase_evidence=PhaseEvidence.EDITS)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), color=False, show_phase=True)
        assert "implem?" in out

    def test_empty_snapshot_renders_without_raising(self):
        assert isinstance(termview.render_terminal(Snapshot(generated_at=1.0, cards=[]),
                                                   color=False), str)

    def test_golden_output_for_a_mixed_session_grid(self):
        """Per-cell assertions can't catch alignment drift; a golden block can.

        One attached session with a Claude row (idle) and a zsh row, plus one
        detached session with a single zsh row, rendered at a fixed width with
        colour off so the expected text is exactly what a reviewer would see
        in a terminal.
        """
        idle_pane = self._claude(
            title="Build personal agent with calendar",
            activity=Activity.WAITING,
            waiting_reason=WaitingReason.IDLE,
            waiting_since=1000.0 - 15 * 3600,
            tasks=TaskProgress(known=True, total=12, completed=12),
        )
        attached = SessionCard(
            name="alfred",
            attached=True,
            activity=500.0,
            windows=[
                WindowRecord(0, 0, "claude", "/tmp", 1000.0 - 15 * 3600, idle_pane),
                WindowRecord(2, 0, "zsh", "/home/ezalos/42/Alfred", 1000.0 - 9 * 60, None),
            ],
        )
        detached = SessionCard(
            name="scratch",
            attached=False,
            activity=100.0,
            windows=[WindowRecord(0, 0, "zsh", "/tmp", 1000.0 - 30, None)],
        )
        out = termview.render_terminal(
            Snapshot(generated_at=1000.0, cards=[detached, attached]),
            width=100, color=False, now=1000.0,
        )
        expected = (
            "   WIN  CMD       QUIET  STATE        WAITING  TASKS  TITLE\n"
            "\n"
            "  scratch (detached)\n"
            "   0.0  zsh         30s  ·                  ·      ·  /tmp\n"
            "\n"
            "  alfred (attached)\n"
            "   0.0  claude      15h  ○ idle           15h  12/12  Build personal agent with calendar\n"
            "   2.0  zsh          9m  ·                  ·      ·  ~/42/Alfred\n"
            "\n"
            "   WIN  CMD       QUIET  STATE        WAITING  TASKS  TITLE\n"
        )
        assert out == expected
        assert all(termview.display_width(line) <= 100 for line in out.splitlines())

    def test_state_cell_is_pinned_per_reason_and_all_reasons_are_distinct(self):
        """Pins the exact 11-wide STATE cell for every WaitingReason plus the
        working case, including the clamp: `⚠ permission` is 12 columns wide
        against the 11-wide STATE column, so `_pad` truncates it to
        `⚠ permissi…`. The two existing PERMISSION tests only assert ANSI-code
        presence/absence, not this text, so nothing would fail if a future
        edit re-widened the column or re-collided the clamp with another
        label. Written generically over `WaitingReason` so a state added
        later must be pinned here too, and the real property that matters -
        clamping may shorten a label but must never make two states look
        identical - is asserted directly on the rendered set.
        """
        expected = {
            WaitingReason.PERMISSION: "⚠ permissi…",
            WaitingReason.QUESTION: "⚠ question ",
            WaitingReason.UNSENT_INPUT: "⚠ unsent   ",
            WaitingReason.IDLE: "○ idle     ",
        }
        assert set(expected) == set(WaitingReason), (
            "a WaitingReason was added without pinning its rendered STATE text here"
        )

        def state_cell(pane):
            w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
            out = termview.render_terminal(self._snap(w), color=False, now=1000.0)
            lines = out.splitlines()
            header = lines[0]
            row = [l for l in lines if "claude" in l][0]
            # Header and rows share one gutter (_GUTTER, Fix Round 3), so the
            # STATE cell starts at the same offset in both - no compensating
            # "-1" needed the way there was before the header/row gutters
            # were unified.
            start = header.index("STATE")
            return row[start:start + 11]

        cells = {
            reason: state_cell(self._claude(activity=Activity.WAITING, waiting_reason=reason,
                                            waiting_since=1000.0 - 3600))
            for reason in expected
        }
        cells["working"] = state_cell(self._claude(activity=Activity.WORKING))

        # The property that actually matters, checked first: distinct states
        # must never render to the same text, however the clamp shortens
        # them. Checking this ahead of the per-reason pins below means a
        # regression that collides two states is reported as a collision,
        # not masked by whichever pinned text happens to fail first.
        assert len(set(cells.values())) == len(cells), cells

        for reason, text in expected.items():
            assert cells[reason] == text, f"{reason}: expected {text!r}, got {cells[reason]!r}"
        assert cells["working"] == "◉ working  "

    def test_header_and_row_column_boundaries_come_from_the_same_source(self):
        """The real protection behind reading `_columns(show_phase)` in both
        `_header()` and `_row()`: a column's width can only be declared once,
        so the header and every data row necessarily place its boundary at
        the same offset. Before that refactor, `_row()` repeated its own
        literal widths (11/7/5, and a separate 5/8/5/[8] set in the
        non-Claude branch) instead of reading `_columns()`; widening a
        column in `_TAIL_COLUMNS` alone would move the header and leave
        every row where it was, and the golden test could only report "the
        header text changed", never why.

        Boundaries below are computed from `_columns()` itself - not
        hardcoded - so this test keeps working when a width legitimately
        changes; only a column whose row content lands somewhere `_columns`
        did not predict can fail it.

        The header and every data row now share one gutter (`_GUTTER`, Fix
        Round 3 - previously the header carried its own "  " literal while
        rows carried " ", so every header label sat one column right of the
        data it named even though the widths agreed). This test compares the
        header's column start position against each row's start position
        directly, using that single shared gutter length for both - no
        compensating "+1"/"-1" between them. If a fudge factor were needed
        here to make the assertions pass, the layout would be wrong, not the
        test - that was exactly the previous version's mistake: hardcoding
        `HEADER_GUTTER = 2` and `ROW_GUTTER = 1` let it pass while the
        header text visibly sat over the wrong column. Run for both
        show_phase values, since the phase column shifts every column
        after it.
        """
        gutter_len = len(termview._GUTTER)

        def slot_offsets(columns):
            """Column i's slot starts after the widths and "  " gaps of every earlier column."""
            pos, out = 0, []
            for _, width, _ in columns:
                out.append(pos)
                pos += width + 2
            return out

        for show_phase in (False, True):
            columns = termview._columns(show_phase)
            offsets = slot_offsets(columns)

            claude_pane = self._claude(
                phase=Phase.IMPLEM, phase_evidence=PhaseEvidence.SKILL,
                activity=Activity.WAITING, waiting_reason=WaitingReason.IDLE,
                waiting_since=1000.0 - 3600,
                tasks=TaskProgress(known=True, total=4, completed=3),
            )
            w = [
                WindowRecord(0, 0, "claude", "/tmp", 1000.0 - 300, claude_pane),
                WindowRecord(1, 0, "zsh", "/tmp", 1000.0 - 120, None),
            ]
            out = termview.render_terminal(self._snap(w), color=False,
                                           show_phase=show_phase, now=1000.0)
            lines = out.splitlines()
            header = lines[0]
            claude_row = [l for l in lines if "claude" in l][0]
            zsh_row = [l for l in lines if l.strip().startswith("1.0")][0]

            # Independently known expected raw values per column, in the
            # column's own alignment - built from what the fixtures above
            # declare, not read back from the renderer.
            claude_values = {"WIN": "0.0", "CMD": "claude", "QUIET": "5m",
                             "PHASE": "implem", "STATE": "○ idle", "WAITING": "1h",
                             "TASKS": "3/4"}
            zsh_values = {"WIN": "1.0", "CMD": "zsh", "QUIET": "2m",
                         "PHASE": termview.NONE_CELL, "STATE": termview.NONE_CELL,
                         "WAITING": termview.NONE_CELL, "TASKS": termview.NONE_CELL}

            for (name, width, right), offset in zip(columns, offsets):
                # One column start position, used unmodified against both the
                # header and every row - the literal "no compensating offset"
                # this test exists to enforce.
                col_start = gutter_len + offset

                header_cell = header[col_start: col_start + width]
                assert header_cell.strip() == name, (
                    f"show_phase={show_phase}: header column {name} not at start "
                    f"{col_start}: {header_cell!r}"
                )
                for row, values in ((claude_row, claude_values), (zsh_row, zsh_values)):
                    expected_cell = termview._pad(values[name], width, right)
                    actual_cell = row[col_start: col_start + width]
                    assert actual_cell == expected_cell, (
                        f"show_phase={show_phase}: row column {name} not at the "
                        f"header's start {col_start}: expected {expected_cell!r}, "
                        f"got {actual_cell!r}\nrow={row!r}"
                    )

    # --- Change 1: full session name, "@"-suffix dimmed --------------------

    def test_full_session_name_shown_with_at_suffix_dimmed_under_color(self):
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None)]
        snap = self._snap(w, name="alfred@2026-08-01-15h45")
        out = termview.render_terminal(snap, color=True, now=1000.0)
        name_line = [l for l in out.splitlines() if "alfred" in l][0]
        assert f"{termview.BOLD_CYAN}alfred{termview.RESET}" in name_line
        assert f"{termview.DIM}@2026-08-01-15h45{termview.RESET}" in name_line

    def test_full_session_name_is_complete_in_plain_text(self):
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None)]
        snap = self._snap(w, name="alfred@2026-08-01-15h45")
        out = termview.render_terminal(snap, color=False, now=1000.0)
        assert "alfred@2026-08-01-15h45" in out
        assert "\x1b[" not in out

    def test_session_name_without_at_has_nothing_dimmed(self):
        w = [WindowRecord(0, 0, "zsh", "/tmp", 1000.0, None)]
        snap = self._snap(w, name="alfred", attached=True)
        out = termview.render_terminal(snap, color=True, now=1000.0)
        name_line = [l for l in out.splitlines() if "alfred" in l][0]
        assert name_line == (
            f"  {termview.BOLD_CYAN}alfred{termview.RESET} "
            f"{termview.GREEN}(attached){termview.RESET}"
        )
        assert termview.DIM not in name_line

    # --- Change 2: header repeated at the bottom ----------------------------

    def test_header_appears_exactly_twice_and_identically(self):
        pane = self._claude(
            title="Build personal agent with calendar, mail, and task management " * 3
        )
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane),
             WindowRecord(2, 0, "zsh", "/tmp", 1000.0, None)]
        out = termview.render_terminal(self._snap(w), width=65, color=False, now=1000.0)
        expected_header = termview._GUTTER + termview._header(False)
        lines = out.splitlines()
        occurrences = [l for l in lines if l == expected_header]
        assert len(occurrences) == 2
        assert lines[0] == expected_header
        assert lines[-1] == expected_header

    # --- Change 3: word-wrapped titles instead of truncation ----------------

    def test_wrap_text_breaks_on_word_boundaries(self):
        frags = termview._wrap_text("alpha beta gamma delta epsilon zeta",
                                    first_budget=15, rest_budget=30)
        assert frags == ["alpha beta", "gamma delta epsilon zeta"]

    def test_wrap_text_first_line_budget_just_below_fifteen_gets_no_fragment(self):
        """Round 2 fix: a fitting word on a budget under 15 still reads as an
        orphan ("Improve" alone, real content one line below) - worse than
        no fragment at all. 14 is deliberately one under the floor.
        """
        title = "Improve reproducible evals with visual inspection dashboard"
        frags = termview._wrap_text(title, first_budget=14, rest_budget=57)
        assert frags[0] == ""
        assert frags[1].startswith("Improve")

    def test_wrap_text_first_line_budget_at_fifteen_gets_a_fragment(self):
        """Same title, budget exactly at the 15-column floor: the first word
        fits and the budget is wide enough, so the row line gets it.
        """
        title = "Improve reproducible evals with visual inspection dashboard"
        frags = termview._wrap_text(title, first_budget=15, rest_budget=57)
        assert frags[0] == "Improve"

    def test_wrap_text_unbreakable_token_whole_on_continuation_when_it_fits_there(self):
        """A token wider than the first-line budget but narrower than the
        continuation width must never be hard-broken just because it didn't
        fit on the row line - it goes whole onto the continuation line.
        """
        token = "supercalifragilisticexpialidocious"  # 35 columns
        frags = termview._wrap_text(token, first_budget=15, rest_budget=40)
        assert frags == ["", token]

    def test_wrap_text_hard_breaks_a_token_too_long_for_the_continuation_width(self):
        """The genuinely pathological case: even the continuation line isn't
        wide enough, so the hard-break fallback still has to fire.
        """
        word = "supercalifragilisticexpialidocious"
        frags = termview._wrap_text(word, first_budget=15, rest_budget=8)
        assert frags[0] == ""
        assert all(len(f) <= 8 for f in frags[1:])
        assert "".join(frags) == word  # nothing dropped, nothing added

    def test_wrap_text_uses_display_width_not_len_for_cjk(self):
        title = "日本語のタイトルです" * 5  # no spaces: CJK routinely has none
        frags = termview._wrap_text(title, first_budget=10, rest_budget=12)
        assert termview.display_width(frags[0]) <= 10
        assert all(termview.display_width(f) <= 12 for f in frags[1:])
        assert "".join(frags) == title

    def test_wrap_text_leaves_first_line_empty_when_budget_is_zero(self):
        frags = termview._wrap_text("Build personal agent", first_budget=0, rest_budget=57)
        assert frags[0] == ""
        assert frags[1] == "Build personal agent"

    def test_row_has_no_title_fragment_when_first_line_budget_is_too_small(self):
        """`_wrap_text` treats a first-line budget under 15 columns as no
        room at all (a fitting word there would still read as an orphan)
        and starts the title on the first continuation line instead. Width
        58 puts the computed budget at 4: fixed(41) + gaps(12) + 1 = 54.
        """
        pane = self._claude(title="Build personal agent with calendar")
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        width = 58
        out = termview.render_terminal(self._snap(w), width=width, color=False, now=1000.0)
        lines = out.splitlines()
        row_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith("0.0"))
        row_line = lines[row_idx]
        assert row_line.rstrip() == row_line, "no title fragment should trail the fixed columns"
        cmd_col = termview._continuation_indent(False)
        cont_line = lines[row_idx + 1]
        assert cont_line[:cmd_col] == " " * cmd_col
        assert cont_line[cmd_col:].startswith("Build")

    def test_width_65_orphan_word_title_has_no_row_fragment(self):
        """Round 2 fix, the exact reported case: at width 65 the first-line
        budget is 11 - a fitting word ("Improve") would still be an orphan,
        real content one line below it. The row must carry none of the
        title, and the title must begin on the first continuation line.
        """
        title = "Improve reproducible evals with visual inspection dashboard"
        pane = self._claude(title=title)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        width = 65
        out = termview.render_terminal(self._snap(w), width=width, color=False, now=1000.0)
        lines = out.splitlines()
        row_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith("0.0"))
        row_line = lines[row_idx]
        assert row_line.rstrip() == row_line, "no title fragment should trail the fixed columns"
        assert "Improve" not in row_line

        columns = termview._columns(False)
        fixed = sum(w for _, w, _ in columns)
        gaps = 2 * len(columns)
        title_budget = width - fixed - gaps - 1
        cmd_col = termview._continuation_indent(False)
        cont_budget = width - cmd_col
        expected_frags = termview._wrap_text(title, title_budget, cont_budget)
        assert expected_frags[0] == ""  # sanity: this really is the no-room case

        cont_lines = []
        i = row_idx + 1
        while lines[i] != "":
            cont_lines.append(lines[i])
            i += 1
        assert [cl[cmd_col:] for cl in cont_lines] == expected_frags[1:]
        assert all(cl[:cmd_col] == " " * cmd_col for cl in cont_lines)
        assert cont_lines[0][cmd_col:].startswith("Improve reproducible")

    def test_width_120_same_title_starts_on_the_row_line(self):
        """Pins that the narrow-case rule hasn't leaked into the normal
        case: at width 120 the budget is ~66, wide enough that the title
        (59 columns) fits on the row line exactly as it does today.
        """
        title = "Improve reproducible evals with visual inspection dashboard"
        pane = self._claude(title=title)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        out = termview.render_terminal(self._snap(w), width=120, color=False, now=1000.0)
        lines = out.splitlines()
        row_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith("0.0"))
        assert title in lines[row_idx]

    def test_unbreakable_cwd_token_appears_whole_on_continuation_never_split(self):
        """The exact reported case for the non-Claude branch: `~/42/V-Jaygent`
        is one 14-column token. At width 65 it doesn't fit the 11-column row
        budget, but it fits easily in the 57-column continuation width, so
        it must land there whole - never hard-broken into `V-Jayg` / `ent`.
        """
        w = [WindowRecord(1, 0, "zsh", "/home/ezalos/42/V-Jaygent", 1000.0, None)]
        out = termview.render_terminal(self._snap(w), width=65, color=False, now=1000.0)
        lines = out.splitlines()
        row_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith("1.0"))
        assert lines[row_idx].rstrip() == lines[row_idx]
        cmd_col = termview._continuation_indent(False)
        cont_line = lines[row_idx + 1]
        assert cont_line[cmd_col:] == "~/42/V-Jaygent"

    def test_long_claude_title_wraps_with_continuation_at_cmd_column_start(self):
        title = ("Build personal agent with calendar, mail, and task management "
                 "for Louis across every project and channel without dropping "
                 "anything important along the way")
        pane = self._claude(title=title)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        width = 65
        out = termview.render_terminal(self._snap(w), width=width, color=False, now=1000.0)
        lines = out.splitlines()
        cmd_col = termview._continuation_indent(False)
        row_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith("0.0"))

        cont_lines = []
        i = row_idx + 1
        while lines[i] != "":
            cont_lines.append(lines[i])
            i += 1

        assert len(cont_lines) >= 2, "this title should need more than one continuation line"
        for cl in cont_lines:
            # Computed from `_columns` via `_continuation_indent`, not a literal:
            # every continuation line must start exactly there, no more no less.
            assert cl[:cmd_col] == " " * cmd_col
            assert cl[cmd_col] != " "

        # The wrap must not drop or duplicate any word.
        columns = termview._columns(False)
        fixed = sum(w for _, w, _ in columns)
        gaps = 2 * len(columns)
        frag0_start = len(termview._GUTTER) + fixed + gaps
        row_frag = lines[row_idx][frag0_start:]
        # At width 65 the first-line budget (11) is under the 15-column
        # floor, so this title - like the reported case - carries no
        # fragment on the row line at all; it starts on the continuation.
        assert row_frag == ""
        reconstructed = " ".join([row_frag] + [cl[cmd_col:] for cl in cont_lines])
        assert reconstructed.split() == title.split()

    def test_long_non_claude_cwd_wraps_like_a_title(self):
        long_cwd = ("/home/ezalos/42/some/deeply/nested/project/directory/that/keeps/"
                    "going/and/going/and/going/without/end")
        w = [WindowRecord(2, 0, "zsh", long_cwd, 940.0, None)]
        width = 65
        out = termview.render_terminal(self._snap(w), width=width, color=False, now=1000.0)
        lines = out.splitlines()
        cmd_col = termview._continuation_indent(False)
        row_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith("2.0"))

        cont_lines = []
        i = row_idx + 1
        while lines[i] != "":
            cont_lines.append(lines[i])
            i += 1

        assert cont_lines, "a cwd this long should wrap onto at least one continuation line"
        for cl in cont_lines:
            assert cl[:cmd_col] == " " * cmd_col
        assert all(termview.display_width(l) <= width for l in out.splitlines())

    def test_no_rendered_line_exceeds_width_at_common_terminal_widths(self):
        """The phone case: at width 65 the old `max(20, ...)` floor forced a
        74-column-wide row regardless of the real width, and the terminal
        hard-wrapped mid-word. Pinned explicitly at 65, 80, 100 and 200 over
        a long title so a regression of the floor - or of the wrap budget -
        is caught here, not just in the field.
        """
        idle_pane = self._claude(
            title="Build personal agent with calendar, mail, and task management",
            activity=Activity.WAITING,
            waiting_reason=WaitingReason.IDLE,
            waiting_since=1000.0 - 19 * 3600,
        )
        w = [
            WindowRecord(0, 0, "claude", "/tmp", 1000.0 - 19 * 3600, idle_pane),
            WindowRecord(2, 0, "zsh", "/home/ezalos/42/Alfred", 1000.0 - 19 * 3600, None),
        ]
        for width in (65, 80, 100, 200):
            out = termview.render_terminal(self._snap(w), width=width, color=False, now=1000.0)
            overflowing = [l for l in out.splitlines() if termview.display_width(l) > width]
            assert overflowing == [], f"width={width}: {overflowing!r}"

    def test_cjk_emoji_title_respects_the_budget_through_render(self):
        pane = self._claude(title="日本語のタイトルです🎯" * 4)
        w = [WindowRecord(0, 0, "claude", "/tmp", 1000.0, pane)]
        for width in (65, 80, 100, 200):
            out = termview.render_terminal(self._snap(w), width=width, color=False, now=1000.0)
            overflowing = [l for l in out.splitlines() if termview.display_width(l) > width]
            assert overflowing == [], f"width={width}: {overflowing!r}"


# --- tls: the CLI command wiring collect() and render_terminal() together --
from agents_dashboard import __main__ as main_mod
import json as _json


class TestTlsCommand:
    def test_passes_show_phase_and_skips_the_scan_by_default(self, monkeypatch, capsys):
        # A snapshot with no cards would trip the "No tmux sessions" guard
        # (see test_no_tmux_server_exits_nonzero_with_the_old_message below)
        # before render_terminal ever runs, so this fixture needs at least
        # one card - its content is irrelevant, only the kwargs seen matter.
        seen = {}
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: seen.update(kw) or
                            Snapshot(generated_at=1.0, cards=[SessionCard(name="s")]))
        monkeypatch.setattr(main_mod, "render_terminal",
                            lambda snap, **kw: seen.update(kw) or "GRID\n")
        main_mod.tls()
        assert seen["with_phase"] is False
        assert seen["show_phase"] is False
        assert "GRID" in capsys.readouterr().out

    def test_phase_flag_enables_both_the_scan_and_the_column(self, monkeypatch, capsys):
        seen = {}
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: seen.update(kw) or
                            Snapshot(generated_at=1.0, cards=[SessionCard(name="s")]))
        monkeypatch.setattr(main_mod, "render_terminal",
                            lambda snap, **kw: seen.update(kw) or "GRID\n")
        main_mod.tls(phase=True)
        assert seen["with_phase"] is True
        assert seen["show_phase"] is True

    def test_json_output_includes_non_claude_windows(self, monkeypatch, capsys):
        card = SessionCard(name="s", windows=[
            WindowRecord(0, 0, "zsh", "/tmp", 5.0, None)], attached=True)
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: Snapshot(generated_at=1.0, cards=[card]))
        main_mod.tls(json=True)
        payload = _json.loads(capsys.readouterr().out)
        window = payload["sessions"][0]["windows"][0]
        assert window["command"] == "zsh"
        assert window["claude"] is None

    def test_no_tmux_server_exits_nonzero_with_the_old_message(self, monkeypatch, capsys):
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: Snapshot(generated_at=1.0, cards=[]))
        with pytest.raises(SystemExit) as exc:
            main_mod.tls()
        assert exc.value.code == 1
        assert "No tmux sessions" in capsys.readouterr().err

    @pytest.mark.parametrize("is_tty, no_color_set, expected_color", [
        (True, False, True),    # a real terminal, nothing suppressing colour
        (True, True, False),    # a real terminal, but NO_COLOR opts out
        (False, False, False),  # piped/redirected - never colour, regardless of NO_COLOR
        (False, True, False),   # piped AND NO_COLOR set - still no colour
    ])
    def test_color_requires_both_a_tty_and_no_color_unset(
        self, monkeypatch, capsys, is_tty, no_color_set, expected_color
    ):
        # Pins `color=isatty() and NO_COLOR is unset` as a two-condition AND.
        # Verified by mutation: swapping the `and` for `or` fails rows 2 and 3
        # (tty=True/NO_COLOR-set and tty=False/NO_COLOR-unset - the two rows
        # where the conditions disagree); dropping the NO_COLOR check
        # entirely (color=isatty()) fails row 2 alone. Row 4 (piped AND
        # NO_COLOR set) never disagrees with either mutation on its own, but
        # without it a future change that flips just the "piped" half of the
        # logic while leaving NO_COLOR alone could regress silently - a full
        # combination table is what makes that visible, not any single row.
        seen = {}
        monkeypatch.setattr(main_mod, "collect",
                            lambda **kw: Snapshot(generated_at=1.0,
                                                  cards=[SessionCard(name="s")]))
        monkeypatch.setattr(main_mod, "render_terminal",
                            lambda snap, **kw: seen.update(kw) or "GRID\n")
        monkeypatch.setattr(main_mod.sys.stdout, "isatty", lambda: is_tty)
        if no_color_set:
            monkeypatch.setenv("NO_COLOR", "1")
        else:
            monkeypatch.delenv("NO_COLOR", raising=False)

        main_mod.tls()
        assert seen["color"] is expected_color


# --- dotfiles/bin/tls: the deployed wrapper, run as a real subprocess ------
# Every test above calls tls() in-process, under pytest's own cwd (this
# repo). That structurally cannot exhibit the wrapper bug: `uv run --project`
# (no `--directory`) picks the right venv but leaves cwd wherever the caller
# was, and pyproject.toml has no [build-system] so agents_dashboard is never
# installed into .venv - `python -m agents_dashboard` only resolves via cwd.
# The wrapper worked only when invoked from ~/Setup itself. This runs the
# actual script, as a subprocess, from a directory that is not this repo.
class TestTlsWrapperSubprocess:
    WRAPPER = "/home/ezalos/Setup/dotfiles/bin/tls"

    def _tmux_server_is_up(self):
        try:
            result = subprocess.run(["tmux", "list-sessions"],
                                    capture_output=True, timeout=5)
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def test_wrapper_renders_the_grid_from_a_foreign_cwd(self, tmp_path):
        if not self._tmux_server_is_up():
            pytest.skip("no tmux server running on this machine")
        result = subprocess.run([self.WRAPPER], cwd=tmp_path,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "WIN" in result.stdout

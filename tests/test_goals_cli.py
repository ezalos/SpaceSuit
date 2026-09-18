# ABOUTME: Tests for `python -m goals report` as a real subprocess on real files: exit codes,
# ABOUTME: what it writes, what it refuses, and what it never echoes into an error.

import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tests.test_goals_report import NOW, cost, goal

ROOT = Path(__file__).resolve().parent.parent


def run(args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "goals", *args],
                          cwd=str(cwd), capture_output=True, text=True)


def inputs(tmp_path, g=None, c=None):
    goal_path, cost_path = tmp_path / "goal.json", tmp_path / "cost.json"
    goal_path.write_text(json.dumps(g or goal()))
    cost_path.write_text(json.dumps(c or cost(), default=str))
    return goal_path, cost_path


def test_it_writes_a_real_report_and_exits_zero(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    out = tmp_path / "report.json"
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(out), "--now", NOW])
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text())
    assert report["goal"]["id"] == "g-1"
    assert report["generated_utc"] == NOW


def test_the_report_file_is_private(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    out = tmp_path / "report.json"
    run(["report", "--goal", str(goal_path), "--cost", str(cost_path), "--out", str(out)])
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_it_writes_html_when_asked(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    out, page = tmp_path / "report.json", tmp_path / "report.html"
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(out), "--html", str(page), "--now", NOW])
    assert proc.returncode == 0, proc.stderr
    assert page.read_text().lstrip().lower().startswith("<!doctype html")
    assert "A bounded goal" in page.read_text()


def test_it_writes_no_html_unless_asked(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    out = tmp_path / "report.json"
    run(["report", "--goal", str(goal_path), "--cost", str(cost_path), "--out", str(out)])
    assert not list(tmp_path.glob("*.html"))


def test_without_now_it_uses_the_real_clock_and_invents_no_authorization(tmp_path):
    goal_path, cost_path = inputs(tmp_path, goal(authorized_utc=None))
    out = tmp_path / "report.json"
    assert run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(out)]).returncode == 0
    report = json.loads(out.read_text())
    generated = datetime.fromisoformat(report["generated_utc"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - generated).total_seconds()) < 120
    assert report["time"]["authorized_utc"] is None
    assert report["time"]["elapsed_open"]["known"] is False


def test_an_unsupported_snapshot_exits_two_with_a_concise_cause(tmp_path):
    snap = cost()
    snap["engine"] = {"costCacheVersion": 4, "timePrecision": "utc-day"}
    goal_path, cost_path = inputs(tmp_path, None, snap)
    out = tmp_path / "report.json"
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path), "--out", str(out)])
    assert proc.returncode == 2
    assert "costCacheVersion" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) <= 2
    assert not out.exists()


def test_an_error_never_echoes_the_private_payload(tmp_path):
    g = goal()
    g["outcome"]["status"] = "not-a-status"
    g["goal_id"] = "token-s3cr3t-in-the-manifest"
    goal_path, cost_path = inputs(tmp_path, g)
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(tmp_path / "report.json")])
    assert proc.returncode == 2
    assert "s3cr3t" not in proc.stderr and "s3cr3t" not in proc.stdout


def test_a_missing_input_exits_two(tmp_path):
    _, cost_path = inputs(tmp_path)
    proc = run(["report", "--goal", str(tmp_path / "absent.json"), "--cost", str(cost_path),
                "--out", str(tmp_path / "report.json")])
    assert proc.returncode == 2
    assert "goal manifest" in proc.stderr


def test_it_refuses_to_write_the_report_over_one_of_its_inputs(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    before = cost_path.read_text()
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(cost_path)])
    assert proc.returncode == 2
    assert "read-only" in proc.stderr or "input" in proc.stderr
    assert cost_path.read_text() == before


def test_it_refuses_to_write_the_html_over_one_of_its_inputs(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    before = goal_path.read_text()
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(tmp_path / "report.json"), "--html", str(goal_path)])
    assert proc.returncode == 2
    assert goal_path.read_text() == before


def test_a_failed_run_leaves_an_existing_report_intact(tmp_path):
    snap = cost()
    snap["scope"]["roots"] = ["some-other-root"]
    goal_path, cost_path = inputs(tmp_path, None, snap)
    out = tmp_path / "report.json"
    out.write_text('{"previous": true}')
    assert run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(out)]).returncode == 2
    assert json.loads(out.read_text()) == {"previous": True}


def test_an_invalid_now_exits_two(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(tmp_path / "report.json"), "--now", "tea time"])
    assert proc.returncode == 2
    assert "now_utc" in proc.stderr


def test_no_subcommand_exits_two_and_names_the_ones_it_has():
    proc = run([])
    assert proc.returncode == 2
    assert "report" in (proc.stderr + proc.stdout)


def test_the_help_text_states_what_the_reporter_does_not_do():
    # `never|not` matched almost any help text. Name the disclaimers that have to be there.
    proc = run(["report", "--help"])
    assert proc.returncode == 0
    for claim in ("does NOT collect usage", "re-verify any acceptance claim",
                  "never as cash", "cost engine"):
        assert claim in proc.stdout, claim


def test_success_names_the_files_it_wrote(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    out, page = tmp_path / "report.json", tmp_path / "report.html"
    proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                "--out", str(out), "--html", str(page), "--now", NOW])
    assert str(out) in proc.stdout and str(page) in proc.stdout


def test_an_unwritable_destination_exits_two_without_a_traceback(tmp_path):
    goal_path, cost_path = inputs(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        proc = run(["report", "--goal", str(goal_path), "--cost", str(cost_path),
                    "--out", str(locked / "report.json"), "--now", NOW])
        assert proc.returncode == 2
        assert "Traceback" not in proc.stderr
    finally:
        os.chmod(locked, 0o700)

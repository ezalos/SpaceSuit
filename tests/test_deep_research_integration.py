# ABOUTME: End to end test spawning a real claude --bg session and awaiting its files.
# ABOUTME: Deliberately trivial charter so the run costs seconds, not a real research run.

import shutil
import time
from pathlib import Path

import pytest

from deep_research.charter import Charter
from deep_research.launcher import launch
from deep_research.status import DONE_SENTINEL, REPORT_NAME, RunState, resolve_state

pytestmark = pytest.mark.slow

TIMEOUT_SECONDS = 300
POLL_SECONDS = 3

TRIVIAL = Charter(
    question="What is two plus two",
    decision="Proving the detached run pipeline delivers files.",
    must_answer=("What is two plus two",),
    source_tier="no sources needed, answer from arithmetic",
    recency="not applicable",
    deliverable=(
        "One short sentence. Do not search the web. Do not cite anything. "
        "Write an empty sources.md and a run-result.json with zero sources."
    ),
    out_of_scope=("anything requiring research",),
)


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_a_real_background_run_writes_the_output_contract(tmp_path):
    out = tmp_path / "run"
    manifest = launch(TRIVIAL, out_dir=out, runs_root=tmp_path)
    assert manifest.bg_session_id

    sentinel = out / DONE_SENTINEL
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline and not sentinel.exists():
        time.sleep(POLL_SECONDS)

    assert sentinel.exists(), (
        f"no DONE sentinel after {TIMEOUT_SECONDS}s; "
        f"directory holds: {sorted(p.name for p in out.iterdir())}"
    )
    assert (out / REPORT_NAME).read_text(encoding="utf-8").strip()
    assert resolve_state(out, session_alive=False) is RunState.DONE

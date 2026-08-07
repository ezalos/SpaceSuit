# ABOUTME: Tests tmux-save.sh / tmux-restore.sh against a real, isolated tmux server.
# ABOUTME: Guards the destructive-wipe and cron/systemd-PATH regressions, plus a save→restore round trip.

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SAVE = SCRIPTS / "tmux-save.sh"
RESTORE = SCRIPTS / "tmux-restore.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)


@pytest.fixture
def tmux_env(tmp_path):
    """Env that points tmux at a private socket and the save scripts at temp dirs.

    Every test gets its own tmux server (via a short TMUX_TMPDIR) and its own
    save/log paths, so the suite never touches the user's real ~/.tmux-save or
    their live tmux server. The socket dir is kept short to stay under the unix
    socket path-length limit; pytest's tmp_path can be too long for that.

    TMUX_SAVE_HISTORY_DIR must be overridden too. Without it tmux-save.sh falls
    back to ~/.tmux-save-history, so every save test wrote a junk snapshot (with
    a pytest tmp path as its cwd) into the real rolling history AND ran the
    retention prune over it, evicting real snapshots from the newest-8 tier.
    """
    sock_tmp = Path(tempfile.mkdtemp(prefix="tmuxtest-"))
    env = os.environ.copy()
    env["TMUX_TMPDIR"] = str(sock_tmp)
    env["TMUX_SAVE_DIR"] = str(tmp_path / "tmux-save")
    env["TMUX_SAVE_LOG"] = str(tmp_path / "tmux-save.log")
    env["TMUX_SAVE_HISTORY_DIR"] = str(tmp_path / "tmux-save-history")
    env.pop("TMUX", None)  # don't inherit an outer tmux server (we run inside one a lot)
    yield env
    subprocess.run(["tmux", "kill-server"], env=env, capture_output=True, text=True)
    shutil.rmtree(sock_tmp, ignore_errors=True)


def tmux(env, *args):
    return subprocess.run(["tmux", *args], env=env, capture_output=True, text=True)


def run_save(env, extra_env=None):
    e = dict(env)
    if extra_env:
        e.update(extra_env)
    return subprocess.run(
        [str(SAVE)], env=e, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=60,
    )


def run_restore(env, *args):
    return subprocess.run(
        [str(RESTORE), *args], env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=60,
    )


def test_scripts_are_executable():
    for s in (SAVE, RESTORE):
        assert s.exists(), f"{s} not found"
        assert os.access(s, os.X_OK), f"{s} is not executable"


def test_fixture_isolates_the_real_rolling_history(tmux_env, tmp_path):
    """Regression: the suite must never write into ~/.tmux-save-history.

    tmux-save.sh defaults TMUX_SAVE_HISTORY_DIR to $HOME/.tmux-save-history and
    prunes it on every run. With the fixture overriding only TMUX_SAVE_DIR, save
    tests dropped junk snapshots into the real history and evicted real ones.
    """
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0
    real_history = Path.home() / ".tmux-save-history"
    before = sorted(p.name for p in real_history.glob("*")) if real_history.is_dir() else []

    assert run_save(tmux_env).returncode == 0

    after = sorted(p.name for p in real_history.glob("*")) if real_history.is_dir() else []
    assert after == before, "the save leaked into the real rolling history"
    assert list(Path(tmux_env["TMUX_SAVE_HISTORY_DIR"]).glob("*/state.tsv")), \
        "the save should have written to the isolated history instead"


def test_save_with_no_server_preserves_existing_save(tmux_env):
    """Regression: a save run while no tmux server is up must NOT destroy the
    previous good snapshot. (Old code wiped the save dir before checking.)"""
    save_dir = Path(tmux_env["TMUX_SAVE_DIR"])
    (save_dir / "pane_contents").mkdir(parents=True)
    (save_dir / "state.tsv").write_text("SENTINEL-GOOD-SAVE\n")
    (save_dir / "saved_at").write_text("2026-01-01 00:00:00\n")

    # No session created on this isolated socket → no server running.
    result = run_save(tmux_env)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "no tmux server" in result.stdout.lower()
    # The good save must still be intact.
    assert (save_dir / "state.tsv").read_text() == "SENTINEL-GOOD-SAVE\n"


def test_save_captures_running_session(tmux_env, tmp_path):
    """Happy path: a running session is written to a complete snapshot, the log
    is appended, and no staging dir is left behind."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0

    result = run_save(tmux_env)

    assert result.returncode == 0, (result.stdout, result.stderr)
    save_dir = Path(tmux_env["TMUX_SAVE_DIR"])
    assert (save_dir / "saved_at").exists()
    assert "alpha" in (save_dir / "state.tsv").read_text()
    assert Path(tmux_env["TMUX_SAVE_LOG"]).exists()
    staging = save_dir.parent / (save_dir.name + ".staging")
    assert not staging.exists(), "staging dir should be swapped away, not left behind"


def test_save_succeeds_with_minimal_path(tmux_env, tmp_path):
    """Regression: under cron / the systemd shutdown unit, PATH is minimal and
    `rip` (in a user bin dir) is not on it. The save must still succeed because
    the script re-adds the user bin dirs itself."""
    rip = shutil.which("rip")
    if rip is None:
        pytest.skip("rip not installed")

    # Build a PATH that has tmux but NOT rip, so we isolate the PATH fix.
    rip_dir = os.path.dirname(rip)
    tmux_dir = os.path.dirname(shutil.which("tmux"))
    seen, minimal = set(), []
    for p in ["/usr/bin", "/bin", "/usr/sbin", "/sbin", tmux_dir]:
        if p != rip_dir and p not in seen:
            seen.add(p)
            minimal.append(p)
    minimal_path = ":".join(minimal)
    if shutil.which("tmux", path=minimal_path) is None:
        pytest.skip("cannot isolate rip from tmux on this PATH layout")
    if shutil.which("rip", path=minimal_path) is not None:
        pytest.skip("rip reachable without user bin dirs; cannot test the PATH fix")

    # Pre-seed an existing save so the swap-time `rip` of the old save fires.
    save_dir = Path(tmux_env["TMUX_SAVE_DIR"])
    (save_dir / "pane_contents").mkdir(parents=True)
    (save_dir / "state.tsv").write_text("old\n")
    assert tmux(tmux_env, "new-session", "-d", "-s", "beta", "-c", str(tmp_path)).returncode == 0

    result = run_save(tmux_env, extra_env={"PATH": minimal_path})

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "beta" in (save_dir / "state.tsv").read_text()


def test_restore_recreates_sessions(tmux_env, tmp_path):
    """Round trip: save two plain sessions, kill them, restore → both return."""
    wd = str(tmp_path)
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", wd).returncode == 0
    assert tmux(tmux_env, "new-session", "-d", "-s", "beta", "-c", wd).returncode == 0
    assert run_save(tmux_env).returncode == 0

    tmux(tmux_env, "kill-server")

    result = run_restore(tmux_env, "-c", "0")  # -c 0: skip scrollback replay

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert tmux(tmux_env, "has-session", "-t", "alpha").returncode == 0
    assert tmux(tmux_env, "has-session", "-t", "beta").returncode == 0


def test_restore_with_no_saved_state_errors(tmux_env):
    """No snapshot on disk → restore exits cleanly with a clear message."""
    result = run_restore(tmux_env, "-c", "0")
    assert result.returncode == 1
    assert "no saved state" in result.stdout.lower()


def run_save_args(env, *args):
    return subprocess.run(
        [str(SAVE), *args], env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=60,
    )


def test_save_records_origin_cron_when_not_a_tty(tmux_env, tmp_path):
    """No flag and no tty (cron, systemd) must record origin=cron."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0
    assert run_save(tmux_env).returncode == 0
    assert (Path(tmux_env["TMUX_SAVE_DIR"]) / "origin").read_text().strip() == "cron"


def test_save_records_explicit_origin(tmux_env, tmp_path):
    """--origin wins over the tty heuristic."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0
    result = run_save_args(tmux_env, "--origin", "shutdown")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (Path(tmux_env["TMUX_SAVE_DIR"]) / "origin").read_text().strip() == "shutdown"


def test_save_rejects_unknown_origin(tmux_env, tmp_path):
    """A typo must fail loudly rather than silently record garbage."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0
    result = run_save_args(tmux_env, "--origin", "bogus")
    assert result.returncode != 0
    assert "bogus" in (result.stdout + result.stderr)


def test_origin_propagates_into_history(tmux_env, tmp_path):
    """History snapshots are cp -a copies, so the marker must come along."""
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0
    result = run_save_args(tmux_env, "--origin", "manual")
    assert result.returncode == 0, (result.stdout, result.stderr)
    copies = sorted(Path(tmux_env["TMUX_SAVE_HISTORY_DIR"]).glob("*/origin"))
    assert len(copies) == 1
    assert copies[0].read_text().strip() == "manual"


def fiemap(path):
    """filefrag -v output for one file, or None if we can't judge this filesystem.

    Returns None (→ skip) when filefrag is missing or the file does not live on
    ext4, because `delalloc` is an ext4 extent flag and other filesystems answer
    the durability question differently.
    """
    if shutil.which("filefrag") is None:
        return None
    r = subprocess.run(
        ["filefrag", "-v", str(path)], capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0 or "ef53" not in r.stdout:  # ef53 == ext4
        return None
    return r.stdout


def test_save_is_durable_before_it_returns(tmux_env, tmp_path):
    """The snapshot must be on stable storage by the time tsave exits.

    The shutdown save is the most valuable one and the most exposed: it runs
    seconds before the machine loses power. Written with plain buffered I/O it
    sits in page cache with no blocks allocated (ext4 delayed allocation), so an
    unclean stop brings every file back zero-length — the file names were
    journaled, their contents never were.

    That is exactly what destroyed the 2026-08-06 11:02:06 shutdown snapshot: a
    complete 29-pane save (the run's own summary counted 29 lines back out of
    state.tsv), power cut at 11:02:09, and every file in both the save and its
    history copy came back 0 bytes. `~/.tmux-save.log` was the sole survivor,
    because it alone is written via a temp-file+rename, which trips ext4's
    auto_da_alloc heuristic and forces its data out.

    `delalloc` in the extent flags means "no blocks on disk yet" — the precise
    state that loses the data.
    """
    assert tmux(tmux_env, "new-session", "-d", "-s", "alpha", "-c", str(tmp_path)).returncode == 0
    assert run_save(tmux_env).returncode == 0

    save_dir = Path(tmux_env["TMUX_SAVE_DIR"])
    files = [save_dir / "state.tsv", save_dir / "saved_at", save_dir / "origin"]
    files += sorted((save_dir / "pane_contents").glob("*.txt"))
    # The rolling history copy has to survive the same power cut: it is the
    # fallback you reach for precisely when the live save was the one lost.
    history = Path(tmux_env["TMUX_SAVE_HISTORY_DIR"])
    files += sorted(history.glob("*/state.tsv")) + sorted(history.glob("*/pane_contents/*.txt"))
    assert files, "nothing was saved, so there is nothing to check"

    checked = 0
    for f in files:
        out = fiemap(f)
        if out is None:
            pytest.skip("need filefrag and an ext4 save dir to judge durability")
        assert "delalloc" not in out, (
            f"{f.name} still had unallocated (page-cache-only) blocks when the "
            f"save returned; a power cut here loses it:\n{out}"
        )
        checked += 1
    assert checked == len(files)


def test_restore_rejects_an_empty_snapshot_and_names_a_usable_one(tmux_env, tmp_path):
    """An empty snapshot must send you to a good one, not quietly restore nothing.

    A power cut can leave the live save as a full set of correctly-named but
    zero-length files. `trestore` used to accept that (state.tsv exists, so the
    -f check passed), read no rows, and report "Restored 0 session(s)" — leaving
    no clue that the rolling history held a good snapshot from minutes earlier.
    """
    save_dir = Path(tmux_env["TMUX_SAVE_DIR"])
    (save_dir / "pane_contents").mkdir(parents=True)
    (save_dir / "state.tsv").write_text("")
    (save_dir / "saved_at").write_text("")
    (save_dir / "origin").write_text("")

    good = Path(tmux_env["TMUX_SAVE_HISTORY_DIR"]) / "2026-08-06_11-00-03"
    (good / "pane_contents").mkdir(parents=True)
    (good / "state.tsv").write_text(
        f"alpha\t0\tzsh\tlayout\t0\t{tmp_path}\t0\t1\t\n"
    )
    (good / "saved_at").write_text("2026-08-06 11:00:03\n")
    # An older empty one must not be offered ahead of the good one.
    stale = Path(tmux_env["TMUX_SAVE_HISTORY_DIR"]) / "2026-08-06_11-02-06"
    (stale / "pane_contents").mkdir(parents=True)
    (stale / "state.tsv").write_text("")

    result = run_restore(tmux_env, "-c", "0")

    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "empty" in out.lower(), out
    assert "2026-08-06_11-00-03" in out, out
    assert "2026-08-06_11-02-06" not in out, out
    assert tmux(tmux_env, "has-session", "-t", "alpha").returncode != 0, \
        "an unusable snapshot must not half-restore anything"


def test_restore_reports_when_no_snapshot_is_usable(tmux_env):
    """Everything on disk zeroed: say so plainly rather than name a fallback."""
    save_dir = Path(tmux_env["TMUX_SAVE_DIR"])
    (save_dir / "pane_contents").mkdir(parents=True)
    (save_dir / "state.tsv").write_text("")

    result = run_restore(tmux_env, "-c", "0")

    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "empty" in out.lower(), out
    assert "no usable snapshot" in out.lower(), out


def test_restore_no_longer_contains_the_inline_prompt_loop():
    """Guard against the old loop being reintroduced alongside the new table."""
    text = RESTORE.read_text()
    assert "Resume claude --resume" not in text
    assert "[s]aved / [l]atest / [p]icker" not in text
    assert "claude_resume" in text


def test_shutdown_unit_marks_its_origin():
    unit = SCRIPTS / "tmux-save-on-shutdown.service"
    assert "--origin shutdown" in unit.read_text()

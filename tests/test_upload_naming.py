# ABOUTME: Tests the upload inbox naming contract shared by the tusd post-finish hook
# ABOUTME: and the browser UI, so the prompt the UI prints names the file that lands.

import datetime
import importlib.machinery
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK_PATH = _ROOT / "upload_file" / "tusd" / "post-finish"
_UI_PATH = _ROOT / "upload_file" / "ui" / "index.html"

# The hook is an executable without a .py suffix; load it by path with an explicit loader.
_spec = importlib.util.spec_from_file_location(
    "post_finish", _HOOK_PATH,
    loader=importlib.machinery.SourceFileLoader("post_finish", str(_HOOK_PATH)))
post_finish = importlib.util.module_from_spec(_spec)
sys.modules["post_finish"] = post_finish
_spec.loader.exec_module(post_finish)

NOW = datetime.datetime(2026, 8, 18, 16, 30, 5)


def test_unprefixed_name_is_stamped():
    assert post_finish.final_name("selection.json", NOW) == "2026-08-18_163005_selection.json"


def test_client_stamped_name_is_preserved():
    """The browser UI shows this name in its prompt before the file lands — keep it."""
    assert post_finish.final_name("2026-08-18_161500_photo.jpg", NOW) == "2026-08-18_161500_photo.jpg"


@pytest.mark.parametrize("raw", ["", None, "..", "/", "2026-08-18_1615_x.jpg", "20260818_161500_x.jpg"])
def test_prefix_lookalikes_and_junk_are_stamped(raw):
    """Anything not carrying a real YYYY-MM-DD_HHMMSS_ prefix gets one, and stays a plain name."""
    out = post_finish.final_name(raw, NOW)
    assert out.startswith("2026-08-18_163005_")
    assert "/" not in out and out not in (".", "..")


def test_hostile_characters_are_sanitised():
    """Path components and shell metacharacters go; spaces and dashes stay legal."""
    assert post_finish.final_name("../../etc/pa$$wd; drop it", NOW) == "2026-08-18_163005_pa__wd_ drop it"


def _node():
    return shutil.which("nodejs") or shutil.which("node")


@pytest.mark.skipif(not _node(), reason="no node runtime to exercise the browser-side namer")
def test_ui_names_survive_the_hook_unchanged():
    """The UI's finalName() must produce names the hook preserves verbatim — else the
    prompt it printed points at a file that never exists."""
    src = _UI_PATH.read_text()
    fn = re.search(r"function finalName\(file\) \{.*?\n\}", src, re.S)
    assert fn, "finalName() not found in the upload UI"

    raws = ["photo.jpg", "Photo (1).JPEG", "rapport été.pdf", "../../etc/passwd",
            "pa$$wd; drop it", "", "  ", "no-extension", "a/b\\c.txt", "2026-01-01_000000_x.md"]
    script = fn.group(0) + "\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map(n => finalName({name: n}))));"
    out = subprocess.run([_node(), "-e", script, json.dumps(raws)],
                         capture_output=True, text=True, check=True).stdout
    for raw, name in zip(raws, json.loads(out)):
        assert post_finish.PREFIXED.match(name), f"{raw!r} -> {name!r} lacks the prefix the hook looks for"
        assert post_finish.final_name(name, NOW) == name, f"{raw!r} -> hook rewrote {name!r}"


def test_existing_route_folder_is_the_destination(tmp_path):
    (tmp_path / "seat").mkdir()
    assert post_finish.landing_dir("seat", str(tmp_path), "/inbox") == str(tmp_path / "seat")


@pytest.mark.parametrize("dest", [None, "", "unknown", "..", "../seat", "seat/..", "SEAT", ".", "a" * 33, "seat\n"])
def test_unknown_or_hostile_destination_falls_back_to_the_inbox(tmp_path, dest):
    """Only a plain name whose folder exists routes; everything else lands where it always did."""
    (tmp_path / "seat").mkdir()
    assert post_finish.landing_dir(dest, str(tmp_path), "/inbox") == "/inbox"


def test_symlinked_route_escaping_the_root_is_refused(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "routes" / "seat").symlink_to(tmp_path / "elsewhere")
    assert post_finish.landing_dir("seat", str(tmp_path / "routes"), "/inbox") == "/inbox"


def _run_hook(tmp_path, meta):
    inbox, routes, tus = tmp_path / "inbox", tmp_path / "to", tmp_path / "tus"
    for d in (inbox, routes / "seat", tus):
        d.mkdir(parents=True, exist_ok=True)
    src = tus / "abc123"
    src.write_text("payload")
    (tus / "abc123.info").write_text("{}")
    event = {"Event": {"Upload": {"Storage": {"Path": str(src)}, "MetaData": meta}}}
    env = {"PATH": "/usr/bin:/bin", "UPLOAD_INBOX": str(inbox), "UPLOAD_ROUTES": str(routes)}
    subprocess.run([sys.executable, str(_HOOK_PATH)], input=json.dumps(event),
                   text=True, env=env, check=True, capture_output=True)
    return inbox, routes, tus


def test_hook_moves_a_routed_upload_into_its_folder(tmp_path):
    inbox, routes, tus = _run_hook(tmp_path, {"filename": "2026-08-18_161500_x.pdf", "destination": "seat"})
    assert [p.name for p in (routes / "seat").iterdir()] == ["2026-08-18_161500_x.pdf"]
    assert list(inbox.iterdir()) == [] and list(tus.iterdir()) == []


def test_hook_without_destination_lands_in_the_inbox(tmp_path):
    inbox, routes, _ = _run_hook(tmp_path, {"filename": "2026-08-18_161500_x.pdf"})
    assert [p.name for p in inbox.iterdir()] == ["2026-08-18_161500_x.pdf"]
    assert list((routes / "seat").iterdir()) == []

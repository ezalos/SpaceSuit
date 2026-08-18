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

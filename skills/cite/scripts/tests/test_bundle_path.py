# ABOUTME: Tests slug derivation and central/legacy bundle-dir resolution.
from pathlib import Path
import bundle_path as bp

def test_slug_strips_md_and_replaces_slashes():
    assert bp.slug_for("slides/session-05/A-reg.md") == "slides-session-05-A-reg"

def test_slug_handles_absolute_paths():
    s = bp.slug_for("/home/x/My Deck.md")
    assert s == "home-x-My Deck" or s == "home-x-My-Deck"  # accept space or hyphen, no leading dash
    assert not s.startswith("-")

def test_central_dir_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CITE_STATE_HOME", str(tmp_path))
    d = bp.bundle_dir("deck.md")
    assert str(d).startswith(str(tmp_path))
    assert d.name == "deck"

def test_legacy_dir_preferred_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CITE_STATE_HOME", str(tmp_path / "state"))
    legacy = tmp_path / "docs" / "citation-audit" / "deck"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert bp.bundle_dir("deck.md") == legacy

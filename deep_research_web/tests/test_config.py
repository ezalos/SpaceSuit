# ABOUTME: Tests for config loading: defaults, overrides, blanks.
# ABOUTME: Never reads the real environment; every case passes its own mapping and home.
from pathlib import Path

from deep_research_web.config import DEFAULT_MODEL, Config, load_config


def test_defaults_when_env_empty(tmp_path):
    cfg = load_config(env={}, home=tmp_path)
    assert cfg.model == DEFAULT_MODEL == "claude-fable-5-1"
    assert cfg.project is None
    assert cfg.runs_root == tmp_path / "research-runs"
    assert cfg.profile == tmp_path / ".local/state/deep-research-web/chrome-profile"
    assert cfg.profiles == tmp_path / ".local/state/deep-research-web/profiles"


def test_env_overrides_every_field(tmp_path):
    env = {
        "DEEP_RESEARCH_WEB_MODEL": "claude-opus-5",
        "DEEP_RESEARCH_WEB_PROJECT": "Deep research",
        "DEEP_RESEARCH_WEB_RUNS_ROOT": "/r",
        "DEEP_RESEARCH_WEB_PROFILE": "/p",
        "DEEP_RESEARCH_WEB_PROFILES": "/ps",
    }
    assert load_config(env=env, home=tmp_path) == Config(
        "claude-opus-5", "Deep research", Path("/r"), Path("/p"), Path("/ps")
    )


def test_blank_values_fall_back_to_defaults(tmp_path):
    cfg = load_config(env={"DEEP_RESEARCH_WEB_MODEL": "  ", "DEEP_RESEARCH_WEB_PROJECT": ""}, home=tmp_path)
    assert cfg.model == DEFAULT_MODEL
    assert cfg.project is None


def test_tilde_in_paths_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config(env={"DEEP_RESEARCH_WEB_RUNS_ROOT": "~/runs"}, home=tmp_path)
    assert cfg.runs_root == tmp_path / "runs"


def test_archive_root_is_off_by_default(tmp_path):
    assert load_config(env={}, home=tmp_path).archive_root is None


def test_archive_root_from_env_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config(env={"DEEP_RESEARCH_WEB_ARCHIVE_ROOT": "~/42/Research/deep-research"}, home=tmp_path)
    assert cfg.archive_root == tmp_path / "42/Research/deep-research"

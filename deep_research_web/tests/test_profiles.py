# ABOUTME: Tests for the profile layout: saved profiles, the live symlink, the atomic swap, run ownership.
# ABOUTME: Real directories and symlinks under tmp_path; no browser.
import fcntl
import os
from types import SimpleNamespace

import pytest

from deep_research_web.profiles import (
    ProfileError, group_by_profile, live_name, run_profile, saved_profiles, switch_to,
)
from deep_research_web.session import LOCK_FILE, SessionError


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "profiles"
    for name in ("perso", "work5"):
        (root / name).mkdir(parents=True)
        (root / name).chmod(0o700)
    return tmp_path / "chrome-profile", root


def test_saved_profiles_lists_directories_only(state):
    link, root = state
    (root / "stray.txt").write_text("x")
    assert saved_profiles(root) == {"perso", "work5"}
    assert saved_profiles(root.parent / "absent") == set()


def test_switch_points_the_link_at_the_profile(state):
    link, root = state
    switch_to(link, root, "perso")
    assert live_name(link, root) == "perso"
    assert os.readlink(link) == "profiles/perso"  # relative, reads as chrome-profile -> profiles/perso
    switch_to(link, root, "work5")
    assert live_name(link, root) == "work5"


def test_live_name_is_none_for_a_real_directory_or_nothing(state):
    link, root = state
    assert live_name(link, root) is None
    link.mkdir()
    assert live_name(link, root) is None


def test_switch_refuses_a_missing_profile(state):
    link, root = state
    with pytest.raises(ProfileError, match="login ghost"):
        switch_to(link, root, "ghost")


def test_switch_refuses_to_replace_a_real_directory(state):
    link, root = state
    link.mkdir()
    with pytest.raises(ProfileError, match="not a symlink"):
        switch_to(link, root, "perso")


def test_switch_refuses_a_profile_open_to_group(state):
    link, root = state
    (root / "perso").chmod(0o750)
    with pytest.raises(SessionError, match="0700"):
        switch_to(link, root, "perso")


def test_switch_refuses_while_the_live_profile_is_locked(state):
    link, root = state
    switch_to(link, root, "perso")
    fd = os.open(root / "perso" / LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(SessionError, match="busy"):
            switch_to(link, root, "work5")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert live_name(link, root) == "perso"
    switch_to(link, root, "work5")
    assert live_name(link, root) == "work5"


def test_switch_releases_the_previous_live_profiles_lock(state):
    link, root = state
    switch_to(link, root, "perso")
    switch_to(link, root, "work5")
    fd = os.open(root / "perso" / LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if switch_to left perso's lock held
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_switch_removes_a_leftover_temp_link_for_this_process(state):
    link, root = state
    leftover = link.with_name(link.name + f".switching.{os.getpid()}")
    leftover.symlink_to("nowhere")
    switch_to(link, root, "perso")
    assert live_name(link, root) == "perso"


def test_run_profile_uses_the_account_or_falls_back_to_the_live_link(state):
    link, root = state
    assert run_profile(link, root, "work5") == root / "work5"
    assert run_profile(link, root, None) == link


def test_group_by_profile_keeps_each_account_together(state):
    link, root = state
    a, b, legacy = (SimpleNamespace(account=x) for x in ("perso", "perso", None))
    groups = group_by_profile(link, root, [a, legacy, b])
    assert groups == {root / "perso": [a, b], link: [legacy]}

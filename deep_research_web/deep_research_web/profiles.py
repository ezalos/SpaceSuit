# ABOUTME: The on-disk profile layout: one saved Chrome profile per account under profiles/, and a
# ABOUTME: symlink naming the live one. Swaps are atomic; a run's own profile is found from its account.
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .session import LOCK_FILE, SessionError, check_profile_permissions


class ProfileError(Exception):
    pass


def saved_profiles(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()}


def live_name(link: Path, root: Path) -> str | None:
    """The account the live symlink points at, or None when it is not a symlink into root."""
    if not link.is_symlink():
        return None
    target = (link.parent / os.readlink(link)).resolve()
    return target.name if target.parent == root.resolve() else None


def switch_to(link: Path, root: Path, name: str) -> None:
    """Point the live symlink at root/name: a temporary symlink renamed over the old one.

    Holds the CURRENT live profile's own lock (the same flock Session takes) across the
    whole swap, releasing it in a finally: a check-then-act busy check left a window where
    a browser could start on the old profile between the check and the rename. The tmp
    link name is per-process so two switches never collide on the same leftover name.
    """
    target = root / name
    if not target.is_dir():
        raise ProfileError(f"no saved profile {target}; run: deep-research-web login {name}")
    check_profile_permissions(target)
    if link.exists() and not link.is_symlink():
        raise ProfileError(f"{link} is a real directory, not a symlink; move it into {root} first")
    fd: int | None = None
    if link.exists():
        fd = os.open(link / LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise SessionError("browser profile busy: another deep-research-web command is running") from exc
    try:
        tmp = link.with_name(link.name + f".switching.{os.getpid()}")
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(os.path.relpath(target, link.parent))
        os.replace(tmp, link)
    finally:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def run_profile(link: Path, root: Path, account: str | None) -> Path:
    """The profile a run belongs to: its account's own, or the live link for a record without one."""
    return root / account if account else link


def group_by_profile(link: Path, root: Path, recs: list) -> dict[Path, list]:
    groups: dict[Path, list] = {}
    for rec in recs:
        groups.setdefault(run_profile(link, root, rec.account), []).append(rec)
    return groups

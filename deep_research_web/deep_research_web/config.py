# ABOUTME: Configuration from DEEP_RESEARCH_WEB_* environment variables with documented defaults.
# ABOUTME: Pure: takes a mapping and a home directory so tests never touch the real environment.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PREFIX = "DEEP_RESEARCH_WEB_"
DEFAULT_MODEL = "claude-fable-5-1"


@dataclass(frozen=True)
class Config:
    model: str
    project: str | None
    runs_root: Path
    profile: Path
    profiles: Path
    archive_root: Path | None = None  # unset: collect archives nothing
    # Research runs one account may hold at once. Measured 2026-09-25: two Research tasks ran
    # concurrently on one claude.ai account (work5); 3 is not measured, so 2 is the default.
    max_per_account: int = 2
    # When the live account is full, launch on another saved work account with room instead of
    # refusing. Off until Louis rules on it; never an account claude-usage marks priority >= 1.
    spread: bool = False


def load_config(env: Mapping[str, str] | None = None, home: Path | None = None) -> Config:
    env = os.environ if env is None else env
    home = Path.home() if home is None else home

    def get(name: str) -> str | None:
        value = (env.get(PREFIX + name) or "").strip()
        return value or None

    return Config(
        model=get("MODEL") or DEFAULT_MODEL,
        project=get("PROJECT"),
        runs_root=Path(get("RUNS_ROOT") or home / "research-runs").expanduser(),
        profile=Path(
            get("PROFILE") or home / ".local/state/deep-research-web/chrome-profile"
        ).expanduser(),
        profiles=Path(
            get("PROFILES") or home / ".local/state/deep-research-web/profiles"
        ).expanduser(),
        archive_root=Path(get("ARCHIVE_ROOT")).expanduser() if get("ARCHIVE_ROOT") else None,
        max_per_account=int(get("MAX_PER_ACCOUNT") or 2),
        spread=get("SPREAD") == "1",
    )

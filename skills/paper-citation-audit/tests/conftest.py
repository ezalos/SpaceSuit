# ABOUTME: Puts the skill's scripts on sys.path (PCA_SCRIPTS overrides, to replay the suite on an older copy) and
# ABOUTME: forbids real network access in every test: lookups go through canned responses only.
import os
import sys
import urllib.request
from pathlib import Path

import pytest

SCRIPTS = Path(os.environ.get("PCA_SCRIPTS") or Path(__file__).resolve().parent.parent / "scripts")
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test reached the real network")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

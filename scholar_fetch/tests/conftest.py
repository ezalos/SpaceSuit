# ABOUTME: Test setup: tests/ on sys.path for the pdfs helper, and a guard that fails any non-live test touching the internet.
# ABOUTME: The chain's direct step fetches whatever URL it is given, so a mistyped test URL would otherwise go out.
import pathlib
import sys

import pytest
import urllib3.util.connection

sys.path.insert(0, str(pathlib.Path(__file__).parent))


@pytest.fixture(autouse=True)
def loopback_only(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    real = urllib3.util.connection.create_connection

    def guard(address, *args, **kwargs):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError(f"a non-live test tried the internet: {address}")
        return real(address, *args, **kwargs)

    monkeypatch.setattr(urllib3.util.connection, "create_connection", guard)

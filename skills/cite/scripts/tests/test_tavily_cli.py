# ABOUTME: Tests Tavily key resolution and request-payload construction without hitting the network.
import json
import os
import stat

import tavily_cli as tv

def test_key_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    assert tv.resolve_key() == "tvly-env"

def test_key_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    f = tmp_path / "tavily_api_key"
    f.write_text("tvly-file\n")
    assert tv.resolve_key(key_file=f) == "tvly-file"

FAKE_PROTON_AGENT = """#!/usr/bin/env bash
# fake proton-agent: resolves refs to a sentinel; error branch keyed on ref text
set -euo pipefail
SENTINEL="SENTINEL_tavily_hunter2_SENTINEL"
case "${1:-}" in
  item)
    ref="${3:-}"
    case "$ref" in
      *missing*) echo "error: NotFound" >&2; exit 1 ;;
      *)         echo "$SENTINEL" ;;
    esac ;;
  *) exit 1 ;;
esac
"""

def test_key_from_file_resolves_pass_ref(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake = fake_bin / "proton-agent"
    fake.write_text(FAKE_PROTON_AGENT)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    f = tmp_path / "tavily_api_key"
    f.write_text("pass://share1/item1/Secret\n")
    assert tv.resolve_key(key_file=f) == "SENTINEL_tavily_hunter2_SENTINEL"

def test_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError):
        tv.resolve_key(key_file=tmp_path / "nope")

def test_search_payload():
    body = tv.build_search_payload("ai market 2026", max_results=3,
                                   include_domains=["gartner.com"], days=180)
    assert body["query"] == "ai market 2026"
    assert body["max_results"] == 3
    assert body["include_domains"] == ["gartner.com"]
    assert body["days"] == 180

def test_extract_payload():
    body = tv.build_extract_payload("https://x.com/a")
    assert body["urls"] == ["https://x.com/a"]
    assert body["extract_depth"] == "advanced"
    assert body["format"] == "markdown"

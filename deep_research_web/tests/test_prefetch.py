# ABOUTME: Tests for the claims pre-fetch: sources.md parsing, the fetched/ files, fetched.json.
# ABOUTME: The resolver is a stand-in returning real Fetched objects; the file writing is real.
import json

from scholar_fetch.chain import Fetched
from scholar_fetch.ids import Ids

from deep_research_web.prefetch import parse_sources, prefetch

SOURCES = """# Sources

1. [arxiv](https://arxiv.org/abs/2511.15605) via web_fetch, accessed 2026-09-17
2. [npm](https://www.npmjs.com/package/x) via web_search, accessed 2026-09-17
3. https://github.com/sylvestf/LIBERO-plus
"""


def test_parse_sources_handles_markdown_links_and_bare_urls():
    assert parse_sources(SOURCES) == [
        (1, "https://arxiv.org/abs/2511.15605"), (2, "https://www.npmjs.com/package/x"),
        (3, "https://github.com/sylvestf/LIBERO-plus")]


def test_prefetch_writes_text_for_hits_and_the_index_for_all(tmp_path):
    def fake(url):
        if "npmjs" in url:
            return Fetched(url, None, None, None, Ids(), [{"step": "direct", "target": url, "outcome": "http 403"}])
        return Fetched(url, f"text of {url}", "arxiv" if "arxiv" in url else "direct", "fulltext", Ids(), [])

    got = prefetch(tmp_path, SOURCES, fake)
    assert (tmp_path / "fetched" / "1.txt").read_text() == "text of https://arxiv.org/abs/2511.15605"
    assert not (tmp_path / "fetched" / "2.txt").exists()
    index = json.loads((tmp_path / "fetched.json").read_text())
    assert index["2"]["tried"][0]["outcome"] == "http 403"
    assert got[1]["served_by"] == "arxiv"


def test_prefetch_twice_replaces_stale_text(tmp_path):
    prefetch(tmp_path, SOURCES, lambda u: Fetched(u, "old", "direct", "fulltext", Ids(), []))
    prefetch(tmp_path, SOURCES, lambda u: Fetched(u, None, None, None, Ids(), []))
    assert not list((tmp_path / "fetched").glob("*.txt"))


def test_one_raising_source_is_recorded_and_never_aborts_the_run(tmp_path):
    def flaky(url):
        if "npmjs" in url:
            raise RuntimeError("parser blew up")
        return Fetched(url, f"text of {url}", "direct", "fulltext", Ids(), [])

    got = prefetch(tmp_path, SOURCES, flaky)
    assert got[2]["served_by"] is None and got[2]["kind"] is None
    assert got[2]["tried"] == [{"step": "prefetch", "target": "https://www.npmjs.com/package/x",
                                "outcome": "error: RuntimeError"}]
    assert got[1]["served_by"] == got[3]["served_by"] == "direct"
    assert (tmp_path / "fetched" / "3.txt").exists()
    assert json.loads((tmp_path / "fetched.json").read_text())["2"]["tried"][0]["step"] == "prefetch"


def test_fetched_json_is_written_atomically(tmp_path):
    prefetch(tmp_path, SOURCES, lambda u: Fetched(u, "t", "direct", "fulltext", Ids(), []))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fetched", "fetched.json"]  # no .tmp left behind


def test_a_url_listed_twice_is_resolved_once(tmp_path):
    calls = []

    def counting(url):
        calls.append(url)
        return Fetched(url, "t", "direct", "fulltext", Ids(), [])

    got = prefetch(tmp_path, SOURCES + "4. https://github.com/sylvestf/LIBERO-plus\n", counting)
    assert len(calls) == 3 and got[4] == got[3]
    assert (tmp_path / "fetched" / "4.txt").read_text() == "t"


def test_prefetch_reports_one_line_per_source(tmp_path):
    lines = []

    def fake(url):
        if "npmjs" in url:
            return Fetched(url, None, None, None, Ids(), [])
        return Fetched(url, "t", "arxiv", "fulltext", Ids(), [])

    prefetch(tmp_path, SOURCES, fake, log=lines.append)
    assert lines == ["  [1/3] arxiv https://arxiv.org/abs/2511.15605",
                     "  [2/3] not fetched https://www.npmjs.com/package/x",
                     "  [3/3] arxiv https://github.com/sylvestf/LIBERO-plus"]


def test_default_resolver_uses_prefetch_resolver_env_var(monkeypatch, tmp_path):
    """PREFETCH_RESOLVER env var overrides default_resolver: it imports the named factory and calls
    it with the Client, replacing bare chain.resolve with the caller's own resolver."""
    from deep_research_web.prefetch import default_resolver

    calls = []
    fake = Fetched("u", "plugged in", "custom", "fulltext", Ids(), [])

    def make_resolver(client):
        return lambda url: (calls.append((url, client)), fake)[1]

    monkeypatch.setenv("PREFETCH_RESOLVER", "tests.test_prefetch.make_resolver_fixture")
    import sys, types
    mod = types.ModuleType("tests.test_prefetch.make_resolver_fixture")
    monkeypatch.setitem(sys.modules, "tests.test_prefetch.make_resolver_fixture", mod)

    # Simpler: patch importlib.import_module so we don't need to create a real module
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *a, **k):
        if name == "tests_scihub_plugin_fixture":
            m = types.ModuleType(name)
            m.make_resolver = make_resolver
            return m
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setenv("PREFETCH_RESOLVER", "tests_scihub_plugin_fixture.make_resolver")

    sentinel_client = object()
    resolver = default_resolver.__wrapped__(sentinel_client) if hasattr(default_resolver, "__wrapped__") else None
    # Call default_resolver() the normal way; it reads PREFETCH_RESOLVER and calls our factory
    resolver = default_resolver()
    result = resolver("https://doi.org/10.1000/plugin-test")
    assert result is fake
    assert calls[0][0] == "https://doi.org/10.1000/plugin-test"

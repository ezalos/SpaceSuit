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

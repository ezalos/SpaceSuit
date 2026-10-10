# ABOUTME: fetch_texts with scholar_fetch stubbed: a re-run clears old texts and verdicts, so a failed refetch leaves
# ABOUTME: nothing stale, a key with '/' stays one file, and fetched.json records whether each text was a PDF.
import json
import sys
import types
from dataclasses import dataclass, field

import pytest


@dataclass
class Got:
    text: str | None
    served_by: str | None
    kind: str | None
    tried: list = field(default_factory=list)

    def to_dict(self):
        return {"served_by": self.served_by, "kind": self.kind, "tried": self.tried}


@pytest.fixture
def ft(monkeypatch, tmp_path):
    answers: dict[str, object] = {}
    chain = types.ModuleType("scholar_fetch.chain")
    http = types.ModuleType("scholar_fetch.http")

    def resolve(url, client):
        a = answers[url]
        if isinstance(a, Exception):
            raise a
        return a
    chain.resolve = resolve
    chain._text_cache = lambda client, url: tmp_path / "cache" / (url.replace("/", "_") + ".json")

    class Client:
        def __init__(self, cache_dir):
            self.cache_dir = cache_dir
    http.Client = Client
    monkeypatch.setitem(sys.modules, "scholar_fetch", types.ModuleType("scholar_fetch"))
    monkeypatch.setitem(sys.modules, "scholar_fetch.chain", chain)
    monkeypatch.setitem(sys.modules, "scholar_fetch.http", http)
    monkeypatch.delitem(sys.modules, "fetch_texts", raising=False)
    import fetch_texts
    return fetch_texts, answers


def run(mod, out, bib, monkeypatch):
    monkeypatch.setattr("sys.argv", ["fetch_texts.py", str(out), str(bib)])
    mod.main()
    return json.loads((out / "fetched.json").read_text())


def test_rerun_clears_stale_texts_and_verdicts(ft, tmp_path, monkeypatch):
    mod, answers = ft
    bib = tmp_path / "r.bib"
    bib.write_text("@misc{a/b, title={T}, eprint={2306.03310}}\n")
    out = tmp_path / "out"
    url = "https://arxiv.org/abs/2306.03310"
    answers[url] = Got("x" * 6000, "arxiv", "fulltext", [{"step": "arxiv", "target": "pdf-url", "outcome": "ok"}])
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "pdf-url.json").write_text(json.dumps({"pdf": True}))
    rec = run(mod, out, bib, monkeypatch)
    text = out / "texts" / "a%2Fb.txt"
    assert text.exists() and rec["a/b"]["pdf"] is True and rec["a/b"]["landing"] is False
    (out / "verdicts-1.jsonl").write_text("{}\n")
    answers[url] = RuntimeError("network down")
    rec = run(mod, out, bib, monkeypatch)
    assert not text.exists(), "a failed re-fetch must not leave the old text behind"
    assert not (out / "verdicts-1.jsonl").exists()
    assert rec["a/b"]["served_by"] is None


def test_html_fulltext_is_a_landing_page(ft, tmp_path, monkeypatch):
    mod, answers = ft
    bib = tmp_path / "r.bib"
    bib.write_text("@misc{w, title={T}, url={https://example.org/page}}\n")
    answers["https://example.org/page"] = Got("y" * 9000, "direct", "fulltext",
                                              [{"step": "direct", "target": "html-url", "outcome": "ok"}])
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "html-url.json").write_text(json.dumps({"pdf": False}))
    rec = run(mod, tmp_path / "out", bib, monkeypatch)
    assert rec["w"]["pdf"] is False and rec["w"]["landing"] is True


def test_a_missing_private_cache_symbol_is_a_clear_error(monkeypatch):
    chain = types.ModuleType("scholar_fetch.chain")
    chain.resolve = lambda url, client: None
    http = types.ModuleType("scholar_fetch.http")
    http.Client = object
    monkeypatch.setitem(sys.modules, "scholar_fetch", types.ModuleType("scholar_fetch"))
    monkeypatch.setitem(sys.modules, "scholar_fetch.chain", chain)
    monkeypatch.setitem(sys.modules, "scholar_fetch.http", http)
    monkeypatch.delitem(sys.modules, "fetch_texts", raising=False)
    with pytest.raises(SystemExit) as e:
        import fetch_texts  # noqa: F401
    assert "_text_cache" in str(e.value)

# ABOUTME: Tests for the failover chain against one local server playing arXiv, Unpaywall, S2, OpenAlex and publishers.
# ABOUTME: Order, skips, kind, the tried log, the S2 key staying on S2, and the text cache.
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from pdfs import make_pdf

from scholar_fetch.chain import resolve
from scholar_fetch.http import Client

PAPER = make_pdf("Attention is all you need. " * 20)
ROUTES: dict[str, tuple[int, str, bytes]] = {}
SEEN: list[tuple[str, str | None]] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append((self.path, self.headers.get("x-api-key")))
        status, ctype, body = ROUTES.get(urlsplit(self.path).path, (404, "text/plain", b""))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def j(obj):
    return (200, "application/json", json.dumps(obj).encode())


@pytest.fixture
def svc(tmp_path, monkeypatch):
    ROUTES.clear()
    SEEN.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    b = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.org")
    monkeypatch.setenv("S2_API_KEY", "sekrit")
    endpoints = {
        "arxiv": b + "/arxiv/pdf/{id}",
        "unpaywall": b + "/unpaywall/{doi}?email={email}",
        "s2": b + "/s2/{pid}?fields=externalIds,openAccessPdf",
        "openalex": b + "/openalex/doi:{doi}",
    }
    client = Client(tmp_path / "cache", min_interval={}, lock_path=tmp_path / "dl.lock")
    yield b, endpoints, client
    server.shutdown()


def steps(f):
    return [(t["step"], t["outcome"]) for t in f.tried]


def test_arxiv_url_is_served_by_arxiv(svc):
    b, ep, c = svc
    ROUTES["/arxiv/pdf/2511.15605"] = (200, "application/pdf", PAPER)
    f = resolve(b + "/arxiv.org/abs/2511.15605", c, ep)
    assert (f.served_by, f.kind) == ("arxiv", "fulltext")
    assert "Attention" in f.text
    assert steps(f) == [("arxiv", "ok")]


def test_paywalled_doi_falls_through_to_unpaywall_before_the_publisher(svc):
    b, ep, c = svc
    ROUTES["/unpaywall/10.1038/nature14539"] = j({"best_oa_location": {"url_for_pdf": b + "/oa.pdf", "url": b + "/oa"}})
    ROUTES["/oa.pdf"] = (200, "application/pdf", PAPER)
    f = resolve(b + "/doi.org/10.1038/nature14539", c, ep)
    assert (f.served_by, f.kind) == ("unpaywall", "fulltext")
    assert steps(f)[0] == ("arxiv", "skipped: no arxiv id")
    assert not any(p.startswith("/doi.org") for p, _ in SEEN)  # the publisher was never asked


def test_every_oa_step_fails_then_the_landing_page_is_labelled_landing(svc):
    b, ep, c = svc
    ROUTES["/unpaywall/10.1234/x"] = j({"best_oa_location": None})
    ROUTES["/s2/DOI:10.1234/x"] = j({"externalIds": {"DOI": "10.1234/x"}, "openAccessPdf": None})
    ROUTES["/openalex/doi:10.1234/x"] = j({"best_oa_location": None, "open_access": {"oa_url": None}})
    ROUTES["/doi/10.1234/x"] = (200, "text/html", b"<p>" + b"Abstract only. " * 30 + b"</p>")
    f = resolve(b + "/doi/10.1234/x", c, ep)
    assert (f.served_by, f.kind) == ("direct", "landing")
    assert [s for s, _ in steps(f)] == ["arxiv", "unpaywall", "semantic-scholar", "openalex", "direct"]
    assert steps(f)[1] == ("unpaywall", "no-oa-location")


def test_non_paper_url_goes_straight_to_direct_and_counts_as_fulltext(svc):
    b, ep, c = svc
    ROUTES["/readme"] = (200, "text/html", b"<p>" + b"The project README. " * 30 + b"</p>")
    f = resolve(b + "/readme", c, ep)
    assert (f.served_by, f.kind) == ("direct", "fulltext")
    assert all(o.startswith("skipped") for s, o in steps(f) if s != "direct")


def test_everything_failing_is_a_fetched_with_no_text_and_a_full_log(svc):
    b, ep, c = svc
    f = resolve(b + "/gone", c, ep)
    assert (f.text, f.served_by, f.kind) == (None, None, None)
    assert steps(f)[-1] == ("direct", "http 404")


def test_pubmed_maps_through_s2_and_reuses_its_open_access_pdf(svc):
    b, ep, c = svc
    ROUTES["/s2/PMID:31285318"] = j({"externalIds": {"DOI": "10.2222/y"}, "openAccessPdf": {"url": b + "/pmc.pdf"}})
    ROUTES["/unpaywall/10.2222/y"] = j({"best_oa_location": None})
    ROUTES["/pmc.pdf"] = (200, "application/pdf", PAPER)
    f = resolve(b + "/pubmed.ncbi.nlm.nih.gov/31285318/", c, ep)
    assert f.served_by == "semantic-scholar"
    assert f.ids.doi == "10.2222/y" and f.ids.pmid == "31285318"


def test_openalex_is_skipped_for_arxiv_datacite_dois(svc):
    b, ep, c = svc
    f = resolve(b + "/doi.org/10.48550/arXiv.2106.09685", c, ep)  # identifies as arXiv, no DOI kept
    assert ("openalex", "skipped: no doi") in steps(f)


def test_the_s2_key_goes_to_s2_only(svc):
    b, ep, c = svc
    ROUTES["/s2/ARXIV:2511.15605"] = j({"externalIds": {}, "openAccessPdf": None})
    resolve(b + "/arxiv.org/abs/2511.15605", c, ep)
    assert SEEN and all((key == "sekrit") == p.startswith("/s2/") for p, key in SEEN)


def test_unpaywall_without_a_contact_address_is_skipped(svc, monkeypatch):
    b, ep, c = svc
    monkeypatch.delenv("CONTACT_EMAIL")
    f = resolve(b + "/doi.org/10.1038/nature14539", c, ep)
    assert ("unpaywall", "skipped: no-contact-email") in steps(f)


def test_a_too_large_oa_pdf_falls_through(svc):
    b, ep, c = svc
    ROUTES["/arxiv/pdf/2511.15605"] = (200, "application/pdf", PAPER)
    f = resolve(b + "/arxiv.org/abs/2511.15605", c, ep, max_bytes=100)
    assert steps(f)[0] == ("arxiv", "too-large")


def test_second_resolve_of_the_same_url_comes_from_the_text_cache(svc):
    b, ep, c = svc
    ROUTES["/arxiv/pdf/2511.15605"] = (200, "application/pdf", PAPER)
    resolve(b + "/arxiv.org/abs/2511.15605", c, ep)
    n = len(SEEN)
    again = resolve(b + "/arxiv.org/abs/2511.15605", c, ep)
    assert again.served_by == "arxiv" and len(SEEN) == n


def test_a_truthy_non_dict_nested_value_falls_through_instead_of_crashing(svc):
    b, ep, c = svc
    # A bare top-level [] is falsy and already degrades gracefully; the real hazard is a truthy
    # non-dict one level *inside* an answer (best_oa_location as a string, openAccessPdf as a list),
    # which the old (x or {}).get(...) chains never guarded against.
    ROUTES["/unpaywall/10.1038/nature14539"] = j({"best_oa_location": "not-a-dict"})
    ROUTES["/s2/DOI:10.1038/nature14539"] = j({"openAccessPdf": ["x"]})
    f = resolve(b + "/doi.org/10.1038/nature14539", c, ep)
    assert ("unpaywall", "no-oa-location") in steps(f)
    assert ("semantic-scholar", "no-oa-location") in steps(f)
    assert [s for s, _ in steps(f)] == ["arxiv", "unpaywall", "semantic-scholar", "openalex", "direct"]


def html(words: str) -> tuple[int, str, bytes]:
    return (200, "text/html", b"<p>" + words.encode() * 30 + b"</p>")


def test_an_oa_landing_page_does_not_stop_the_chain_a_later_pdf_wins(svc):
    b, ep, c = svc
    ROUTES["/unpaywall/10.1234/x"] = j({"best_oa_location": {"url": b + "/repo/landing"}})
    ROUTES["/repo/landing"] = html("Repository record, abstract only. ")
    ROUTES["/s2/DOI:10.1234/x"] = j({"openAccessPdf": {"url": b + "/s2.pdf"}})
    ROUTES["/s2.pdf"] = (200, "application/pdf", PAPER)
    f = resolve(b + "/doi/10.1234/x", c, ep)
    assert (f.served_by, f.kind) == ("semantic-scholar", "fulltext")
    assert "Attention" in f.text
    assert ("unpaywall", "landing") in steps(f)


def test_when_every_oa_step_serves_html_the_first_landing_is_returned(svc):
    b, ep, c = svc
    ROUTES["/unpaywall/10.1234/x"] = j({"best_oa_location": {"url": b + "/u"}})
    ROUTES["/u"] = html("Unpaywall landing. ")
    ROUTES["/s2/DOI:10.1234/x"] = j({"openAccessPdf": {"url": b + "/s"}})
    ROUTES["/s"] = html("S2 landing. ")
    ROUTES["/openalex/doi:10.1234/x"] = j({"open_access": {"oa_url": b + "/o"}})
    ROUTES["/o"] = html("OpenAlex landing. ")
    ROUTES["/doi/10.1234/x"] = html("Publisher landing. ")
    f = resolve(b + "/doi/10.1234/x", c, ep)
    assert (f.served_by, f.kind) == ("unpaywall", "landing")
    assert "Unpaywall landing" in f.text
    assert [s for s, _ in steps(f)] == ["arxiv", "unpaywall", "semantic-scholar", "openalex", "direct"]


def test_a_direct_pdf_is_fulltext_even_for_a_paper_url(svc):
    b, ep, c = svc
    ROUTES["/doi/10.1234/x"] = (200, "application/pdf", PAPER)
    f = resolve(b + "/doi/10.1234/x", c, ep)
    assert (f.served_by, f.kind) == ("direct", "fulltext")


def test_a_text_cache_entry_without_the_pdf_flag_is_a_miss(svc):
    b, ep, c = svc
    ROUTES["/arxiv/pdf/2511.15605"] = (200, "application/pdf", PAPER)
    resolve(b + "/arxiv.org/abs/2511.15605", c, ep)
    [entry] = (c.cache_dir / "text").glob("*.json")
    old = json.loads(entry.read_text())
    assert old.pop("pdf") is True
    entry.write_text(json.dumps(old))  # an entry written before the content kind was recorded
    n = len(SEEN)
    again = resolve(b + "/arxiv.org/abs/2511.15605", c, ep)
    assert (again.served_by, again.kind) == ("arxiv", "fulltext") and len(SEEN) == n + 1
    assert json.loads(entry.read_text())["pdf"] is True

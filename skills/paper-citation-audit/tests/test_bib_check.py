# ABOUTME: bib_check against canned arXiv / Crossref / OpenAlex / page responses: a wrong id, author, venue, year or a
# ABOUTME: near-identical title must never come out VERIFIED, and the BibTeX tokenizer must not misread or crash.
import json
import urllib.error

import bib_check as bc
import pytest

LIBERO = "LIBERO: Benchmarking Knowledge Transfer for Lifelong Robot Learning"
LIBERO_AUTHORS = ["Bo Liu", "Yifeng Zhu", "Chongkai Gao", "Yihao Feng", "Qiang Liu", "Yuke Zhu", "Peter Stone"]
LIBERO_BIB_AUTHORS = " and ".join(["Liu, Bo", "Zhu, Yifeng", "Gao, Chongkai", "Feng, Yihao", "Liu, Qiang",
                                   "Zhu, Yuke", "Stone, Peter"])


def feed(id_, title=LIBERO, authors=LIBERO_AUTHORS, published="2023-06-05", updated=None, journal_ref=None):
    ref = f"<arxiv:journal_ref>{journal_ref}</arxiv:journal_ref>" if journal_ref else ""
    names = "".join(f"<author><name>{a}</name></author>" for a in authors)
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
            f"<id>http://arxiv.org/abs/{id_}v2</id><title>{title}</title><published>{published}T00:00:00Z</published>"
            f"<updated>{updated or published}T00:00:00Z</updated>{names}{ref}</entry></feed>").encode()


EMPTY_FEED = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>query</title></feed>'


def crossref_json(doi, title=LIBERO, year=2023, venue="Advances in Neural Information Processing Systems",
                  authors=LIBERO_AUTHORS):
    return json.dumps({"message": {"DOI": doi, "title": [title], "issued": {"date-parts": [[year]]},
                                   "container-title": [venue],
                                   "author": [{"given": a.split()[0], "family": a.split()[-1]} for a in authors]}
                      }).encode()


def not_found(url):
    return urllib.error.HTTPError(url, 404, "Not Found", {}, None)


@pytest.fixture
def http(monkeypatch):
    """routes: {url substring: bytes or exception}; every call is recorded, an unrouted URL fails the test."""
    calls, routes = [], {}

    def fake_get(url):
        calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unrouted URL {url}")
    monkeypatch.setattr(bc, "get", fake_get)
    monkeypatch.setattr(bc, "ARXIV_SPACING_S", 0.0)
    return routes, calls


def entry(fields, typ="inproceedings", key="k"):
    return {"type": typ, "key": key, "fields": fields}


def base_fields(**extra):
    return {"title": LIBERO, "author": LIBERO_BIB_AUTHORS, "year": "2023", **extra}


# ---- B1: every identifier resolves, no fall-through

def test_fake_doi_with_real_title_is_not_verified(http):
    routes, calls = http
    routes["api.crossref.org"] = not_found("x")
    routes["api.datacite.org"] = not_found("x")
    routes["api.openalex.org"] = json.dumps({"results": [{"id": "W1", "title": LIBERO, "publication_year": 2023,
                                                          "authorships": []}]}).encode()
    r = bc.check(entry(base_fields(doi="10.9999/fake.2023.1")))
    assert r["status"].startswith("ID_NOT_FOUND: doi")
    assert not any("openalex" in c for c in calls), "a failed id must not fall through to a title search"


def test_wellformed_nonexistent_arxiv_id_empty_feed_is_not_found(http):
    routes, _ = http
    routes["export.arxiv.org"] = EMPTY_FEED
    r = bc.check(entry(base_fields(eprint="2106.99999", archiveprefix="arXiv"), typ="misc"))
    assert r["status"] == "ID_NOT_FOUND: eprint"


def test_arxiv_error_entry_for_malformed_id_is_not_found(http):
    routes, _ = http
    routes["export.arxiv.org"] = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors'
                                  b'#incorrect_id_format</id><title>Error</title></entry></feed>')
    assert bc.arxiv("2106.0968") is None


def test_feed_for_another_paper_is_not_accepted(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    assert bc.arxiv("2306.99999") is None


def test_eprint_and_doi_both_checked(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    routes["api.crossref.org"] = crossref_json("10.5555/other", title="A Completely Different Paper About Cooking")
    r = bc.check(entry(base_fields(eprint="2306.03310", doi="10.5555/other",
                                   booktitle="Advances in Neural Information Processing Systems")))
    assert "MISMATCH: title" in r["status"]


def test_lookup_error_is_not_verified_and_does_not_fall_through(http):
    routes, calls = http
    routes["export.arxiv.org"] = urllib.error.HTTPError("x", 429, "Too Many Requests", {}, None)
    r = bc.check(entry(base_fields(eprint="2306.03310"), typ="misc"))
    assert r["status"] == "LOOKUP_ERROR: eprint" and r["errors"]
    assert not any("openalex" in c for c in calls)


def test_title_search_status_does_not_start_with_verified(http):
    routes, _ = http
    routes["api.openalex.org"] = json.dumps({"results": [{"id": "W1", "title": LIBERO, "publication_year": 2023,
                                                          "authorships": [{"author": {"display_name": a}}
                                                                          for a in LIBERO_AUTHORS]}]}).encode()
    r = bc.check(entry(base_fields(), typ="misc"))
    assert r["status"] == "WEAK_TITLE_SEARCH"


def test_good_entry_with_both_ids_is_verified(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", journal_ref="NeurIPS 2023 Datasets and Benchmarks")
    routes["api.crossref.org"] = crossref_json("10.5555/libero")
    r = bc.check(entry(base_fields(eprint="2306.03310", doi="10.5555/libero",
                                   booktitle="Advances in Neural Information Processing Systems")))
    assert r["status"] == "VERIFIED", r


# ---- B2: whole author list, venue, year

def test_invented_coauthor_is_a_mismatch(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    authors = LIBERO_BIB_AUTHORS.replace("Gao, Chongkai", "Smith, John")
    r = bc.check(entry(base_fields(eprint="2306.03310", author=authors), typ="misc"))
    assert "MISMATCH: authors" in r["status"]


def test_and_others_truncation_is_accepted(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    r = bc.check(entry(base_fields(eprint="2306.03310", author="Liu, Bo and Zhu, Yifeng and others"), typ="misc"))
    assert r["status"] == "VERIFIED"


def test_missing_authors_without_others_is_a_mismatch(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    r = bc.check(entry(base_fields(eprint="2306.03310", author="Liu, Bo and Zhu, Yifeng"), typ="misc"))
    assert "MISMATCH: authors" in r["status"]


def test_wrong_venue_is_a_mismatch(http):
    routes, _ = http
    routes["api.crossref.org"] = crossref_json("10.5555/libero")
    r = bc.check(entry(base_fields(doi="10.5555/libero", booktitle="International Conference on Machine Learning")))
    assert "MISMATCH: venue" in r["status"]


@pytest.mark.parametrize("bib,rec", [("CoRL", "Conference on Robot Learning"),
                                     ("Proc. of the 7th Conference on Robot Learning", "CoRL 2023"),
                                     ("NeurIPS", "Advances in Neural Information Processing Systems"),
                                     ("ICRA", "2024 IEEE International Conference on Robotics and Automation (ICRA)")])
def test_venue_abbreviations_match(bib, rec):
    assert bc.venue_match(bib, rec)


@pytest.mark.parametrize("bib,rec", [("ICML", "International Conference on Learning Representations"),
                                     ("Robotics: Science and Systems", "Conference on Robot Learning")])
def test_different_venues_do_not_match(bib, rec):
    assert not bc.venue_match(bib, rec)


def test_unconfirmed_venue_is_not_verified(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    routes["api.openalex.org"] = not_found("x")
    routes["dblp.org"] = json.dumps({"result": {"hits": {"@total": "0"}}}).encode()
    r = bc.check(entry(base_fields(eprint="2306.03310", booktitle="NeurIPS")))
    assert r["status"] == "VENUE_UNCHECKED"


def test_year_off_by_one_is_a_mismatch(http):
    routes, _ = http
    routes["api.crossref.org"] = crossref_json("10.5555/libero")
    r = bc.check(entry(base_fields(doi="10.5555/libero", year="2024",
                                   booktitle="Advances in Neural Information Processing Systems")))
    assert "MISMATCH: year" in r["status"]


def test_arxiv_year_is_v1_or_journal_ref(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", published="2023-06-05", updated="2024-01-10")
    assert "MISMATCH: year" in bc.check(entry(base_fields(eprint="2306.03310", year="2024"), typ="misc"))["status"]
    routes["export.arxiv.org"] = feed("2306.03310", published="2023-06-05", journal_ref="CoRL 2024")
    assert bc.check(entry(base_fields(eprint="2306.03310", year="2024"), typ="misc"))["status"] == "VERIFIED"


# ---- M1: near-identical titles of different papers

@pytest.mark.parametrize("bib_title,rec_title", [
    ("LIBERO-Pro: Robustness Analysis of Vision-Language-Action Models under Perturbations",
     "LIBERO-Plus: Robustness Analysis of Vision-Language-Action Models under Perturbations"),
    ("OpenVLA: An Open-Source Vision-Language-Action Model",
     "OpenVLA-OFT: An Open-Source Vision-Language-Action Model")])
def test_near_identical_title_is_a_variant_not_verified(http, bib_title, rec_title):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2510.13626", title=rec_title)
    r = bc.check(entry(base_fields(eprint="arXiv:2510.13626", title=bib_title), typ="misc"))
    assert r["status"] == "TITLE_VARIANT"
    assert bib_title in r["findings"][0]["detail"] and rec_title in r["findings"][0]["detail"]


# ---- M7: the cited page's metadata, never its URLs

def test_url_slug_does_not_verify_a_title(http):
    routes, _ = http
    url = "https://example.org/papers/libero-benchmarking-knowledge-transfer-for-lifelong-robot-learning"
    routes["example.org"] = (f'<html><head><title>Some other paper</title></head><body><a href="{url}">x</a>'
                             f"</body></html>").encode()
    r = bc.check(entry(base_fields(url=url), typ="misc"))
    assert "MISMATCH: title" in r["status"] and r["status"] != "PAGE_MATCH"


def test_page_meta_tags_are_fully_checked(http):
    routes, _ = http
    metas = "".join(f'<meta name="citation_author" content="{a}">' for a in LIBERO_AUTHORS)
    routes["example.org"] = (f'<html><head><meta name="citation_title" content="{LIBERO}">{metas}'
                             f'<meta name="citation_publication_date" content="2023/12/10"></head></html>').encode()
    assert bc.check(entry(base_fields(url="https://example.org/p"), typ="misc"))["status"] == "PAGE_MATCH"
    r = bc.check(entry(base_fields(url="https://example.org/p", year="2021"), typ="misc"))
    assert "MISMATCH: year" in r["status"]


# ---- minors: ids, names, mailto

@pytest.mark.parametrize("raw,want", [("arXiv:2510.13626", "2510.13626"), ("2510.13626v2", "2510.13626v2"),
                                      ("cs/0601001", "cs/0601001"), ("math.GT/0309136", "math.GT/0309136"),
                                      ("not an id", None)])
def test_arxiv_id_forms(raw, want):
    assert bc.parse_arxiv_id(raw) == want


def test_malformed_eprint_is_id_not_found(http):
    r = bc.check(entry(base_fields(eprint="2106.0968x"), typ="misc"))
    assert r["status"] == "ID_NOT_FOUND: eprint"


def test_arxiv_doi_goes_to_arxiv_not_crossref(http):
    routes, calls = http
    routes["export.arxiv.org"] = feed("2306.03310")
    r = bc.check(entry(base_fields(doi="10.48550/arXiv.2306.03310"), typ="misc"))
    assert r["status"] == "VERIFIED" and not any("crossref" in c for c in calls)


@pytest.mark.parametrize("a,b", [('M{\\"u}ller', "Müller"), ("{\\'E}mile", "Émile"), ("\\c{C}elik", "Çelik"),
                                 ("{\\o}stergaard", "østergaard")])
def test_norm_latex_accents(a, b):
    assert bc.norm(a) == bc.norm(b) and " " not in bc.norm(a)


@pytest.mark.parametrize("a,b", [("van der Berg, Jan", "Jan van der Berg"), ('M{\\"u}ller, Hans', "Hans Müller"),
                                 ("de la Cruz, Ana", "Ana De La Cruz"), ("King, Jr., Martin", "Martin King Jr.")])
def test_name_variants_are_the_same_author(a, b):
    assert bc.same_author(a, b)


def test_openalex_carries_mailto(http, monkeypatch):
    routes, calls = http
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.org")
    routes["api.openalex.org"] = json.dumps({"results": []}).encode()
    bc.openalex_title(LIBERO)
    assert "mailto=someone%40example.org" in calls[0]


# ---- M8: the BibTeX tokenizer

def parse(text):
    entries, errors = bc.parse_bib_report(text)
    assert not errors
    return entries


def test_misc_with_no_fields_and_paren_entries():
    es = parse("@misc{nofields}\n@article(paren, title = {T}, year = 2020)\n")
    assert [(e["key"], e["fields"]) for e in es] == [("nofields", {}), ("paren", {"title": "T", "year": "2020"})]


def test_field_scan_does_not_enter_a_consumed_value():
    es = parse('@misc{k, url = {https://x.org/?title=Injected&doi=10.1/evil}, title = {Real}}')
    assert es[0]["fields"] == {"url": "https://x.org/?title=Injected&doi=10.1/evil", "title": "Real"}


def test_quoted_value_with_umlaut_and_inner_quote():
    es = parse('@article{k, author = "M{\\"u}ller, Hans and M\\"uller, Jo", title = "A {"quoted"} word"}')
    assert es[0]["fields"]["author"] == 'M{\\"u}ller, Hans and M\\"uller, Jo'
    assert es[0]["fields"]["title"] == 'A {"quoted"} word'


def test_string_macros_and_concatenation():
    es = parse('@string{corl = "Conference on Robot Learning"}\n'
                      '@inproceedings{k, booktitle = "Proc. " # corl, month = jun, title = {T}}')
    assert es[0]["fields"]["booktitle"] == "Proc. Conference on Robot Learning"
    assert es[0]["fields"]["month"] == "june"


@pytest.mark.parametrize("bad", ["@article{k, title = {never closed",  "@article{, title = {T}}",
                                 '@article{k, title = "never closed}', "@article{k, = {T}}",
                                 "@article{k, title = {T}} @article{k, title = {U}}", "@article{k title = {T}}",
                                 "@article{k, title = }"])
def test_malformed_inputs_do_not_crash_and_refuse(bad, tmp_path):
    _, errors = bc.parse_bib_report(bad)
    assert errors
    p = tmp_path / "bad.bib"
    p.write_text(bad)
    with pytest.raises(SystemExit):
        bc.load_bib(p)


def test_comment_hiding_an_entry_is_refused_by_the_raw_count(tmp_path):
    p = tmp_path / "c.bib"
    p.write_text("@comment{ @article{hidden, title={H}} }\n@article{shown, title = {S}}\n")
    with pytest.raises(SystemExit):
        bc.load_bib(p)


def test_good_file_loads(tmp_path):
    p = tmp_path / "g.bib"
    p.write_text("@string{x = {y}}\n@comment{nothing}\n@article{a, title={A}}\n@misc{b, title=\"B\"}\n")
    assert [e["key"] for e in bc.load_bib(p)] == ["a", "b"]


def test_cli_argument_order_out_dir_first(http, tmp_path, monkeypatch):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    bib = tmp_path / "r.bib"
    bib.write_text(f"@misc{{k, title={{{LIBERO}}}, author={{{LIBERO_BIB_AUTHORS}}}, year=2023, eprint={{2306.03310}}}}")
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["bib_check.py", str(out), str(bib)])
    bc.main()
    assert json.loads((out / "bib-check.json").read_text())[0]["status"] == "VERIFIED"


# ---- venue claims in note, addendum, type; ids inside arXiv-form venues; repository locations

ATT_FIELDS = dict(title=LIBERO, author=LIBERO_BIB_AUTHORS, year="2023", eprint="2306.03310", archiveprefix="arXiv")


@pytest.mark.parametrize("field,value", [("note", "ICML 2023"), ("note", "Spotlight at ICML 2017"),
                                         ("note", "Oral presentation, ICML 2017"), ("note", "ICLR 2024"),
                                         ("addendum", "Published at ICML 2017"), ("type", "ICML workshop paper")])
def test_a_venue_in_note_addendum_or_type_is_checked(http, field, value):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", journal_ref="NeurIPS 2023")
    assert "VENUE_UNCHECKED" in bc.check(entry({**ATT_FIELDS, field: value}, typ="misc"))["status"]


@pytest.mark.parametrize("value", ["NeurIPS 2023", "Spotlight at NeurIPS 2023", "https://example.org/page",
                                   "arXiv:2306.03310", "Code at github"])
def test_a_matching_or_venue_free_note_passes(http, value):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", journal_ref="NeurIPS 2023")
    assert bc.check(entry({**ATT_FIELDS, "note": value}, typ="misc"))["status"] == "VERIFIED"


@pytest.mark.parametrize("journal", ["arXiv preprint 2399.99999", "CoRR abs/2399.99999"])
def test_an_id_inside_an_arxiv_form_venue_is_looked_up(http, journal):
    routes, calls = http
    routes["export.arxiv.org"] = EMPTY_FEED
    r = bc.check(entry(base_fields(journal=journal), typ="article"))
    assert "ID_NOT_FOUND: journal" in r["status"] and any("2399.99999" in c for c in calls)


def test_a_mendeley_arxivid_field_is_looked_up(http):
    routes, _ = http
    routes["export.arxiv.org"] = EMPTY_FEED
    assert "ID_NOT_FOUND: arxivid" in bc.check(entry(base_fields(arxivid="2399.99999"), typ="misc"))["status"]


def test_repository_locations_do_not_confirm_a_venue(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    routes["api.openalex.org/works/doi"] = json.dumps({"locations": [
        {"source": {"display_name": "NeurIPS", "type": "repository"}}]}).encode()
    routes["api.openalex.org/works?search"] = json.dumps({"results": []}).encode()
    routes["dblp.org"] = json.dumps({"result": {"hits": {}}}).encode()
    r = bc.check(entry({**ATT_FIELDS, "booktitle": "NeurIPS"}, typ="inproceedings"))
    assert r["status"] == "VENUE_UNCHECKED"


# ---- years inside venues, a DOI record's own container, dates, notes naming a journal, errata, duplicates

@pytest.mark.parametrize("fields", [{"booktitle": "NeurIPS 2018"}, {"note": "NeurIPS 2019"},
                                    {"booktitle": "Advances in Neural Information Processing Systems 30 "
                                                  "(NeurIPS 2019)"}])
def test_a_wrong_year_inside_a_venue_is_a_mismatch(http, fields):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", journal_ref="NeurIPS 2023")
    r = bc.check(entry({**ATT_FIELDS, **fields}, typ="inproceedings"))
    assert "MISMATCH: year (venue)" in r["status"]


def test_a_doi_records_own_container_overrides_title_search_hits(http, monkeypatch):
    routes, _ = http
    routes["api.crossref.org"] = crossref_json("10.1007/978-3-030-1", venue="Lecture Notes in Computer Science")
    tpami = "IEEE Transactions on Pattern Analysis and Machine Intelligence"
    monkeypatch.setattr(bc, "VENUE_SOURCES", (("DBLP", lambda ids, t: [tpami]),))
    r = bc.check(entry(base_fields(doi="10.1007/978-3-030-1", journal="IEEE Transactions on Pattern Analysis and "
                                                                     "Machine Intelligence"), typ="article"))
    assert "MISMATCH: venue" in r["status"] and "another version" in str(r["findings"])


def test_howpublished_text_beside_a_link_is_checked(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", journal_ref="NeurIPS 2023")
    r = bc.check(entry({**ATT_FIELDS, "howpublished": "ICML, \\url{https://example.org/x}"}, typ="misc"))
    assert "VENUE_UNCHECKED" in r["status"]


def test_a_note_that_is_only_a_journal_name_is_checked(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310", journal_ref="NeurIPS 2023")
    assert "VENUE_UNCHECKED" in bc.check(entry({**ATT_FIELDS, "note": "Nature Machine Intelligence"},
                                               typ="misc"))["status"]


def test_date_and_year_must_agree(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    assert "MISMATCH: year (date)" in bc.check(entry({**ATT_FIELDS, "date": "2019-05-01"}, typ="misc"))["status"]


def test_the_not_checked_note_lists_every_unchecked_field(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    notes = " ".join(bc.check(entry(ATT_FIELDS, typ="misc"))["notes"])
    assert all(w in notes for w in ("volume", "pages", "publisher", "address", "month", "editor", "edition"))


def test_an_erratum_record_is_a_record_type_mismatch(http):
    routes, _ = http
    body = json.loads(crossref_json("10.1038/x1"))
    body["message"]["update-to"] = [{"DOI": "10.1038/orig", "type": "erratum"}]
    routes["api.crossref.org"] = json.dumps(body).encode()
    r = bc.check(entry(base_fields(doi="10.1038/x1", booktitle="Advances in Neural Information Processing Systems")))
    assert "MISMATCH: record type (erratum)" in r["status"]


def test_two_entries_with_one_title_are_noted(http, tmp_path, monkeypatch):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2306.03310")
    bib = tmp_path / "r.bib"
    one = f"title={{{LIBERO}}}, author={{{LIBERO_BIB_AUTHORS}}}, year=2023, eprint={{2306.03310}}"
    bib.write_text(f"@misc{{a, {one}}}\n@misc{{b, {one}}}\n")
    monkeypatch.setattr("sys.argv", ["bib_check.py", str(tmp_path / "o"), str(bib)])
    bc.main()
    rows = json.loads((tmp_path / "o" / "bib-check.json").read_text())
    assert all(any("DUPLICATE_ENTRY" in n for n in r["notes"]) for r in rows)

# ABOUTME: bib_check: arXiv id boundaries, ids in any field, venue lookups through OpenAlex and DBLP,
# ABOUTME: the whole author list, corporate authors, Greek titles, DataCite, multi-bib and --only.
import json
import urllib.error

import bib_check as bc
import pytest

T = "Deep Widgets for Robots"


A = ["Ann Smith", "Bob Jones"]


BIB_A = "Smith, Ann and Jones, Bob"


def cr(title=T, authors=A, venue=None, year=2023):
    return json.dumps({"message": {"title": [title], "issued": {"date-parts": [[year]]},
                                   "container-title": [venue] if venue else [],
                                   "author": [{"given": a.split()[0], "family": a.split()[-1]} for a in authors]}
                       }).encode()


def feed(id_, title=T, authors=A, pub="2023", upd=None, ref=None):
    r = f"<arxiv:journal_ref>{ref}</arxiv:journal_ref>" if ref else ""
    n = "".join(f"<author><name>{a}</name></author>" for a in authors)
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
            f"<id>http://arxiv.org/abs/{id_}v1</id><title>{title}</title><published>{pub}-01-01T00:00:00Z</published>"
            f"<updated>{upd or pub}-01-01T00:00:00Z</updated>{n}{r}</entry></feed>").encode()


EMPTY = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def nf(url="x"):
    return urllib.error.HTTPError(url, 404, "Not Found", {}, None)


@pytest.fixture
def http(monkeypatch):
    routes, calls = {}, []

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


def status(fields, typ="article"):
    return bc.check({"type": typ, "key": "k", "fields": fields})


def f(**kw):
    return {"title": T, "author": BIB_A, "year": "2023", **kw}


def no_venue_sources(routes):
    routes["api.openalex.org"] = nf()
    routes["dblp.org"] = json.dumps({"result": {"hits": {"@total": "0"}}}).encode()


@pytest.mark.parametrize("fields", [{"url": "https://arxiv.org/abs/2106.012345"},
                                    {"journal": "arXiv preprint arXiv:2106.012345"},
                                    {"note": "arXiv:2106.012345"}, {"eprint": "2106.012345"}])
def test_six_digit_id_is_never_the_five_digit_paper(http, fields):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2106.01234")
    assert not any(v == "2106.01234" for _, _, v, _ in bc.identifiers(fields))
    r = status(f(**fields), "misc")
    assert r["status"] != "VERIFIED" and "ID_NOT_FOUND" in r["status"]


@pytest.mark.parametrize("raw,want", [("https://arxiv.org/pdf/2106.01234.pdf", "2106.01234"),
                                      ("https://arxiv.org/abs/2106.01234v12", "2106.01234v12"),
                                      ("arXiv preprint arXiv:2106.01234.", "2106.01234")])
def test_valid_id_forms_still_parse(raw, want):
    assert [v for _, k, v, _ in bc.identifiers({"note": raw}) if k == "arxiv"] == [want]


def test_doi_in_url_drops_trailing_brace():
    assert [v for _, k, v, _ in bc.identifiers({"url": "https://doi.org/10.1145/123.456}"})] == ["10.1145/123.456"]


@pytest.mark.parametrize("bib,rec", [("Nature", "Nature Communications"), ("Science", "Science Robotics"),
                                     ("Science", "Robotics: Science and Systems"),
                                     ("IEEE Transactions on Robotics", "Robotics")])
def test_subset_venue_is_a_mismatch(http, bib, rec):
    routes, _ = http
    routes["crossref"] = cr(venue=rec)
    assert "MISMATCH: venue" in status(f(journal=bib, doi="10.1000/x1"))["status"]


@pytest.mark.parametrize("bib,rec", [("ICRA", "2024 IEEE International Conference on Robotics and Automation (ICRA)"),
                                     ("Proceedings of the 40th International Conference on Machine Learning", "ICML"),
                                     ("CVPR", "IEEE/CVF Conference on Computer Vision and Pattern Recognition")])
def test_acronyms_and_equal_sets_still_match(bib, rec):
    assert bc.venue_match(bib, rec)


def test_unconfirmed_venue_is_venue_unchecked(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    no_venue_sources(routes)
    r = status(f(booktitle="International Conference on Machine Learning", eprint="2301.00001"), "inproceedings")
    assert r["status"] == "VENUE_UNCHECKED"


def test_openalex_location_contradicts_the_venue(http):
    routes, calls = http
    routes["export.arxiv.org"] = feed("2301.00001")
    routes["api.openalex.org"] = json.dumps({"primary_location": {"source": {"display_name": "arXiv"}}, "locations": [
        {"source": {"display_name": "Conference on Robot Learning"}}]}).encode()
    routes["dblp.org"] = json.dumps({"result": {"hits": {"hit": []}}}).encode()
    r = status(f(booktitle="International Conference on Machine Learning", eprint="2301.00001"), "inproceedings")
    assert "MISMATCH: venue" in r["status"]
    assert sum("openalex" in c for c in calls) == 1 and sum("dblp" in c for c in calls) <= 1


def test_dblp_confirms_the_venue(http, monkeypatch):
    routes, calls = http
    monkeypatch.setenv("CONTACT_EMAIL", "a@b.org")
    routes["export.arxiv.org"] = feed("2301.00001")
    routes["api.openalex.org"] = nf()
    hit = {"info": {"title": T + ".", "venue": "ICML"}}
    routes["dblp.org"] = json.dumps({"result": {"hits": {"hit": [hit]}}}).encode()
    r = status(f(booktitle="International Conference on Machine Learning", eprint="2301.00001"), "inproceedings")
    assert r["status"] == "VERIFIED", r
    assert any("openalex" in c and "mailto=" in c for c in calls)


def test_howpublished_venue_is_checked(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    no_venue_sources(routes)
    assert status(f(eprint="2301.00001", howpublished="NeurIPS 2023"), "misc")["status"] == "VENUE_UNCHECKED"


@pytest.mark.parametrize("extra", [{"note": "arXiv:2399.99999"},
                                   {"howpublished": "\\url{https://arxiv.org/abs/2399.99999}"},
                                   {"volume": "abs/2399.99999"}, {"eprint": "arXiv:2399.99999"}])
def test_wrong_arxiv_id_in_any_field_is_found(http, extra):
    routes, _ = http
    routes["crossref"] = cr(venue="Nature")
    routes["export.arxiv.org"] = EMPTY
    r = status(f(journal="Nature", doi="10.1000/x1", **extra))
    assert "ID_NOT_FOUND" in r["status"]


def test_doi_in_note_is_resolved(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    routes["crossref"] = nf()
    routes["datacite"] = nf()
    assert "ID_NOT_FOUND: note" in status(f(eprint="2301.00001", note="doi: 10.1000/wrong"), "misc")["status"]


def test_corporate_author_does_not_switch_off_the_rest(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001", authors=["Open Collab", "Real Person"])
    r = status(f(author="{Open Collab} and Invented, Person", eprint="2301.00001"), "misc")
    assert "MISMATCH: authors" in r["status"]
    r = status(f(author="{Open Collab} and Person, Real", eprint="2301.00001"), "misc")
    assert r["status"] == "VERIFIED", r


def test_record_without_authors_is_authors_unchecked(http):
    routes, _ = http
    routes["crossref"] = cr(authors=[], venue="Nature")
    assert "AUTHORS_UNCHECKED" in status(f(author="Fake, A and Fake, B", journal="Nature", doi="10.1000/x1"))["status"]


def test_another_record_with_agreeing_authors_covers_an_empty_one(http):
    routes, _ = http
    routes["crossref"] = cr(authors=[], venue="Nature")
    routes["export.arxiv.org"] = feed("2301.00001")
    assert status(f(journal="Nature", doi="10.1000/x1", eprint="2301.00001"))["status"] == "VERIFIED"


def test_wrong_first_names_are_a_mismatch(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    assert "MISMATCH: authors" in status(f(author="Smith, Zed and Jones, Quinn", eprint="2301.00001"), "misc")["status"]


def test_only_others_is_a_finding(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    assert status(f(author="others", eprint="2301.00001"), "misc")["status"] != "VERIFIED"


def test_openreview_url_beside_an_eprint_is_noted(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    r = status(f(eprint="2301.00001", url="https://openreview.net/forum?id=BOGUS"), "misc")
    assert any("openreview" in n for n in r["notes"])


def test_year_up_to_last_version_is_not_accepted(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001", pub="2019", upd="2024")
    assert "MISMATCH: year" in status(f(year="2022", eprint="2301.00001"), "misc")["status"]


def test_greek_letters_keep_their_names():
    assert bc.norm("$\\beta$-Net") != bc.norm("α-Net") and bc.norm("$\\pi_0$") == bc.norm("π0")


def test_empty_normalised_title_is_a_finding(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001", title="")
    assert status(f(title="\\NoTitle", eprint="2301.00001"), "misc")["status"] != "VERIFIED"


def test_datacite_doi_is_resolved(http):
    routes, _ = http
    routes["crossref"] = nf()
    routes["api.datacite.org"] = json.dumps({"data": {"attributes": {
        "titles": [{"title": T}], "creators": [{"familyName": "Smith", "givenName": "Ann"},
                                               {"familyName": "Jones", "givenName": "Bob"}],
        "publicationYear": 2023, "container": {}}}}).encode()
    no_venue_sources(routes)
    assert status(f(doi="10.5281/zenodo.1"), "misc")["status"] == "VERIFIED"


def test_non_arxiv_eprinttype_is_a_note(http):
    routes, _ = http
    routes["crossref"] = cr(venue="Nature")
    r = status(f(journal="Nature", doi="10.1000/x1", eprint="12345678", eprinttype="pubmed"))
    assert r["status"] == "VERIFIED" and any("pubmed" in n for n in r["notes"])


def test_refusal_leaves_no_stale_output(tmp_path, monkeypatch):
    out = tmp_path / "o"
    out.mkdir()
    (out / "bib-check.json").write_text("[]")
    bad = tmp_path / "bad.bib"
    bad.write_text("@article{k, title = {never closed")
    monkeypatch.setattr("sys.argv", ["bib_check.py", str(out), str(bad)])
    with pytest.raises(SystemExit):
        bc.main()
    assert not (out / "bib-check.json").exists()


def test_several_bibs_and_only(http, tmp_path, monkeypatch):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    a, b = tmp_path / "a.bib", tmp_path / "b.bib"
    a.write_text(f"@misc{{one, title={{{T}}}, author={{{BIB_A}}}, year=2023, eprint={{2301.00001}}}}")
    b.write_text("@misc{two, title={Other}, author={X, Y}, year=2020, eprint={2399.99999}}")
    monkeypatch.setattr("sys.argv", ["bib_check.py", str(tmp_path / "o"), str(a), str(b), "--only", "one"])
    bc.main()
    assert [r["key"] for r in json.loads((tmp_path / "o" / "bib-check.json").read_text())] == ["one"]
    b.write_text("@misc{one, title={Dup}}")
    with pytest.raises(SystemExit):
        bc.load_bibs([a, b])


def test_dblp_bot_wall_is_an_error_not_silence(http):
    routes, _ = http
    routes["export.arxiv.org"] = feed("2301.00001")
    routes["api.openalex.org"] = nf()
    routes["dblp.org"] = b"<!doctype html><title>Making sure you're not a bot!</title>"
    r = status(f(booktitle="ICML", eprint="2301.00001"), "inproceedings")
    assert r["status"] == "VENUE_UNCHECKED" and any("bot check" in e for e in r["errors"])

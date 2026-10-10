# ABOUTME: bib_check: venue qualifiers (workshops, tracks) never swallowed by an acronym, arXiv id
# ABOUTME: forms, old-style ids, surnames such as Le, and a record without a year.
import json

import bib_check as bc
import pytest

T = "Deep Widgets for Robots"


def cr(venue=None, year=2023, authors=(("Ann", "Smith"), ("Bob", "Jones"))):
    m = {"title": [T], "container-title": [venue] if venue else [],
         "author": [{"given": g, "family": f} for g, f in authors]}
    if year:
        m["issued"] = {"date-parts": [[year]]}
    return json.dumps({"message": m}).encode()


EMPTY = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


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


def status(fields, typ="inproceedings"):
    return bc.check({"type": typ, "key": "k", "fields": {"title": T, "author": "Smith, Ann and Jones, Bob",
                                                         "year": "2023", **fields}})


@pytest.mark.parametrize("bib,rec", [("ICLR", "ICLR 2023 Workshop on Trustworthy ML"), ("CVPR", "CVPR Workshops"),
                                     ("NeurIPS", "NeurIPS Datasets and Benchmarks Track"),
                                     ("Robotics: Science and Systems", "RSS Workshop on Dexterous Manipulation"),
                                     ("AAAI", "AAAI Fall Symposium Series"),
                                     ("Artificial Intelligence", "AI Magazine")])
def test_qualified_venue_is_a_mismatch(http, bib, rec):
    routes, _ = http
    routes["api.crossref.org"] = cr(venue=rec)
    assert "MISMATCH: venue" in status({"booktitle": bib, "doi": "10.1000/xyz123"})["status"]


@pytest.mark.parametrize("bib,rec", [("Proceedings of Robotics: Science and Systems",
                                      "Robotics: Science and Systems XXI"),
                                     ("RSS", "Robotics: Science and Systems"), ("CoRL", "Conference on Robot Learning"),
                                     ("ICRA", "2024 IEEE International Conference on Robotics and Automation (ICRA)"),
                                     ("CVPR", "IEEE/CVF Conference on Computer Vision and Pattern Recognition"),
                                     ("NeurIPS", "Advances in Neural Information Processing Systems"),
                                     ("Proceedings of the 40th International Conference on Machine Learning", "ICML")])
def test_same_venue_still_matches(bib, rec):
    assert bc.venue_match(bib, rec)


@pytest.mark.parametrize("extra", [{"url": "https://arxiv.org/html/2106.99999v2"}, {"note": "arXiv~2106.99999"},
                                   {"note": "arXiv:{2106.99999}"},
                                   {"url": "https://www.semanticscholar.org/arxiv/2106.99999"}])
def test_more_arxiv_id_forms_are_looked_up(http, extra):
    routes, _ = http
    routes["api.crossref.org"] = cr(venue="Conference on Robot Learning")
    routes["export.arxiv.org"] = EMPTY
    assert "ID_NOT_FOUND" in status({"booktitle": "CoRL", "doi": "10.1000/xyz123", **extra})["status"]


def test_arxiv_preprint_journal_is_one_valid_id():
    ids = bc.identifiers({"journal": "arXiv preprint arXiv:2106.01234"})
    assert [(k, v) for _, k, v, _ in ids] == [("arxiv", "2106.01234")]


def test_old_style_id_with_subject_class_queries_without_it(http):
    routes, calls = http
    routes["export.arxiv.org"] = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/'
                                  b'math/0303109v2</id><title>T</title><published>2003-03-01T00:00:00Z</published>'
                                  b'</entry></feed>')
    assert bc.arxiv("math.DG/0303109") is not None and "math/0303109" in calls[0]


def test_le_is_a_surname_not_a_particle(http):
    routes, _ = http
    routes["api.crossref.org"] = cr(authors=[("Quoc V.", "Le")])
    r = bc.check({"type": "article", "key": "k", "fields": {"title": T, "author": "Le, Quoc V.", "year": "2023",
                                                            "doi": "10.1000/x"}})
    assert r["status"] == "VERIFIED", r
    assert bc.family("Quoc V. Le") == ["le"]


def test_record_without_year_is_year_unchecked(http):
    routes, _ = http
    routes["api.crossref.org"] = cr(venue="CoRL", year=None)
    assert "YEAR_UNCHECKED" in status({"booktitle": "CoRL", "doi": "10.1000/x", "year": "1999"})["status"]


@pytest.mark.parametrize("a,b,same", [("Science", "Science Advances", False),
                                      ("Mathematics", "Advances in Mathematics", False),
                                      ("Robotics", "Advances in Robotics", False), ("NIPS", "NeurIPS", True),
                                      ("NeurIPS 2023", "Advances in Neural Information Processing Systems", True),
                                      ("IROS", "IEEE/RSJ International Conference on Intelligent Robots and Systems",
                                       True),
                                      ("ICLR 2024", "The Twelfth International Conference on Learning Representations",
                                       True)])
def test_advances_aliases_and_ordinals(a, b, same):
    assert bc.venue_match(a, b) is same

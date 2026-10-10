# ABOUTME: bib_check on every printed venue field, arXiv-form skipping, crossref, note venue claims, title with
# ABOUTME: subtitle, duplicate fields, whole family names, strict venue words, the OpenAlex title-search fallback.
import json
import urllib.error

import bib_check as bc
import pytest

REC = {"source": "Crossref 10.1234/x", "title": "Real Paper Title", "authors": ["Smith, John", "Doe, Jane"],
       "years": [2020], "venue": None}
BASE = "title={Real Paper Title}, author={Smith, John and Doe, Jane}, year={2020}, doi={10.1234/x}"


@pytest.fixture
def canned(monkeypatch):
    monkeypatch.setattr(bc, "LOOKUPS", {"crossref": lambda v: dict(REC), "arxiv": lambda v: dict(REC, source="arXiv")})
    monkeypatch.setattr(bc, "VENUE_SOURCES", (("OpenAlex", lambda ids, t: ["Conference on Robot Learning"]),))


def check(bib):
    entries, errors = bc.parse_bib_report(bib)
    assert not errors, errors
    return bc.check(entries[0])


@pytest.mark.parametrize("fields", ["journaltitle={Fabricated Journal of Robots}",
                                    "journal={Fabricated Journal}, booktitle={CoRL}",
                                    "booktitle={CoRL}, series={Fabricated Series}",
                                    "booktitle={CoRL}, eventtitle={Fabricated Event}",
                                    "booktitle={CoRL}, organization={Fabricated Org}",
                                    "booktitle={arXiv}, howpublished={Nature 2020}",
                                    "booktitle={Fabricated Workshop (arXiv version)}",
                                    "journal={arXiv preprint}, note={Accepted at Nature}",
                                    "booktitle={CoRL}, crossref={p}"])
def test_every_printed_venue_field_is_checked(canned, fields):
    assert check("@inproceedings{k, " + BASE + ", " + fields + "}")["status"] != "VERIFIED"


def test_arxiv_form_venue_is_skipped(canned):
    r = check("@misc{k, " + BASE + ", journal={arXiv preprint arXiv:2106.01234}}")
    assert any("arXiv" in n for n in r["notes"])


def test_confirmed_venue_and_unchecked_extras_note(canned):
    r = check("@inproceedings{k, " + BASE + ", booktitle={CoRL}, pages={1--9}, publisher={P}}")
    assert r["status"] == "VERIFIED" and any("volume, pages, publisher" in n for n in r["notes"])


def test_fabricated_subtitle_is_a_title_finding(canned):
    assert check("@article{k, " + BASE + ", subtitle={A Hallucinated Subtitle}, booktitle={CoRL}}")["status"] != \
        "VERIFIED"


def test_biblatex_date_gives_the_year(canned):
    r = check("@article{k, title={Real Paper Title}, author={Smith, John and Doe, Jane}, date={2020-05}, "
              "doi={10.1234/x}, booktitle={CoRL}}")
    assert r["status"] == "VERIFIED", r


def test_duplicate_field_is_refused():
    _, errors = bc.parse_bib_report("@article{k, title={A}, title={B}}")
    assert errors and "duplicate field" in errors[0]


@pytest.mark.parametrize("a,b,same", [("Smith-Fake, John", "John Smith", False),
                                      ("García Pérez, Ana", "Ana García Pérez", True),
                                      ("van der Berg, Jan", "Jan van der Berg", True),
                                      ("de la Cruz, Ana", "Ana De La Cruz", True),
                                      ("Fake Smith, J.", "John Smith", False)])
def test_whole_family_name(a, b, same):
    assert bc.same_author(a, b) is same


@pytest.mark.parametrize("a,b", [("International Journal of Robotics Research", "Robotics Research"),
                                 ("Journal of Field Robotics", "Field Robotics"),
                                 ("IEEE Transactions on Robotics", "ACM Transactions on Robotics"),
                                 ("IEEE Transactions on Robotics", "Transactions on Robotics")])
def test_journal_international_ieee_acm_distinguish(a, b):
    assert not bc.venue_match(a, b)


def test_openalex_title_search_gives_the_published_venue(monkeypatch):
    def fake_get(url):
        if "works/doi:" in url:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if "works?search=" in url:
            return json.dumps({"results": [
                {"title": "Real Paper Title", "primary_location": {"source": {"display_name": "Conference on Robot "
                                                                                              "Learning"}}},
                {"title": "Another Paper", "primary_location": {"source": {"display_name": "ICML"}}}]}).encode()
        raise AssertionError(url)
    monkeypatch.setattr(bc, "get", fake_get)
    monkeypatch.setattr(bc, "LOOKUPS", {"crossref": lambda v: dict(REC)})
    monkeypatch.setattr(bc, "VENUE_SOURCES", bc.VENUE_SOURCES[:2])
    assert check("@inproceedings{k, " + BASE + ", booktitle={CoRL}}")["status"] == "VERIFIED"

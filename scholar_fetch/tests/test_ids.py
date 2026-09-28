# ABOUTME: Tests for identifying papers from URLs and extracting arXiv/DOI from text.
# ABOUTME: Tests for the URL chain endpoints: what id do we extract from a given URL?
import pytest

from scholar_fetch.ids import Ids, candidates, identify


def test_margin_stamp_comes_first_even_when_later_in_the_text():
    text = "We build on arXiv:1512.03385 ...\n arXiv:1706.03762v7 [cs.CL] 2 Aug 2023"
    assert candidates(text) == [("arxiv", "1706.03762"), ("arxiv", "1512.03385")]


def test_dois_are_normalised_and_arxiv_dois_become_arxiv_ids():
    text = "https://doi.org/10.1038/NATURE14539. and doi:10.48550/arXiv.2106.09685"
    assert candidates(text) == [("doi", "10.1038/nature14539"), ("arxiv", "2106.09685")]


def test_old_style_arxiv_id():
    assert candidates("see arXiv:cs/0112017v1") == [("arxiv", "cs/0112017")]


def test_doi_any_trims_a_capitalized_word_glued_on_by_a_missing_space():
    assert candidates("See 10.1038/nature14539.This changes everything.") == [("doi", "10.1038/nature14539")]


def test_doi_any_keeps_internal_dots_and_dashes_of_a_real_doi():
    text = "10.1016/j.cell.2015.05.001 and 10.1038/s41586-021-03819-2"
    assert candidates(text) == [("doi", "10.1016/j.cell.2015.05.001"), ("doi", "10.1038/s41586-021-03819-2")]


def test_a_citation_in_stamp_form_does_not_outrank_the_dated_margin_stamp():
    text = ("We build on prior work arXiv:1706.03762v5 [cs.CL] for the transformer "
            "architecture.\n\narXiv:2106.09685v2 [cs.LG]  5 Jun 2021")
    assert candidates(text) == [("arxiv", "2106.09685"), ("arxiv", "1706.03762")]


@pytest.mark.parametrize("url, want", [
    ("https://arxiv.org/abs/2511.15605", Ids(arxiv="2511.15605")),
    ("https://arxiv.org/abs/2511.15605v2", Ids(arxiv="2511.15605")),
    ("https://arxiv.org/pdf/2106.09685v1", Ids(arxiv="2106.09685")),
    ("https://arxiv.org/html/2510.13626v1#S4", Ids(arxiv="2510.13626")),
    ("https://arxiv.org/abs/cs/0112017", Ids(arxiv="cs/0112017")),
    ("https://doi.org/10.1038/nature14539", Ids(doi="10.1038/nature14539")),
    ("https://doi.org/10.48550/arXiv.2106.09685", Ids(arxiv="2106.09685")),
    ("https://dl.acm.org/doi/10.1145/3313831.3376167", Ids(doi="10.1145/3313831.3376167")),
    ("https://dl.acm.org/doi/pdf/10.1145/3313831.3376167?download=true", Ids(doi="10.1145/3313831.3376167")),
    ("https://link.springer.com/article/10.1007/s11263-020-01234-5", Ids(doi="10.1007/s11263-020-01234-5")),
    ("https://scholar.google.com/scholar_lookup?doi=10.1016%2Fj.cell.2015.05.001", Ids(doi="10.1016/j.cell.2015.05.001")),
    ("https://pubmed.ncbi.nlm.nih.gov/31285318/", Ids(pmid="31285318")),
    ("https://www.ncbi.nlm.nih.gov/pubmed/31285318", Ids(pmid="31285318")),
    ("https://github.com/sylvestf/LIBERO-plus", Ids()),
    ("https://huggingface.co/Sylvest/openvla-7b-oft-finetuned-libero-plus-mixdata", Ids()),
    ("https://www.sciencedirect.com/science/article/pii/S0092867415006340", Ids()),
])
def test_identify(url, want):
    assert identify(url) == want


def test_ids_any():
    assert not Ids().any()
    assert Ids(pmid="1").any()

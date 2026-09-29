# ABOUTME: Tests for identifying papers from URLs and extracting arXiv/DOI from text.
# ABOUTME: Moved candidates() tests from Research; new tests for URL identification.
import pytest

from scholar_fetch.ids import Ids, candidates, from_doi, identify, researchgate_title, same_title


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
    # Trailing view segments and slashes: publisher view words should be stripped
    ("https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1204166/full", Ids(doi="10.3389/fpsyg.2023.1204166")),
    ("https://doi.org/10.1145/3313831.3376167/", Ids(doi="10.1145/3313831.3376167")),
    ("https://onlinelibrary.wiley.com/doi/10.1111/cdev.13100/abstract", Ids(doi="10.1111/cdev.13100")),
    ("https://onlinelibrary.wiley.com/doi/10.1111/cdev.13100/pdf", Ids(doi="10.1111/cdev.13100")),
    # Multi-slash DOIs must be kept whole
    ("https://doi.org/10.1093/ajae/aaq063", Ids(doi="10.1093/ajae/aaq063")),
])
def test_identify(url, want):
    assert identify(url) == want


def test_ids_any():
    assert not Ids().any()
    assert Ids(pmid="1").any()


@pytest.mark.parametrize("url, want", [
    ("https://www.researchgate.net/publication/232458848_The_Effects_of_Feedback_Interventions_on_Performance_A_Historical_Review",
     "The Effects of Feedback Interventions on Performance A Historical Review"),
    ("https://www.researchgate.net/publication/385394918_The_current_evidence_of_solution-focused_brief_therapy?enrichId=x#pf2",
     "The current evidence of solution-focused brief therapy"),
    ("https://www.researchgate.net/publication/381119750_Clean_language_questions/link/abc/download", "Clean language questions"),
    ("https://www.researchgate.net/profile/Some-One", None),
    ("https://doi.org/10.1038/nature14539", None),
])
def test_researchgate_title(url, want):
    assert researchgate_title(url) == want
    assert identify(url).any() is (url.startswith("https://doi.org"))  # identify itself never guesses from a title


@pytest.mark.parametrize("a, b, same", [
    ("The effects of feedback interventions on performance: A historical review",
     "The Effects of Feedback Interventions on Performance A Historical Review", True),
    ("Sycophantic AI decreases prosocial <i>intentions</i>", "Sycophantic AI Decreases Prosocial Intentions", True),
    ("Effects of feedback intervention on performance", "The Effects of Feedback Interventions on Performance", False),
    ("", "", False),
])
def test_same_title(a, b, same):
    assert same_title(a, b) is same


def test_from_doi_maps_an_arxiv_datacite_doi_to_its_arxiv_id():
    assert from_doi("https://doi.org/10.48550/arXiv.2510.01395") == Ids(arxiv="2510.01395")
    assert from_doi("https://doi.org/10.1037/0033-2909.119.2.254") == Ids(doi="10.1037/0033-2909.119.2.254")

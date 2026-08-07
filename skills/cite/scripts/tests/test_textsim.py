# ABOUTME: Tests near-duplicate detection that flags syndicated/copy-pasted sources as non-independent.
import textsim

A = "The global AI market reached 2527 billion dollars in 2026, according to a new Gartner study released Monday."
SYNDICATED = "The global AI market reached 2527 billion dollars in 2026, according to a new Gartner study released Monday."
PARAPHRASE = "An independent McKinsey survey of 1800 firms found adoption climbing from 55 to 88 percent over two years."

def test_identical_text_is_near_duplicate():
    assert textsim.near_duplicate(A, SYNDICATED) is True

def test_distinct_text_is_not_near_duplicate():
    assert textsim.near_duplicate(A, PARAPHRASE) is False

def test_ratio_is_symmetric_and_bounded():
    r = textsim.similarity(A, PARAPHRASE)
    assert 0.0 <= r <= 1.0
    assert abs(textsim.similarity(A, PARAPHRASE) - textsim.similarity(PARAPHRASE, A)) < 1e-9

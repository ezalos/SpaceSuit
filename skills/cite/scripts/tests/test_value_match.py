# ABOUTME: Tests numeric value-match used by auto-promote (conservative: scale words never auto-match).
import validate_claim as vc

def test_same_number_with_separators_matches():
    assert vc.values_match("Le marché atteint 2 527 milliards", "reached 2,527 billion dollars") is True

def test_percent_matches():
    assert vc.values_match("adoption de 88%", "rose to 88 percent") is True

def test_missing_number_is_mismatch():
    assert vc.values_match("88% adoption", "rose to 91 percent") is False

def test_scale_mismatch_does_not_auto_match():
    # claim says 2.5, source says 2500 -> different digit tokens -> no match (flag for review)
    assert vc.values_match("2.5 trillion", "2500 billion") is False

def test_no_numbers_is_not_determinable():
    assert vc.values_match("the company is the market leader", "they lead the market") is False
    assert vc.value_determinable("the company is the market leader") is False
    assert vc.value_determinable("revenue of 12 billion") is True

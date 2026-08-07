# ABOUTME: Tests claim YAML validation: required fields, enums, quote-in-page, extended diagnosis statuses.
from pathlib import Path
import yaml, validate_claim as vc

FX = Path(__file__).parent / "fixtures"

def _claim(): return yaml.safe_load((FX / "claim_ok.yaml").read_text())
def _page(): return (FX / "page_ok.txt").read_text()

def test_valid_claim_has_no_errors():
    assert vc.validate(_claim(), _page()) == []

def test_quote_not_in_page_is_error():
    c = _claim(); c["proposed_source"]["quote"] = "This sentence is absent."
    errs = vc.validate(c, _page())
    assert any("quote" in e for e in errs)

def test_new_diagnosis_status_allowed():
    c = _claim(); c["status"] = "cited-stale"
    assert vc.validate(c, _page()) == []

def test_unknown_status_rejected():
    c = _claim(); c["status"] = "bogus"
    assert any("status" in e for e in vc.validate(c, _page()))

def test_pending_audit_status_allowed():
    c = _claim(); c["status"] = "pending-audit"
    assert vc.validate(c, _page()) == []

# --- ported from Markdowns2Teach scripts/cite/tests/test_validate_claim.py
# (repo mirror being retired; see docs/references/ in that repo) ---

def test_missing_required_field_fails():
    c = _claim(); del c["proposed_source"]["url"]
    errs = vc.validate(c, _page())
    assert any("url" in e for e in errs)

def test_invalid_confidence_fails():
    c = _claim(); c["proposed_source"]["confidence"] = "certain"  # not in enum
    errs = vc.validate(c, _page())
    assert any("confidence" in e for e in errs)

def test_surrounding_paragraph_not_in_page_is_error():
    c = _claim()
    c["proposed_source"]["surrounding_paragraph"] = "Fabricated paragraph that cannot be found in the page."
    errs = vc.validate(c, _page())
    assert any("surrounding_paragraph" in e for e in errs)

def test_malformed_publication_date_fails():
    c = _claim(); c["proposed_source"]["publication_date"] = "September 2010"  # not YYYY-MM-DD
    errs = vc.validate(c, _page())
    assert any("publication_date" in e for e in errs)

def test_url_domain_mismatch_fails():
    c = _claim(); c["proposed_source"]["url_domain"] = "wrong-domain.com"
    errs = vc.validate(c, _page())
    assert any("url_domain" in e for e in errs)

def test_whitespace_normalization_passes():
    c = _claim()
    # Extra whitespace runs in the quote; validator should still match via normalization.
    c["proposed_source"]["quote"] = (
        "The  global  AI market reached  2527 billion   dollars in 2026 according to the firm."
    )
    assert vc.validate(c, _page()) == []

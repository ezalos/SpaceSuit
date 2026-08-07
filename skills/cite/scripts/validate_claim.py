#!/usr/bin/env python3
# ABOUTME: Validate a claim YAML against schema, enums, and quote-in-page substring rules.
# ABOUTME: Exit 0 = valid. Exit 1 = invalid, stderr = human-readable failure list.
# Contract cousin: Markdowns2Teach's scripts/verify-sources.py applies the same
# verbatim-quote idea to deck artifacts — shared CONTRACT, independent code, on purpose
# (2026-07-13 deck-capability design in that repo). Do not unify or fork blindly.

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

STATUS_ENUM = {
    "pending", "auto-approved", "approved", "rejected", "needs-rework",
    "flagged-low-reputation", "flagged-unsourceable", "flagged-stale-stat",
    "flagged-validation-failed",
    # diagnosis of existing citations
    "uncited", "cited-healthy", "cited-broken", "cited-stale", "cited-low-tier", "pending-audit",
    # promote / corroboration outcomes
    "auto-promoted", "flagged-claim-conflict", "flagged-better-source",
}
PROPOSED_ACTION_ENUM = {
    None, "add-citation", "update-claim-value", "soften-language", "none",
    "swap-source", "promote-source",
}
CONFIDENCE_ENUM = {"high", "medium", "low"}
RECENCY_ENUM = {"fresh", "recent", "stale", "historical-event", "unknown", None}

REQUIRED_LOCATION = {"file", "slide", "line"}
REQUIRED_CLAIM = {"text", "type", "has_existing_source"}
REQUIRED_SOURCE = {
    "url", "url_domain", "publisher_org", "publication_date", "accessed_date",
    "quote", "surrounding_paragraph", "section_heading",
    "alignment_justification", "confidence",
}


def _normalize_whitespace(s):
    return re.sub(r"\s+", " ", s).strip()


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text):
    """Extract comparable numeric tokens after stripping thousands separators."""
    if not text:
        return set()
    # remove thousands separators: ',' and ASCII/narrow/no-break spaces between digits
    cleaned = re.sub(r"(?<=\d)[,   ](?=\d)", "", text)
    return set(_NUM_RE.findall(cleaned))


def value_determinable(claim_text):
    """True if the claim contains numeric tokens we can verify against a source."""
    return len(_numbers(claim_text)) > 0


def values_match(claim_text, quote):
    """Conservative numeric match: every number in the claim must appear in the quote.

    Returns False when the claim has no numbers (not determinable) — callers should
    use value_determinable() to distinguish 'mismatch' from 'unknown'. Scale words
    (billion/trillion) are NOT reconciled: differing digit tokens never auto-match.
    """
    claim_nums = _numbers(claim_text)
    if not claim_nums:
        return False
    quote_nums = _numbers(quote)
    return claim_nums.issubset(quote_nums)


def _registered_domain(url):
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def validate(claim, page_text):
    errors = []

    # Top-level required fields
    for field in ("id", "location", "claim", "proposed_source", "status"):
        if field not in claim:
            errors.append(f"missing top-level field: {field}")

    loc = claim.get("location") or {}
    for field in REQUIRED_LOCATION:
        if field not in loc:
            errors.append(f"missing location.{field}")

    clm = claim.get("claim") or {}
    for field in REQUIRED_CLAIM:
        if field not in clm:
            errors.append(f"missing claim.{field}")

    src = claim.get("proposed_source") or {}
    for field in REQUIRED_SOURCE:
        if field not in src:
            errors.append(f"missing proposed_source.{field}")

    # Enum checks
    if claim.get("status") not in STATUS_ENUM:
        errors.append(f"status '{claim.get('status')}' not in enum {sorted(STATUS_ENUM)}")
    if claim.get("proposed_action") not in PROPOSED_ACTION_ENUM:
        errors.append(
            f"proposed_action '{claim.get('proposed_action')}' not in enum"
        )
    if src.get("confidence") not in CONFIDENCE_ENUM:
        errors.append(
            f"proposed_source.confidence '{src.get('confidence')}' not in enum"
        )
    if "recency_verdict" in src and src["recency_verdict"] not in RECENCY_ENUM:
        errors.append(
            f"proposed_source.recency_verdict '{src.get('recency_verdict')}' not in enum"
        )

    # publication_date format
    pub_date = src.get("publication_date")
    if pub_date is not None:
        try:
            datetime.strptime(str(pub_date), "%Y-%m-%d")
        except ValueError:
            errors.append(f"publication_date '{pub_date}' is not YYYY-MM-DD")

    # url_domain matches the parsed domain of url
    url = src.get("url")
    url_domain = src.get("url_domain")
    if url and url_domain:
        parsed = _registered_domain(url)
        if url_domain.lower() != parsed:
            errors.append(
                f"url_domain '{url_domain}' does not match parsed domain '{parsed}' of url"
            )

    # Quote and surrounding_paragraph must appear verbatim (whitespace-normalized) in page_text
    normalized_page = _normalize_whitespace(page_text)
    for field in ("quote", "surrounding_paragraph"):
        value = src.get(field)
        if value is None:
            continue
        normalized_value = _normalize_whitespace(value)
        if normalized_value and normalized_value not in normalized_page:
            errors.append(
                f"proposed_source.{field} does not appear verbatim in page.txt"
            )

    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("claim_yaml", help="Path to claim-NN.yaml")
    parser.add_argument("page_txt", help="Path to claim-NN.page.txt")
    args = parser.parse_args()

    try:
        claim = yaml.safe_load(Path(args.claim_yaml).read_text())
    except yaml.YAMLError as e:
        print(f"YAML parse error: {e}", file=sys.stderr)
        sys.exit(1)
    page_text = Path(args.page_txt).read_text()

    errors = validate(claim, page_text)
    if errors:
        print(f"validation failed for {args.claim_yaml}:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# ABOUTME: CLI wrapper over validate_claim.values_match for the remediate orchestrator.
# ABOUTME: Prints "match" / "mismatch" / "unknown" on stdout. Logic lives in validate_claim.py.

import argparse
import sys

from validate_claim import value_determinable, values_match


def verdict(claim_text, quote):
    if not value_determinable(claim_text):
        return "unknown"
    return "match" if values_match(claim_text, quote) else "mismatch"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("claim_text")
    p.add_argument("quote")
    args = p.parse_args()
    print(verdict(args.claim_text, args.quote))
    sys.exit(0)


if __name__ == "__main__":
    main()

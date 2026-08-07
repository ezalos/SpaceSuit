#!/usr/bin/env python3
# ABOUTME: Pure deterministic decision tables for the cite pipeline — no LLM judgment, no I/O in core fns.
# ABOUTME: recency_verdict / status_for / promote_verdict / corroboration_status / apply_corroboration (+CLI).

import argparse
import json
import sys
from datetime import date, datetime

HISTORICAL_CUTOFF = date(2020, 1, 1)
AUTO_APPROVE_TIERS = {1, 2, 3, 4}
AUTO_APPROVE_RECENCY = {"fresh", "recent", "historical-event"}


def _parse(d):
    if not d:
        return None
    return datetime.strptime(str(d), "%Y-%m-%d").date()


def recency_verdict(publication_date, today, claim_type=None):
    # A settled past event/milestone is exempt from the staleness filter regardless
    # of how old the source is — "ChatGPT hit 100M WAU in 2023" doesn't rot with age.
    if claim_type == "historical-event":
        return "historical-event"
    pd = _parse(publication_date)
    if pd is None:
        return "unknown"
    if pd < HISTORICAL_CUTOFF:
        return "historical-event"
    age = (today - pd).days
    if age <= 180:
        return "fresh"
    if age <= 365:
        return "recent"
    return "stale"


def status_for(tier, recency):
    if tier in AUTO_APPROVE_TIERS and recency in AUTO_APPROVE_RECENCY:
        return ("auto-approved", None)
    if tier not in AUTO_APPROVE_TIERS:
        return ("flagged-low-reputation", f"tier {tier} below auto-approve threshold")
    return ("flagged-low-reputation", f"recency '{recency}' not auto-approvable")


def promote_verdict(orig_tier, orig_date, new_tier, new_date, value_match, value_determinable):
    """Auto-promote an existing citation to a newer source — SOURCE SWAP ONLY."""
    if not value_determinable:
        return "flag-better-source"          # can't confirm same value -> human reviews
    if not value_match:
        return "flag-claim-conflict"         # new source states a different value
    od, nd = _parse(orig_date), _parse(new_date)
    if od is None or nd is None:
        return "flag-better-source"          # date ambiguity -> review
    if nd <= od:
        return "keep"                        # not actually newer
    if new_tier is None or orig_tier is None:
        return "flag-better-source"
    if new_tier <= orig_tier:                # numerically lower tier == more authoritative
        return "auto-promote"
    return "flag-better-source"              # newer but less authoritative


def corroboration_status(secondaries):
    if not secondaries:
        return "uncorroborated"
    validated = [s for s in secondaries if s.get("validated")]
    if any(s.get("value_match") is False for s in validated):
        return "conflicting"
    if any(s.get("independent") and s.get("value_match") for s in validated):
        return "confirmed"
    return "weak"


def apply_corroboration(base_status, tier, secondaries):
    """A flagged low-tier claim becomes auto-approved if an independent, validated,
    value-matching secondary of tier <=4 corroborates it."""
    if base_status != "flagged-low-reputation":
        return base_status
    for s in secondaries:
        if (s.get("validated") and s.get("independent") and s.get("value_match")
                and isinstance(s.get("tier"), int) and s["tier"] <= 4):
            return "auto-approved"
    return base_status


def _today():
    return date.today()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recency"); r.add_argument("date"); r.add_argument("--type", dest="claim_type", default=None)
    s = sub.add_parser("status"); s.add_argument("tier"); s.add_argument("recency")
    pr = sub.add_parser("promote")
    for a in ("orig_tier", "orig_date", "new_tier", "new_date"):
        pr.add_argument(a)
    pr.add_argument("value_match"); pr.add_argument("value_determinable")

    co = sub.add_parser("corroborate")
    co.add_argument("--base-status", required=True)
    co.add_argument("--tier", default="null")
    co.add_argument("--secondaries-json", required=True)

    args = p.parse_args()

    def _t(v):
        return None if v in ("null", "None", "") else int(v)

    def _b(v):
        return v.lower() in ("true", "1", "yes", "match")

    if args.cmd == "recency":
        print(recency_verdict(args.date, _today(), args.claim_type))
    elif args.cmd == "status":
        print(json.dumps(status_for(_t(args.tier), args.recency)))
    elif args.cmd == "promote":
        print(promote_verdict(_t(args.orig_tier), args.orig_date, _t(args.new_tier),
                              args.new_date, _b(args.value_match), _b(args.value_determinable)))
    elif args.cmd == "corroborate":
        secs = json.loads(args.secondaries_json)
        cs = corroboration_status(secs)
        fs = apply_corroboration(args.base_status, _t(args.tier), secs)
        print(json.dumps({"corroboration_status": cs, "final_status": fs}))
    sys.exit(0)


if __name__ == "__main__":
    main()

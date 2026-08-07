#!/usr/bin/env python3
# ABOUTME: Look up authority tier for a URL domain against one or more layered authority maps.
# ABOUTME: Later --map layers override earlier ones. Outputs tier integer (1-6) or "null". No LLM judgment.

import argparse
import sys
from pathlib import Path

import yaml

DEFAULT_MAP = Path(__file__).resolve().parents[1] / "memory/authority-map.yaml"


def load_map(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _domain_chain(domain):
    """Yield the domain and each parent (news.foo.com -> news.foo.com, foo.com)."""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def _domain_to_tier(authority_map):
    out = {}
    for tier_num, tier_data in (authority_map or {}).get("tiers", {}).items():
        for publisher in tier_data.get("publishers", []):
            for d in publisher.get("domains", []):
                out[d.lower()] = int(tier_num)
    return out


def lookup(domain, authority_map):
    return lookup_layered(domain, [authority_map])


def lookup_layered(domain, authority_maps):
    domain = (domain or "").strip().lower()
    if not domain:
        return None
    merged = {}
    for amap in authority_maps:          # later layers override earlier
        merged.update(_domain_to_tier(amap))
    for candidate in _domain_chain(domain):
        if candidate in merged:
            return merged[candidate]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", help="URL domain to look up (e.g., sec.gov)")
    parser.add_argument("--map", action="append", default=None,
                        help="Path to an authority-map.yaml; repeatable, later wins")
    args = parser.parse_args()
    paths = args.map or [str(DEFAULT_MAP)]
    layers = [load_map(p) for p in paths]
    tier = lookup_layered(args.domain, layers)
    print("null" if tier is None else str(tier))
    sys.exit(0)


if __name__ == "__main__":
    main()

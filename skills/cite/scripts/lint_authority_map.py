#!/usr/bin/env python3
# ABOUTME: Lint that authority-map.md and authority-map.yaml describe the same tiered publisher roster.
# ABOUTME: Exit 0 = in sync. Exit 1 = drift, stderr = list of mismatches.

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]            # was parents[2]
DEFAULT_MD = REPO_ROOT / "memory/authority-map.md"          # was docs/references/authority-map.md
DEFAULT_YAML = REPO_ROOT / "memory/authority-map.yaml"      # was docs/references/authority-map.yaml


def parse_md_domains(md_text):
    """Return set of (tier, domain) pairs extracted from bullets in authority-map.md.

    Stops at the horizontal rule (---) that follows tier content, so prose sections
    below the roster are ignored. A --- before any tier heading has been seen is
    treated as an intro separator and skipped. Skips:
      - glob patterns (contain *)
      - template placeholders (contain < or >)
      - path-only tokens (start with /)
      - URL paths that are example sub-paths, not publisher domains
        (e.g. 'anthropic.com/news' — bare_domain regex rejects entries with /)
    """
    pairs = set()
    current_tier = None
    tier_found = False
    tier_heading = re.compile(r"^##\s+Tier\s+(\d)\s+—")
    # Matches the domain inside backticks: `domain.com` or `sub.domain.com`
    bullet_domains = re.compile(r"`([^`]+)`")
    # A bare domain looks like: letters/digits/hyphens, one or more dots, letters
    # with NO slash after the TLD part (path examples are excluded)
    bare_domain = re.compile(r"^[\w.\-]+\.[a-zA-Z]{2,}$")

    for line in md_text.splitlines():
        # A horizontal rule after tiers have started marks end of roster
        if line.strip() == "---":
            if tier_found:
                break
            # Before any tier — intro separator, skip
            continue
        m = tier_heading.match(line)
        if m:
            current_tier = int(m.group(1))
            tier_found = True
            continue
        if current_tier is None or not line.lstrip().startswith("-"):
            continue
        for d in bullet_domains.findall(line):
            # Skip glob patterns, template placeholders, code-slash terms
            if "*" in d or "<" in d or ">" in d:
                continue
            if d.startswith("/"):
                continue
            # Only accept entries that look like bare domains (no path component)
            if not bare_domain.match(d):
                continue
            pairs.add((current_tier, d.lower()))
    return pairs


def yaml_domains(yaml_data):
    pairs = set()
    for tier_num, tier_data in yaml_data.get("tiers", {}).items():
        for publisher in tier_data.get("publishers", []):
            for d in publisher.get("domains", []):
                pairs.add((int(tier_num), d.lower()))
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--md", default=str(DEFAULT_MD))
    parser.add_argument("--yaml", default=str(DEFAULT_YAML))
    args = parser.parse_args()

    md_pairs = parse_md_domains(Path(args.md).read_text())
    yml_pairs = yaml_domains(yaml.safe_load(Path(args.yaml).read_text()))

    errors = []
    only_in_yaml = yml_pairs - md_pairs
    only_in_md = md_pairs - yml_pairs
    for tier, domain in sorted(only_in_yaml):
        errors.append(f"tier {tier}: '{domain}' in yaml but not in md")
    for tier, domain in sorted(only_in_md):
        errors.append(f"tier {tier}: '{domain}' in md but not in yaml")

    if errors:
        print("authority-map.md and authority-map.yaml disagree:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print("authority-map.md and authority-map.yaml are in sync.")
    sys.exit(0)


if __name__ == "__main__":
    main()

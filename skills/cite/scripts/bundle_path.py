#!/usr/bin/env python3
# ABOUTME: Resolve the run-state bundle directory for a target document.
# ABOUTME: Central default ~/.local/state/cite/<slug>/; prefers a pre-existing legacy docs/citation-audit/<slug>/.

import argparse
import os
import sys
from pathlib import Path


def slug_for(target_path):
    p = str(target_path).strip()
    if p.endswith(".md"):
        p = p[:-3]
    slug = p.replace("/", "-").strip("-")
    return slug


def _state_home():
    env = os.environ.get("CITE_STATE_HOME")
    if env:
        return Path(env)
    return Path.home() / ".local" / "state" / "cite"


def bundle_dir(target_path):
    slug = slug_for(target_path)
    legacy = Path.cwd() / "docs" / "citation-audit" / slug
    if legacy.exists():
        return legacy
    return _state_home() / slug


def main():
    p = argparse.ArgumentParser()
    p.add_argument("target")
    p.add_argument("--ensure", action="store_true", help="mkdir -p the bundle + claims/")
    args = p.parse_args()
    d = bundle_dir(args.target)
    if args.ensure:
        (d / "claims").mkdir(parents=True, exist_ok=True)
    print(d)
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# ABOUTME: Janitor for share-file — removes /srv/share/<token>/ dirs whose .expires is past
# ABOUTME: Invoked by share-cleanup.timer on TinyButMighty every 5 minutes
"""
cleanup.py [--root /srv/share] [--dry-run]

For each <token> dir under root, read .expires (unix epoch). If now > expires,
remove the dir. Dirs without .expires are skipped (left for human inspection).

Stdlib only. Idempotent. Safe to run as the share owner.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("/srv/share"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.root.is_dir():
        sys.stderr.write(f"cleanup: root {args.root} is not a directory\n")
        return 1

    now = int(time.time())
    removed = 0
    skipped_no_expires = 0
    kept = 0

    for child in args.root.iterdir():
        if not child.is_dir():
            continue
        expires_file = child / ".expires"
        if not expires_file.is_file():
            skipped_no_expires += 1
            continue
        try:
            expires_at = int(expires_file.read_text().strip())
        except ValueError:
            sys.stderr.write(f"cleanup: bad .expires in {child}, skipping\n")
            continue
        if now > expires_at:
            if args.dry_run:
                print(f"would remove: {child}")
            else:
                shutil.rmtree(child)
            removed += 1
        else:
            kept += 1

    sys.stderr.write(
        f"cleanup: removed={removed} kept={kept} no_expires={skipped_no_expires}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

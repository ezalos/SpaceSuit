#!/usr/bin/env python3
# ABOUTME: Text-similarity for source-independence checks. High similarity => syndicated/copy => not independent.
# ABOUTME: similarity() in [0,1]; near_duplicate() applies a default 0.8 threshold. CLI prints JSON.

import argparse
import json
import re
import sys
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.8


def _normalize(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def similarity(a, b):
    na, nb = _normalize(a), _normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    # Sort args so similarity(a,b)==similarity(b,a): SequenceMatcher is not symmetric by itself.
    first, second = (na, nb) if na <= nb else (nb, na)
    return SequenceMatcher(None, first, second).ratio()


def near_duplicate(a, b, threshold=DEFAULT_THRESHOLD):
    return similarity(a, b) >= threshold


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file_a")
    p.add_argument("file_b")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = p.parse_args()
    from pathlib import Path
    a = Path(args.file_a).read_text()
    b = Path(args.file_b).read_text()
    ratio = similarity(a, b)
    print(json.dumps({"similarity": round(ratio, 4),
                      "near_duplicate": ratio >= args.threshold}))
    sys.exit(0)


if __name__ == "__main__":
    main()

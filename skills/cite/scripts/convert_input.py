#!/usr/bin/env python3
# ABOUTME: Route a citation target to its converter and (with --run) emit converted.md into the bundle.
# ABOUTME: md=native, pdf=pdftotext, html/docx=pandoc, http(s)=fetch+pandoc. Pure route() is unit-tested.

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


def route(target):
    t = str(target).lower()
    if t.startswith(("http://", "https://")):
        return "url"
    if t.endswith(".md"):
        return "native"
    if t.endswith(".pdf"):
        return "pdftotext"
    if t.endswith((".html", ".htm", ".docx")):
        return "pandoc"
    raise ValueError(f"unsupported input format: {target}")


def convert(target, out_dir):
    """Return the path to a markdown file representing `target`. May be the original."""
    kind = route(target)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "native":
        return Path(target)
    out = out_dir / "converted.md"
    if kind == "pdftotext":
        subprocess.run(["pdftotext", "-layout", str(target), str(out.with_suffix(".txt"))], check=True)
        out.write_text(out.with_suffix(".txt").read_text())
    elif kind == "pandoc":
        subprocess.run(["pandoc", str(target), "-t", "gfm", "-o", str(out)], check=True)
    elif kind == "url":
        raw = out_dir / "fetched.html"
        with urlopen(target, timeout=30) as resp:       # nosec - user-supplied target
            raw.write_bytes(resp.read())
        subprocess.run(["pandoc", str(raw), "-f", "html", "-t", "gfm", "-o", str(out)], check=True)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("target")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run", action="store_true", help="perform conversion (else just print the route)")
    args = p.parse_args()
    if args.run:
        print(convert(args.target, args.out_dir))
    else:
        print(route(args.target))
    sys.exit(0)


if __name__ == "__main__":
    main()

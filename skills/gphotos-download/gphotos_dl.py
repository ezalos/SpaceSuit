#!/usr/bin/env python3
# ABOUTME: Download full-res photos from a PUBLIC Google Photos share link (no auth).
# ABOUTME: Scrapes the share page for /pw/ URLs and fetches each at =d (original size).
import argparse
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ExifTags

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# The album photos are served from lh3 under /pw/<token>; owner avatars sit under /a/ and /a-/ and
# are excluded by matching /pw/ only. The initial share-page HTML embeds these URLs directly.
PW_RE = re.compile(r"https://lh3\.googleusercontent\.com/pw/[A-Za-z0-9_-]+")


def extract_photo_urls(html: str) -> list:
    """The distinct /pw/ photo base URLs in a share page's HTML, order preserved (pure/testable)."""
    return list(dict.fromkeys(PW_RE.findall(html)))


def _fetch(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:   # follows the app.goo.gl -> share redirect
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def _exif_warning(path: Path) -> str:
    """Non-empty string naming identifying EXIF (GPS/device) if present — a deanonymization vector in
    the pro tree. Google usually strips EXIF on =d, but we check every file rather than assume."""
    try:
        exif = Image.open(path).getexif()
    except Exception:
        return ""
    if not exif:
        return ""
    hits = []
    if exif.get_ifd(ExifTags.IFD.GPSInfo):
        hits.append("GPS")
    for k, v in exif.items():
        if ExifTags.TAGS.get(k) in ("Make", "Model", "Software", "Artist", "HostComputer"):
            hits.append(ExifTags.TAGS[k])
    return ", ".join(hits)


def download_album(share_url: str, out_dir: Path, size: str = "=d", prefix: str = "gphoto") -> list:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = extract_photo_urls(_fetch(share_url))
    if not urls:
        sys.exit("no /pw/ photos found in the page — is this a PUBLIC 'anyone with the link' share?")
    print(f"found {len(urls)} photo(s) (note: very large albums may lazy-load beyond the initial page)")
    saved = []
    for i, u in enumerate(urls, 1):
        data = _fetch(u + size, binary=True)
        dest = out_dir / f"{prefix}_{i:02d}.jpg"
        dest.write_bytes(data)
        try:
            w, h = Image.open(dest).size
        except Exception:
            sys.exit(f"downloaded {dest} is not a valid image ({len(data)} bytes) — link may be private")
        warn = _exif_warning(dest)
        print(f"  {dest}  {w}x{h}  {len(data)} bytes" + (f"  ⚠ EXIF: {warn} (consider stripping)" if warn else ""))
        saved.append(dest)
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(description="Download a PUBLIC Google Photos share album, full-res.")
    ap.add_argument("share_url", help="photos.app.goo.gl/… or photos.google.com/share/…?key=…")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--size", default="=d", help="lh3 size suffix (=d original, =s0, =w2048-h2048)")
    ap.add_argument("--prefix", default="gphoto", help="output filename prefix")
    ns = ap.parse_args()
    download_album(ns.share_url, ns.out, ns.size, ns.prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

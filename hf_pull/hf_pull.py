#!/usr/bin/env python3
# ABOUTME: Rate-capped, sequential Hugging Face repo downloader that fills the standard
# ABOUTME: hub cache, so from_pretrained() works afterwards without pulling at line rate.
"""
hf-pull REPO_ID [--rate 25M] [--revision main] [--include GLOB]... [--exclude GLOB]...
                [--type model|dataset] [--dry-run]

Why this exists: `hf download` opens eight connections and pulls at whatever the
line gives. On a shared residential link that starves the gateway for everyone in
the house. This tool pulls ONE file at a time through `curl --limit-rate`, verifies
each blob (sha256 for LFS, git-sha1 otherwise), and writes the official cache
layout (blobs/, snapshots/<commit>/, refs/<revision>) — the result is
indistinguishable from an `hf download` for huggingface_hub, and resumable.

Stdlib + curl only. Token: $HF_TOKEN, else ~/.cache/huggingface/token.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

API = "https://huggingface.co"
DEFAULT_RATE = os.environ.get("HF_PULL_RATE", "25M")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def token():
    t = os.environ.get("HF_TOKEN")
    if t:
        return t.strip()
    p = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def cache_root():
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    return os.path.join(home, "hub")


def api_get(url, tok, attempts=4):
    """One Hub API call. Retries on 429/5xx and on network errors with a short backoff;
    4xx other than 429 are the caller's problem (gated, missing, bad revision)."""
    req = urllib.request.Request(url, headers={"User-Agent": "hf-pull/1"})
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        if i < attempts - 1:
            wait = 5 * (i + 1)
            log(f"hf-pull: Hub API {last}; retrying in {wait}s")
            time.sleep(wait)
    raise last


def list_tree(kind, repo, rev, tok):
    """All files of the repo at `rev`, following the API's Link pagination."""
    base = f"{API}/api/{kind}s/{repo}/tree/{urllib.parse.quote(rev, safe='')}"
    url = base + "?recursive=true&expand=true"
    files = []
    while url:
        data, hdr = api_get(url, tok)
        files += [x for x in data if x.get("type") == "file"]
        nxt = None
        for part in (hdr.get("Link") or "").split(","):
            if 'rel="next"' in part:
                nxt = part.split("<", 1)[1].split(">", 1)[0]
        url = nxt
    return files


def commit_sha(kind, repo, rev, tok):
    data, _ = api_get(f"{API}/api/{kind}s/{repo}/revision/{urllib.parse.quote(rev, safe='')}", tok)
    return data["sha"]


def parse_rate(s):
    """curl-style rate ('25M', '800k') -> bytes/s, for the ETA only."""
    s = s.strip()
    mult = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(s[-1].lower(), 1)
    return int(float(s.rstrip("kKmMgG"))) * mult


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def digest(path, lfs, size):
    if lfs:
        h = hashlib.sha256()
    else:
        h = hashlib.sha1()
        h.update(f"blob {size}\0".encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def curl(url, out, rate, tok):
    """One capped, resumable transfer. The token travels in a 0600 config file, never argv.
    Authorization is dropped by curl on the cross-host redirect to the CDN (the signed
    CDN URL carries its own auth), which is the safe behaviour."""
    with tempfile.NamedTemporaryFile("w", delete=False, prefix="hf-pull.", suffix=".curlrc") as cfg:
        os.chmod(cfg.name, 0o600)
        cfg.write(f'url = "{url}"\n')
        if tok:
            cfg.write(f'header = "Authorization: Bearer {tok}"\n')
    try:
        cmd = ["curl", "-K", cfg.name, "-fSL", "--limit-rate", rate, "-C", "-",
               "--retry", "5", "--retry-delay", "5", "--retry-all-errors",
               "-o", out, "-#" if sys.stderr.isatty() else "--no-progress-meter"]
        r = subprocess.run(cmd)
        return r.returncode
    finally:
        os.unlink(cfg.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--rate", default=DEFAULT_RATE, help=f"curl --limit-rate value (default {DEFAULT_RATE})")
    ap.add_argument("--revision", default="main")
    ap.add_argument("--include", action="append", default=[], help="glob; repeatable")
    ap.add_argument("--exclude", action="append", default=[], help="glob; repeatable")
    ap.add_argument("--type", default="model", choices=["model", "dataset"])
    ap.add_argument("--dry-run", action="store_true", help="list what would be pulled and stop")
    a = ap.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        log(f"hf-pull: {a.repo!r} is not a repo id (expected org/name)")
        return 2

    tok = token()
    try:
        sha = commit_sha(a.type, a.repo, a.revision, tok)
        files = list_tree(a.type, a.repo, a.revision, tok)
    except urllib.error.HTTPError as e:
        log(f"hf-pull: {e.code} from the Hub for {a.repo} ({'gated/private: check the token' if e.code in (401, 403) else e.reason})")
        return 2
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log(f"hf-pull: cannot reach the Hub for {a.repo}: {e}")
        return 2

    if a.include:
        files = [f for f in files if any(fnmatch.fnmatch(f["path"], g) for g in a.include)]
    if a.exclude:
        files = [f for f in files if not any(fnmatch.fnmatch(f["path"], g) for g in a.exclude)]

    prefix = "datasets--" if a.type == "dataset" else "models--"
    repo_dir = os.path.join(cache_root(), prefix + a.repo.replace("/", "--"))
    blobs = os.path.join(repo_dir, "blobs")
    snap = os.path.join(repo_dir, "snapshots", sha)

    todo, have = [], 0
    for f in files:
        lfs = f.get("lfs")
        etag = lfs["oid"] if lfs else f["oid"]
        size = lfs["size"] if lfs else f["size"]
        blob = os.path.join(blobs, etag)
        if os.path.exists(blob) and os.path.getsize(blob) == size:
            have += size
        else:
            todo.append((f["path"], etag, size, bool(lfs), blob))
    total = sum(t[2] for t in todo)
    bps = parse_rate(a.rate)
    log(f"hf-pull: {a.repo}@{sha[:8]}  {len(files)} files, {human(have)} already cached, "
        f"{human(total)} to pull in {len(todo)} files at {a.rate}/s (~{total / bps / 60:.1f} min)")
    if a.dry_run:
        for p, _, s, _, _ in todo:
            log(f"  {human(s):>10}  {p}")
        return 0

    os.makedirs(blobs, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    urlbase = f"{API}/{'datasets/' if a.type == 'dataset' else ''}{a.repo}/resolve/{urllib.parse.quote(a.revision, safe='')}/"
    t0 = time.time()
    for i, (path, etag, size, lfs, blob) in enumerate(todo, 1):
        log(f"[{i}/{len(todo)}] {path} ({human(size)})")
        part = blob + ".hfpull-part"
        rc = curl(urlbase + urllib.parse.quote(path), part, a.rate, tok)
        if rc != 0:
            log(f"hf-pull: curl exit {rc} on {path}; partial kept for resume at {part}")
            return 1
        if os.path.getsize(part) != size:
            log(f"hf-pull: size mismatch on {path}: {os.path.getsize(part)} != {size}; partial kept for resume")
            return 1
        got = digest(part, lfs, size)
        if got != etag:
            os.unlink(part)
            log(f"hf-pull: checksum mismatch on {path} ({got[:12]} != {etag[:12]}); partial discarded")
            return 1
        os.replace(part, blob)

    # Pointers for every file (also the ones that were already cached) + the ref.
    for f in files:
        lfs = f.get("lfs")
        etag = lfs["oid"] if lfs else f["oid"]
        ptr = os.path.join(snap, f["path"])
        os.makedirs(os.path.dirname(ptr), exist_ok=True)
        rel = os.path.relpath(os.path.join(blobs, etag), os.path.dirname(ptr))
        if os.path.islink(ptr) or os.path.exists(ptr):
            os.unlink(ptr)
        os.symlink(rel, ptr)
    refs = os.path.join(repo_dir, "refs")
    os.makedirs(refs, exist_ok=True)
    with open(os.path.join(refs, a.revision.replace("/", "%2F")), "w") as fh:
        fh.write(sha)
    log(f"hf-pull: done, {human(total)} in {(time.time() - t0) / 60:.1f} min -> {snap}")
    print(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# ABOUTME: Rate-capped Hugging Face repo downloader (bounded concurrency, aggregate rate
# ABOUTME: split across workers) that fills the standard hub cache for from_pretrained().
"""
hf-pull REPO_ID [--rate 50M] [--jobs 8] [--revision main] [--include GLOB]... [--exclude GLOB]...
                [--type model|dataset] [--dry-run] [--now]
hf-pull --window          # is the line asleep right now?

Why this exists: `hf download` opens eight connections and pulls at whatever the
line gives. On a shared residential link that starves the gateway for everyone in
the house. This tool pulls files through `curl --limit-rate`, verifies each blob
(sha256 for LFS, git-sha1 otherwise), and writes the official cache layout
(blobs/, snapshots/<commit>/, refs/<revision>) — the result is indistinguishable
from an `hf download` for huggingface_hub, and resumable.

Up to --jobs files download at once (default 8), because one file at a time is
pathological for a repo of many small files (thousands of handshakes, serialized).
--rate is always the AGGREGATE ceiling across every worker combined, never a
per-worker one: it is split N ways before it ever reaches curl.

A pull over NIGHT_GB waits for the line's night window. THE LINE'S CLOCK IS NOT
THIS MACHINE'S CLOCK -- set $HF_PULL_LINE_TZ to the IANA zone where the line
physically is, and never reason about the offset yourself. `--window` prints the
answer; `--now` overrides the gate when the human said now.

Stdlib + curl only. Token: $HF_TOKEN, else ~/.cache/huggingface/token.
"""
import argparse
import concurrent.futures
import datetime
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zoneinfo

API = "https://huggingface.co"

# The line belongs to a household. A pull bigger than NIGHT_GB waits until the people
# who share it are asleep -- measured in the LINE's timezone, which is deliberately not
# this machine's. No zone is hardcoded: this tool is public, and a zone names a place.
CONFIG = os.path.expanduser("~/.config/hf-pull/env")
FALLBACKS = {"HF_PULL_RATE": "50M", "HF_PULL_NIGHT_GB": "40", "HF_PULL_LINE_TZ": "", "HF_PULL_JOBS": "8"}
WINDOW = (1, 7)  # [01:00, 07:00) local to the line

# --limit-rate caps ONE curl transfer. Run N of them at the same cap and aggregate
# bandwidth becomes N times the configured ceiling -- exactly the 254 GB / 73-outage
# incident this tool exists to prevent (see the module docstring). So the configured
# rate is an AGGREGATE ceiling, split across workers, never a per-worker one.
MIN_WORKER_RATE_BPS = 1024 * 1024  # 1 MB/s: below this, shrink the worker count instead


def config_file(path=CONFIG):
    """KEY=VALUE lines. A file, not just $ENV, because the scheduled systemd unit this
    tool tells you to create never sees your shell profile."""
    out = {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return out


def setting(key, cfg=None):
    cfg = config_file() if cfg is None else cfg
    return (os.environ.get(key) or cfg.get(key) or FALLBACKS[key]).strip()


DEFAULT_RATE = setting("HF_PULL_RATE")
NIGHT_GB = float(setting("HF_PULL_NIGHT_GB"))
LINE_TZ = setting("HF_PULL_LINE_TZ")
DEFAULT_JOBS = int(setting("HF_PULL_JOBS"))


_log_lock = threading.Lock()


def log(msg):
    # One print() call is effectively atomic against the GIL for strings this short,
    # but multiple workers now call this concurrently -- take the lock explicitly
    # rather than rely on that, so a [i/n] line can never come out interleaved.
    with _log_lock:
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


def worker_plan(rate_bps, jobs):
    """Split an AGGREGATE rate cap across up to `jobs` concurrent curls.

    Safety-critical invariant (asserted in tests): per_worker_bps * workers <= rate_bps,
    always -- aggregate bandwidth can never exceed the configured ceiling, however
    --jobs is set. We get this for free from integer floor division (floor(a/b)*b <= a
    for any b >= 1), so the one thing we must NOT do is impose a floor on per_worker_bps
    directly -- that would push the product back over the ceiling for a large --jobs.
    Instead, when the per-worker share would fall below MIN_WORKER_RATE_BPS (which would
    otherwise round toward 0 for a silly-large --jobs, and --limit-rate 0 means
    UNLIMITED to curl -- the exact failure mode this tool exists to prevent), we shrink
    the number of concurrent workers instead, and keep dividing the real aggregate.
    """
    jobs = max(1, jobs)
    rate_bps = max(1, int(rate_bps))
    workers = min(jobs, max(1, rate_bps // MIN_WORKER_RATE_BPS))
    per_worker_bps = max(1, rate_bps // workers)
    return workers, per_worker_bps


def fmt_rate(bps):
    """bytes/s -> a compact curl-style suffix, for the startup line only. curl itself is
    always given the raw integer byte count from worker_plan(), never this rounded string."""
    for unit, div in (("G", 1024**3), ("M", 1024**2), ("k", 1024)):
        if bps >= div:
            return f"{bps // div}{unit}"
    return str(bps)


def line_clock():
    """(now_at_the_line, zone_name) or (None, None) when no line zone is configured."""
    if not LINE_TZ:
        return None, None
    try:
        return datetime.datetime.now(zoneinfo.ZoneInfo(LINE_TZ)), LINE_TZ
    except zoneinfo.ZoneInfoNotFoundError:
        log(f"hf-pull: $HF_PULL_LINE_TZ={LINE_TZ!r} is not an IANA zone; treating the line as unconfigured")
        return None, None


def window_open(now):
    return WINDOW[0] <= now.hour < WINDOW[1]


def next_open(now):
    """When the window next opens, computed on the line's calendar (DST-safe)."""
    day = now.date() if now.hour < WINDOW[0] else now.date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time(WINDOW[0]), tzinfo=now.tzinfo)


def window_report():
    """Human-readable state of the line's night window. Returns (text, is_open)."""
    now, zone = line_clock()
    if now is None:
        return ("hf-pull: $HF_PULL_LINE_TZ is not set, so I cannot tell whether the line "
                "is asleep.\n"
                "         Set it to the IANA zone where the LINE physically is -- not "
                "this machine's\n"
                "         zone, which is deliberately different.", None)
    here = datetime.datetime.now().astimezone()
    a, b = f"at the line ({zone}):", "this machine:"
    w = max(len(a), len(b))
    lines = [f"  {a:<{w}}  {now:%a %Y-%m-%d %H:%M %Z}",
             f"  {b:<{w}}  {here:%a %Y-%m-%d %H:%M %Z}"]
    if window_open(now):
        shut = datetime.datetime.combine(now.date(), datetime.time(WINDOW[1]), tzinfo=now.tzinfo)
        lines.append(f"  window {WINDOW[0]:02d}:00-{WINDOW[1]:02d}:00 is OPEN, closes in {fmt_delta(shut - now)}")
        return ("\n".join(lines), True)
    nxt = next_open(now)
    lines.append(f"  window {WINDOW[0]:02d}:00-{WINDOW[1]:02d}:00 is CLOSED, opens in {fmt_delta(nxt - now)} "
                 f"({nxt:%a %H:%M %Z})")
    return ("\n".join(lines), False)


def fmt_delta(d):
    m = int(d.total_seconds() // 60)
    return f"{m // 60} h {m % 60:02d} m"


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


def curl_cmd(cfgpath, rate, out):
    """The curl argv, split out from curl() so --limit-rate placement is unit-testable
    without spawning a real curl or touching the network. `rate` is a curl --limit-rate
    value -- for a worker download this is the raw per-worker byte count computed by
    worker_plan(), passed as a plain string (curl reads a bare integer as bytes/s)."""
    return ["curl", "-K", cfgpath, "-fSL", "--limit-rate", str(rate), "-C", "-",
            "--retry", "5", "--retry-delay", "5", "--retry-all-errors",
            "-o", out, "-#" if sys.stderr.isatty() else "--no-progress-meter"]


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
        r = subprocess.run(curl_cmd(cfg.name, rate, out))
        return r.returncode
    finally:
        os.unlink(cfg.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--rate", default=DEFAULT_RATE,
                    help=f"AGGREGATE curl --limit-rate across all jobs combined (default {DEFAULT_RATE})")
    ap.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                    help=f"files to pull concurrently (default {DEFAULT_JOBS}); --rate is split across them")
    ap.add_argument("--window", action="store_true",
                    help="print whether the line's night window is open, and exit")
    ap.add_argument("--now", action="store_true",
                    help=f"pull over {NIGHT_GB:g} GB outside the window anyway (the human said now)")
    ap.add_argument("--revision", default="main")
    ap.add_argument("--include", action="append", default=[], help="glob; repeatable")
    ap.add_argument("--exclude", action="append", default=[], help="glob; repeatable")
    ap.add_argument("--type", default="model", choices=["model", "dataset"])
    ap.add_argument("--dry-run", action="store_true", help="list what would be pulled and stop")
    a = ap.parse_args()
    if a.window:
        log(window_report()[0])
        return 0
    if a.repo is None:
        ap.error("a repo id is required (or use --window)")
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

    # Group by blob, not by file: two files with byte-identical content share one git/LFS
    # hash and therefore one blob path. Concurrent downloads must fetch that blob exactly
    # ONCE -- two workers racing to write the same .hfpull-part path corrupts the transfer
    # and one of them loses a rename race (found by the real-repo verification run, not a
    # thought experiment). Every path sharing a blob gets its pointer once the one
    # download completes.
    groups = {}
    for f in files:
        lfs = f.get("lfs")
        etag = lfs["oid"] if lfs else f["oid"]
        size = lfs["size"] if lfs else f["size"]
        blob = os.path.join(blobs, etag)
        g = groups.setdefault(etag, {"paths": [], "size": size, "lfs": bool(lfs), "blob": blob})
        g["paths"].append(f["path"])

    todo, cached, have = [], [], 0
    for etag, g in groups.items():
        if os.path.exists(g["blob"]) and os.path.getsize(g["blob"]) == g["size"]:
            have += g["size"]
            cached.append((g["paths"], etag))
        else:
            todo.append((g["paths"], etag, g["size"], g["lfs"], g["blob"]))
    total = sum(t[2] for t in todo)
    todo_files = sum(len(t[0]) for t in todo)
    bps = parse_rate(a.rate)
    workers, per_worker_bps = worker_plan(bps, a.jobs)
    log(f"hf-pull: {a.repo}@{sha[:8]}  {len(files)} files, {human(have)} already cached, "
        f"{human(total)} to pull in {todo_files} files at {a.rate}/s aggregate "
        f"({workers} jobs x {fmt_rate(per_worker_bps)}) (~{total / bps / 60:.1f} min)")
    if workers < a.jobs:
        log(f"hf-pull: --jobs {a.jobs} would split {a.rate}/s below a sane per-worker floor; "
            f"using {workers} concurrent jobs instead")
    over = total > NIGHT_GB * 1024**3
    if a.dry_run:
        for paths, _, s, _, _ in todo:
            for p in paths:
                log(f"  {human(s):>10}  {p}")
        if over:
            log(f"hf-pull: {human(total)} is over the {NIGHT_GB:g} GB night-window threshold.")
            log(window_report()[0])
        return 0

    if over and not a.now:
        report, is_open = window_report()
        if is_open is None:
            log(f"hf-pull: {human(total)} is over the {NIGHT_GB:g} GB night-window threshold.")
            log(report)
            log("         Pulling anyway, blind. Set $HF_PULL_LINE_TZ so this can be checked.")
        elif not is_open:
            log(f"hf-pull: refusing -- {human(total)} is over the {NIGHT_GB:g} GB night-window "
                "threshold and the line is awake.")
            log(report)
            argv = [a.repo]
            if a.type != "model":
                argv = ["--type", a.type] + argv
            if a.revision != "main":
                argv = ["--revision", a.revision] + argv
            for g in a.include:
                argv = ["--include", g] + argv
            for g in a.exclude:
                argv = ["--exclude", g] + argv
            log(f"  schedule it:  systemd-run --user --on-calendar='*-*-* "
                f"{WINDOW[0]:02d}:00 {LINE_TZ}' hf-pull {shlex.join(argv)}")
            log("  or pass --now if the human said now.")
            return 3

    os.makedirs(blobs, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    # The ref and the already-cached pointers cost nothing and make the snapshot dir
    # resolvable by revision (huggingface_hub reads refs/<revision> for the commit sha)
    # from the very first moment, rather than only once every file has landed.
    refs = os.path.join(repo_dir, "refs")
    os.makedirs(refs, exist_ok=True)
    with open(os.path.join(refs, a.revision.replace("/", "%2F")), "w") as fh:
        fh.write(sha)

    def make_pointer(path, etag):
        """Symlink one file's snapshot entry to its blob. Called up front for files
        that were already cached, and again for each download the instant ITS blob
        completes -- so an interrupted pull leaves a partial but usable tree instead
        of bare blobs with no readable file for 92 minutes, as before."""
        ptr = os.path.join(snap, path)
        os.makedirs(os.path.dirname(ptr), exist_ok=True)
        rel = os.path.relpath(os.path.join(blobs, etag), os.path.dirname(ptr))
        if os.path.islink(ptr) or os.path.exists(ptr):
            os.unlink(ptr)
        os.symlink(rel, ptr)

    for paths, etag in cached:
        for path in paths:
            make_pointer(path, etag)

    urlbase = f"{API}/{'datasets/' if a.type == 'dataset' else ''}{a.repo}/resolve/{urllib.parse.quote(a.revision, safe='')}/"
    t0 = time.time()

    def pull_one(i, paths, etag, size, lfs, blob):
        # One blob, possibly several identical-content paths -- fetch it once, using
        # the first path as the source URL, then point every path at the result.
        label = paths[0] if len(paths) == 1 else f"{paths[0]} (+{len(paths) - 1} identical)"
        log(f"[{i}/{len(todo)}] {label} ({human(size)})")
        part = blob + ".hfpull-part"
        rc = curl(urlbase + urllib.parse.quote(paths[0]), part, per_worker_bps, tok)
        if rc != 0:
            return f"curl exit {rc} on {label}; partial kept for resume at {part}"
        if os.path.getsize(part) != size:
            return f"size mismatch on {label}: {os.path.getsize(part)} != {size}; partial kept for resume"
        got = digest(part, lfs, size)
        if got != etag:
            os.unlink(part)
            return f"checksum mismatch on {label} ({got[:12]} != {etag[:12]}); partial discarded"
        os.replace(part, blob)
        for path in paths:
            make_pointer(path, etag)
        return None

    # Bounded pipeline of `workers` downloads in flight. Decision on failure handling:
    # on the first failure we stop SCHEDULING new downloads but let whatever is already
    # running finish and be verified -- killing a curl mid-transfer buys nothing (the
    # bytes already pulled are still within the aggregate cap either way, and the file
    # resumes cleanly next run regardless), while a fault that recurs on every file
    # (bad token, deleted revision) now costs at most `workers` extra files instead of
    # draining the whole queue as it would if we let every already-submitted task run.
    pending = iter(enumerate(todo, 1))
    failed = []

    def submit(ex):
        if failed:
            return None
        try:
            i, item = next(pending)
        except StopIteration:
            return None
        return ex.submit(pull_one, i, *item)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        inflight = {f for f in (submit(ex) for _ in range(workers)) if f}
        while inflight:
            done, inflight = concurrent.futures.wait(inflight, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                err = f.result()
                if err:
                    failed.append(err)
            if not failed:
                inflight |= {f for f in (submit(ex) for _ in done) if f}

    if failed:
        for err in failed:
            log(f"hf-pull: {err}")
        return 1

    log(f"hf-pull: done, {human(total)} in {(time.time() - t0) / 60:.1f} min -> {snap}")
    print(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ABOUTME: Before the claims verifier runs, every source in sources.md goes through the scholar_fetch chain.
# ABOUTME: Writes fetched/<n>.txt for what resolved and fetched.json (served_by, kind, tried) for every source.
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable

from scholar_fetch.chain import Fetched, resolve
from scholar_fetch.http import Client
from scholar_fetch.ids import Ids

FETCHED_DIR = "fetched"
FETCHED_JSON = "fetched.json"
SOURCE_LINE = re.compile(r"^\s*(\d+)\.\s.*?(https?://[^\s)>\]]+)", re.M)


def parse_sources(md: str) -> list[tuple[int, str]]:
    return [(int(n), url) for n, url in SOURCE_LINE.findall(md)]


def default_resolver() -> Callable[[str], Fetched]:
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "scholar-fetch"
    client = Client(cache)
    return lambda url: resolve(url, client)


def _guarded(resolver: Callable[[str], Fetched], url: str) -> Fetched:
    """One source can never abort the run: the chain catches what it expects, this catches the rest
    (a parser bug, a full disk under the text cache) and records it as a failed pre-fetch step."""
    try:
        return resolver(url)
    except Exception as exc:
        return Fetched(url, None, None, None, Ids(),
                       [{"step": "prefetch", "target": url, "outcome": f"error: {type(exc).__name__}"}])


def prefetch(out: Path, sources_md: str, resolver: Callable[[str], Fetched],
             log: Callable[[str], None] | None = None) -> dict[int, dict]:
    folder = out / FETCHED_DIR
    folder.mkdir(exist_ok=True)
    for stale in folder.glob("*.txt"):
        stale.unlink()  # our own regenerated output inside the run dir, rewritten below
    index: dict[int, dict] = {}
    seen: dict[str, Fetched] = {}  # a URL cited twice is resolved once per run
    sources = parse_sources(sources_md)
    for i, (n, url) in enumerate(sources, 1):
        if url not in seen:
            seen[url] = _guarded(resolver, url)
        f = seen[url]
        if f.text is not None:
            (folder / f"{n}.txt").write_text(f.text, encoding="utf-8")
        index[n] = f.to_dict()
        if log:
            log(f"  [{i}/{len(sources)}] {f.served_by or 'not fetched'} {url}")
    target, tmp = out / FETCHED_JSON, out / (FETCHED_JSON + ".tmp")
    tmp.write_text(json.dumps({str(k): v for k, v in index.items()}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)  # atomic: a crash mid-write never leaves a half fetched.json beside a verification
    return index

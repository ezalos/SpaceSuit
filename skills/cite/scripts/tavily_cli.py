#!/usr/bin/env python3
# ABOUTME: Tavily REST CLI (search + extract) for the cite pipeline — usable in subagents and headless runs.
# ABOUTME: Key from $TAVILY_API_KEY or ~/.claude/secrets/tavily_api_key. --dry-run prints the payload only.

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
DEFAULT_KEY_FILE = Path.home() / ".claude" / "secrets" / "tavily_api_key"


def _resolve_ref(ref: str) -> str:
    import os
    env = {**os.environ, "PROTON_PASS_AGENT_REASON": os.environ.get("PROTON_PASS_AGENT_REASON", "cite:tavily")}
    out = subprocess.run(
        ["proton-agent", "item", "view", ref],
        capture_output=True, text=True, check=True, env=env,
    )
    return out.stdout.strip()


def resolve_key(key_file=DEFAULT_KEY_FILE):
    import os
    env = os.environ.get("TAVILY_API_KEY")
    if env:
        return env.strip()
    kf = Path(key_file)
    if kf.exists():
        raw = kf.read_text().strip()
        return _resolve_ref(raw) if raw.startswith("pass://") else raw
    raise RuntimeError(
        "No Tavily API key. Set $TAVILY_API_KEY or write it to "
        f"{DEFAULT_KEY_FILE} (chmod 600). Get one at https://app.tavily.com/."
    )


def build_search_payload(query, max_results=5, include_domains=None, days=None, search_depth="advanced"):
    body = {"query": query, "max_results": max_results, "search_depth": search_depth}
    if include_domains:
        body["include_domains"] = include_domains
    if days is not None:
        body["days"] = days
    return body


def build_extract_payload(url):
    return {"urls": [url], "extract_depth": "advanced", "format": "markdown"}


def _post(url, body, key):
    req = Request(url, data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type": "application/json",
                           "Authorization": f"Bearer {key}"})
    with urlopen(req, timeout=60) as resp:    # nosec - calling the Tavily API
        return json.loads(resp.read())


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--max-results", type=int, default=5)
    s.add_argument("--domain", action="append", dest="domains")
    s.add_argument("--days", type=int, default=None)
    s.add_argument("--dry-run", action="store_true")

    e = sub.add_parser("extract")
    e.add_argument("url")
    e.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.cmd == "search":
        body, endpoint = build_search_payload(
            args.query, args.max_results, args.domains, args.days), SEARCH_URL
    else:
        body, endpoint = build_extract_payload(args.url), EXTRACT_URL

    if args.dry_run:
        print(json.dumps({"endpoint": endpoint, "payload": body}, indent=2))
        sys.exit(0)

    key = resolve_key()
    print(json.dumps(_post(endpoint, body, key)))
    sys.exit(0)


if __name__ == "__main__":
    main()

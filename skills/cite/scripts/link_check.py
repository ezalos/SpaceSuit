#!/usr/bin/env python3
# ABOUTME: Check an existing citation URL's health: ok / dead / redirect-suspect. CLI prints JSON.
# ABOUTME: verdict() is pure (testable); check() does the urllib request and feeds verdict().

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _host(url):
    if not url:
        return None
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def verdict(url, final_url=None, code=None, error=None):
    if error or code is None:
        return {"status": "dead", "http_code": code, "final_url": final_url, "error": error}
    if code >= 400:
        return {"status": "dead", "http_code": code, "final_url": final_url, "error": None}
    if final_url and _host(final_url) != _host(url):
        return {"status": "redirect-suspect", "http_code": code, "final_url": final_url, "error": None}
    return {"status": "ok", "http_code": code, "final_url": final_url, "error": None}


def check(url, timeout=20):
    req = Request(url, method="GET", headers={"User-Agent": "cite-link-check/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:    # nosec - checking a user-cited URL
            return verdict(url, final_url=resp.geturl(), code=resp.status)
    except HTTPError as e:
        return verdict(url, final_url=url, code=e.code)
    except (URLError, TimeoutError, ValueError) as e:
        return verdict(url, error=str(e))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--timeout", type=int, default=20)
    args = p.parse_args()
    print(json.dumps(check(args.url, timeout=args.timeout)))
    sys.exit(0)


if __name__ == "__main__":
    main()

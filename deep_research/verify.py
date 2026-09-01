# ABOUTME: Independently verifies a run's citations by fetching each page and grepping its quote.
# ABOUTME: Turns collect's "verified" from the research agent's own attestation into evidence.
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from html.parser import HTMLParser
from typing import Callable, Iterable

USER_AGENT = "deep-research-verifier/1.0 (+citation check)"
TIMEOUT_SECONDS = 20
MAX_BYTES = 5_000_000

# Content that is present in the markup but is not readable page text. A quote must
# never be satisfied by a string that only exists inside a script or a stylesheet.
_NON_TEXT_TAGS = {"script", "style", "noscript", "template"}

# Publishers routinely render typographic punctuation while an agent transcribes the
# ASCII form it read. Folding both sides prevents false CONTRADICTED verdicts on
# quotes that are, to a reader, character-for-character identical.
_PUNCTUATION_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "‒": "-", "―": "-", "−": "-",
    "…": "...", " ": " ", " ": " ", " ": " ", "​": "",
}


class VerdictKind(str, Enum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class Verdict:
    n: int
    url: str
    quote: str
    kind: VerdictKind
    detail: str


class _TextExtractor(HTMLParser):
    """Collects readable text, skipping script/style so their contents cannot be quoted."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in _NON_TEXT_TAGS:
            self._suppress += 1

    def handle_endtag(self, tag):
        if tag in _NON_TEXT_TAGS and self._suppress:
            self._suppress -= 1

    def handle_data(self, data):
        if not self._suppress:
            self.chunks.append(data)


def normalize(text: str) -> str:
    """Reduce markup or a quote to comparable plain text.

    Strips tags and non-text elements, decodes entities, folds typographic
    punctuation to ASCII, and collapses every whitespace run to a single space.
    Both the page and the quote go through this, so the comparison is between two
    strings normalised the same way.
    """
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
        plain = "".join(parser.chunks)
    except Exception:
        # A malformed document must degrade to a usable comparison, not an exception.
        plain = re.sub(r"<[^>]*>", " ", text)

    plain = html.unescape(plain)
    for src, dst in _PUNCTUATION_FOLD.items():
        plain = plain.replace(src, dst)
    return re.sub(r"\s+", " ", plain).strip()


def longest_common_span(needle: str, haystack: str) -> str:
    """The longest contiguous run of `needle` that appears in `haystack`.

    A failed match is far more useful when it says HOW it failed. "118 of 122
    characters matched" is a transcription slip worth a human glance; "6 of 122" is a
    quote that was not copied from this page at all. Anchored binary search over
    lengths, so a short quote against a large page stays cheap.
    """
    best = ""
    for start in range(len(needle)):
        # No anchor beyond this point can beat what we already have.
        if len(needle) - start <= len(best):
            break
        low, high = len(best) + 1, len(needle) - start
        while low <= high:
            mid = (low + high) // 2
            if needle[start : start + mid] in haystack:
                best = needle[start : start + mid]
                low = mid + 1
            else:
                high = mid - 1
    return best


def _looks_like_a_bare_domain(url: str) -> bool:
    """True when the URL names a site rather than the exact page carrying the claim."""
    without_scheme = re.sub(r"^[a-z]+://", "", url.strip(), flags=re.IGNORECASE)
    path = without_scheme.partition("/")[2].split("?")[0].split("#")[0]
    return path.strip("/") == ""


def fetch(url: str, timeout: int | None = None) -> tuple[int, str]:
    """Fetch a URL, returning (status, body). Raises OSError when unreachable."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout or TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        # An HTTP error still carries a status, which is more useful than "unreachable".
        return exc.code, ""


def verify_source(
    source: dict,
    fetcher: Callable[..., tuple[int, str]] = fetch,
    timeout: int | None = None,
) -> Verdict:
    n = source.get("n", 0)
    url = str(source.get("url") or "").strip()
    quote = str(source.get("quote") or "").strip()

    if not url:
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, "no url given")
    if not quote:
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, "no verbatim quote given")
    if _looks_like_a_bare_domain(url):
        return Verdict(
            n, url, quote, VerdictKind.UNVERIFIABLE,
            "bare domain, not the exact page carrying the claim",
        )

    try:
        status, body = fetcher(url, timeout=timeout)
    except Exception as exc:  # network stack raises a wide family; none should crash us
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, f"fetch failed: {exc}")

    if status != 200:
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, f"HTTP {status}")

    page = normalize(body)
    if not page:
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, "page had no readable text")

    needle = normalize(quote)
    if needle in page:
        return Verdict(n, url, quote, VerdictKind.VERIFIED, "quote found on the page")
    if needle.casefold() in page.casefold():
        return Verdict(
            n, url, quote, VerdictKind.VERIFIED, "quote found, differing only in case"
        )
    span = longest_common_span(needle, page)
    detail = f"matched {len(span)} of {len(needle)} characters"
    if span:
        detail += f'; longest span actually on the page: "{span[:90]}"'
    return Verdict(n, url, quote, VerdictKind.CONTRADICTED, detail)


def verify_sources(
    sources: Iterable[dict],
    fetcher: Callable[..., tuple[int, str]] = fetch,
    timeout: int | None = None,
) -> list[Verdict]:
    return [verify_source(s, fetcher=fetcher, timeout=timeout) for s in sources]

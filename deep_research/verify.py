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
from typing import Callable, Iterable, NamedTuple

USER_AGENT = "deep-research-verifier/1.0 (+citation check)"
TIMEOUT_SECONDS = 20
MAX_BYTES = 5_000_000

# Content that is present in the markup but is not readable page text. A quote must
# never be satisfied by a string that only exists inside a script or a stylesheet.
_NON_TEXT_TAGS = {"script", "style", "noscript", "template"}

# A <sup> holding a bracketed number is a footnote marker ("Lisbon[1] is the capital"),
# which no reader treats as prose and no agent transcribes. Any OTHER <sup> is real
# content: dropping it wholesale would turn "10<sup>6</sup> square metres" into
# "10 square metres" and VERIFY a quote that is wrong by a factor of a million.
_FOOTNOTE_MARKER_RE = re.compile(r"^\s*\[\s*\d+\s*\]\s*$")

# Content types we can meaningfully read as text. A PDF decoded as UTF-8 and shoved
# through an HTML parser yields garbage that no quote matches, which would come back as
# the accusatory CONTRADICTED rather than the honest "could not check".
_READABLE_TYPES = ("text/html", "application/xhtml", "text/plain", "application/xml", "text/xml")

# Publishers routinely render typographic punctuation while an agent transcribes the
# ASCII form it read. Folding both sides prevents false CONTRADICTED verdicts on
# quotes that are, to a reader, character-for-character identical.
_PUNCTUATION_FOLD = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u2009": " ", "\u202f": " ",
}

# Invisible characters publishers inject for typesetting. A reader never sees them and
# an agent transcribing the page never types them, so leaving them in produces false
# CONTRADICTED verdicts. Wikipedia really does ship soft hyphens mid-word ("pro\xadduct").
_INVISIBLE = dict.fromkeys("\u00ad\u200b\u200c\u200d\u2060\ufeff", "")

# Tags that render as a line or block break. Their text must not be glued to the next
# element's, or a quote spanning two paragraphs would falsely fail to match.
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "tbody", "td", "th", "thead", "tr", "ul",
}

# A quote shorter than this cannot be evidence: "the" appears on nearly every page, so
# confirming it would be a false VERIFIED. Long enough to admit a real short fact like
# "Capital: Lisbon", short enough not to reject a legitimately terse citation.
MIN_QUOTE_CHARS = 12


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
        self._in_sup = 0

    def handle_starttag(self, tag, attrs):
        if tag in _NON_TEXT_TAGS:
            self._suppress += 1
        elif tag == "sup":
            self._in_sup += 1
        elif tag in _BLOCK_TAGS:
            # A block boundary renders as whitespace. Without this, "<p>foo</p><p>bar</p>"
            # reads as "foobar" and a quote spanning two paragraphs falsely fails.
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in _NON_TEXT_TAGS and self._suppress:
            self._suppress -= 1
        elif tag == "sup" and self._in_sup:
            self._in_sup -= 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_data(self, data):
        if self._suppress:
            return
        if self._in_sup and _FOOTNOTE_MARKER_RE.match(data):
            return  # a citation marker, not prose
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
        # convert_charrefs=True has ALREADY decoded entities here. Calling
        # html.unescape again would decode twice, turning a page's literal "&amp;lt;"
        # into "<" and letting a quote match text the page never displayed.
        plain = "".join(parser.chunks)
    except Exception:
        # A malformed document must degrade to a usable comparison, not an exception.
        # This path did no entity decoding, so it is the one that needs unescape.
        plain = html.unescape(re.sub(r"<[^>]*>", " ", text))

    plain = plain.translate(str.maketrans(_INVISIBLE))
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


class Fetched(NamedTuple):
    status: int
    body: str
    content_type: str = ""
    truncated: bool = False


def fetch(url: str, timeout: int | None = None) -> Fetched:
    """Fetch a URL. Raises OSError when unreachable; an HTTP error returns its status."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout or TIMEOUT_SECONDS) as response:
            # Read one byte past the cap so a truncated page is detectable rather than
            # silently short, which would read as a missing quote.
            raw = response.read(MAX_BYTES + 1)
            truncated = len(raw) > MAX_BYTES
            charset = response.headers.get_content_charset() or "utf-8"
            return Fetched(
                response.status,
                raw[:MAX_BYTES].decode(charset, errors="replace"),
                (response.headers.get_content_type() or "").lower(),
                truncated,
            )
    except urllib.error.HTTPError as exc:
        # An HTTP error still carries a status, which is more useful than "unreachable".
        return Fetched(exc.code, "", "", False)


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
    if len(normalize(quote)) < MIN_QUOTE_CHARS:
        # A very short string matches almost any page, so confirming it would be
        # evidence of nothing. Refuse rather than hand back a meaningless VERIFIED.
        return Verdict(
            n, url, quote, VerdictKind.UNVERIFIABLE,
            f"quote is too short to be evidence (under {MIN_QUOTE_CHARS} characters)",
        )
    if _looks_like_a_bare_domain(url):
        return Verdict(
            n, url, quote, VerdictKind.UNVERIFIABLE,
            "bare domain, not the exact page carrying the claim",
        )

    try:
        fetched = Fetched(*fetcher(url, timeout=timeout))
    except Exception as exc:  # network stack raises a wide family; none should crash us
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, f"fetch failed: {exc}")

    if fetched.status != 200:
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, f"HTTP {fetched.status}")

    ctype = fetched.content_type
    if ctype and not any(ctype.startswith(t) for t in _READABLE_TYPES):
        # Not a text document we can read. Say so honestly instead of accusing the
        # citation of being wrong because we cannot parse a PDF.
        return Verdict(
            n, url, quote, VerdictKind.UNVERIFIABLE,
            f"cannot read content type {ctype}; check this one by hand",
        )

    page = normalize(fetched.body)
    if not page:
        return Verdict(n, url, quote, VerdictKind.UNVERIFIABLE, "page had no readable text")

    needle = normalize(quote)
    if needle in page:
        return Verdict(n, url, quote, VerdictKind.VERIFIED, "quote found on the page")
    if needle.casefold() in page.casefold():
        return Verdict(
            n, url, quote, VerdictKind.VERIFIED, "quote found, differing only in case"
        )
    if fetched.truncated:
        # The quote may well live in the part we never read, so absence is not evidence.
        return Verdict(
            n, url, quote, VerdictKind.UNVERIFIABLE,
            "page exceeded the read limit; the quote may be in the unread part",
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

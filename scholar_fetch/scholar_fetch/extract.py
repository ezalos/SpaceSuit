# ABOUTME: A fetched body to plain text: the PDF text layer via pypdf, visible HTML text via html.parser.
# ABOUTME: Too little text is a failure (a scanned PDF, a login wall), so the chain moves on.
import logging
import re
from html.parser import HTMLParser
from io import BytesIO

from pypdf import PdfReader

# Suppress pypdf warnings (check-claims is a CLI whose stderr a person reads)
logging.getLogger("pypdf").setLevel(logging.ERROR)

MIN_TEXT = 200
SKIPPED = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}
BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "td"}
BOT_WALL_TITLE = "just a moment..."  # Cloudflare's interstitial challenge page
BOT_WALL_TEXT_CAP = 1000  # "captcha" mentioned on a short page is the page itself, not an article about one
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _decode(body: bytes) -> str:
    """Decode body trying UTF-8 first, then charset from meta tag, then cp1252."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        # Look for charset in first 2048 bytes
        head = body[:2048].decode("ascii", errors="replace")
        match = re.search(r'charset\s*=\s*["\']?([^"\'\s;>]+)', head, re.IGNORECASE)
        if match:
            charset = match.group(1)
            try:
                return body.decode(charset, errors="replace")
            except LookupError:
                pass
        # Fall back to cp1252
        return body.decode("cp1252", errors="replace")


class _Visible(HTMLParser):
    def __init__(self, skipped=None):
        super().__init__()
        self.skipped = skipped if skipped is not None else SKIPPED
        self.depth, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.skipped:
            self.depth += 1
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skipped and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def _tidy(text: str) -> str:
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in text.splitlines())
    return "\n".join(ln for ln in lines if ln)


def _is_bot_wall(decoded: str, text: str) -> bool:
    """A captcha or interstitial page must never count as a success: Cloudflare's "Just a moment..."
    challenge title is always a bot wall regardless of length; a short page merely mentioning a
    captcha is also one, but a long page that discusses captchas as a topic is not."""
    m = TITLE_TAG.search(decoded)
    if m and _tidy(m.group(1)).strip().lower() == BOT_WALL_TITLE:
        return True
    return "captcha" in text.lower() and len(text) < BOT_WALL_TEXT_CAP


def is_pdf(body: bytes, content_type: str) -> bool:
    """Sniffed or declared: servers mislabel PDFs as octet-stream often enough to check the magic bytes."""
    return body[:5] == b"%PDF-" or content_type == "application/pdf"


def to_text(body: bytes, content_type: str) -> tuple[str | None, str]:
    if is_pdf(body, content_type):
        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(body)).pages)
        # pypdf is a third-party parser reading untrusted bytes: besides its own PdfReadError it raises
        # DependencyError (AES without the crypto extra), TypeError, AttributeError, recursion limits...
        # Any of them means "this PDF cannot be read here", never a reason to stop the chain.
        except Exception:
            return None, "unreadable-pdf"
    elif content_type in ("text/html", "application/xhtml+xml"):
        decoded = _decode(body)
        parser = _Visible()
        parser.feed(decoded)
        text = "".join(parser.parts)
        # If first parse with full SKIPPED set yields too little, retry with only script/style strict
        if len(_tidy(text)) < MIN_TEXT:
            parser = _Visible(skipped={"script", "style"})
            parser.feed(decoded)
            text = "".join(parser.parts)
        if _is_bot_wall(decoded, _tidy(text)):
            return None, "bot-wall"
    elif content_type.startswith("text/"):
        text = _decode(body)
    else:
        return None, f"unsupported-type {content_type or 'unknown'}"
    text = _tidy(text)
    return (text, "ok") if len(text) >= MIN_TEXT else (None, "no-text-layer")

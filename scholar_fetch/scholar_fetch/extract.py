# ABOUTME: A fetched body to plain text: the PDF text layer via pypdf, visible HTML text via html.parser.
# ABOUTME: Too little text is a failure (a scanned PDF, a login wall), so the chain moves on.
import re
from html.parser import HTMLParser
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MIN_TEXT = 200
SKIPPED = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}
BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "td"}


class _Visible(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in SKIPPED:
            self.depth += 1
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIPPED and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def _tidy(text: str) -> str:
    lines = (re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in text.splitlines())
    return "\n".join(ln for ln in lines if ln)


def to_text(body: bytes, content_type: str) -> tuple[str | None, str]:
    if body[:5] == b"%PDF-" or content_type == "application/pdf":
        try:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(body)).pages)
        except (PdfReadError, ValueError, KeyError):
            return None, "unreadable-pdf"
    elif content_type in ("text/html", "application/xhtml+xml"):
        parser = _Visible()
        parser.feed(body.decode("utf-8", errors="replace"))
        text = "".join(parser.parts)
    elif content_type.startswith("text/"):
        text = body.decode("utf-8", errors="replace")
    else:
        return None, f"unsupported-type {content_type or 'unknown'}"
    text = _tidy(text)
    return (text, "ok") if len(text) >= MIN_TEXT else (None, "no-text-layer")

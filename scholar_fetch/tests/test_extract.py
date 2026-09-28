# ABOUTME: Tests for turning a fetched body into text: PDF text layer, visible HTML text, the refusals.
# ABOUTME: PDFs are real ones built by tests/pdfs.py; nothing is mocked.
from pdfs import make_pdf

from scholar_fetch.extract import to_text

LONG = "Attention is all you need. " * 20


def test_pdf_text_layer():
    text, outcome = to_text(make_pdf(LONG), "application/pdf")
    assert outcome == "ok" and "Attention is all you need" in text


def test_pdf_is_sniffed_when_the_server_mislabels_it():
    assert to_text(make_pdf(LONG), "application/octet-stream")[1] == "ok"


def test_pdf_without_enough_text_is_no_text_layer():
    assert to_text(make_pdf("x"), "application/pdf") == (None, "no-text-layer")


def test_garbage_pdf_is_unreadable():
    assert to_text(b"%PDF-1.7 garbage", "application/pdf") == (None, "unreadable-pdf")


def test_html_keeps_visible_text_and_drops_scripts_styles_nav():
    html = (b"<html><head><style>p{}</style><script>var x=1</script></head><body><nav>Home Menu</nav>"
            b"<p>" + LONG.encode() + b"</p><footer>cookie banner</footer></body></html>")
    text, outcome = to_text(html, "text/html")
    assert outcome == "ok"
    assert "Attention is all you need" in text
    for gone in ("var x", "p{}", "Home Menu", "cookie banner"):
        assert gone not in text


def test_plain_text_passes_through():
    assert to_text(LONG.encode(), "text/plain") == (LONG.strip(), "ok")


def test_binary_is_unsupported():
    assert to_text(b"\x89PNG....", "image/png") == (None, "unsupported-type image/png")

# ABOUTME: Tests input-format routing (md native, pdf->pdftotext, html/docx->pandoc, url->fetch+pandoc).
import convert_input as ci

def test_markdown_is_native():
    assert ci.route("deck.md") == "native"

def test_pdf_routes_to_pdftotext():
    assert ci.route("report.pdf") == "pdftotext"

def test_html_and_docx_route_to_pandoc():
    assert ci.route("page.html") == "pandoc"
    assert ci.route("page.htm") == "pandoc"
    assert ci.route("doc.docx") == "pandoc"

def test_url_routes_to_url():
    assert ci.route("https://example.com/post") == "url"
    assert ci.route("http://example.com/post") == "url"

def test_unknown_extension_raises():
    import pytest
    with pytest.raises(ValueError):
        ci.route("archive.zip")

# ABOUTME: Tests for turning a fetched body into text: PDF text layer, visible HTML text, the refusals.
# ABOUTME: PDFs are real ones built by tests/pdfs.py; nothing is mocked.
from pdfs import make_aes_pdf, make_pdf

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


def test_latin1_html_with_charset_decodes_accents():
    # Latin-1 encoded HTML with meta charset and accented characters
    latin1_body = b'<html><head><meta charset="iso-8859-1"></head><body><p>' + ("éèà " * 60).encode("iso-8859-1") + b'</p></body></html>'
    text, outcome = to_text(latin1_body, "text/html")
    assert outcome == "ok"
    assert "éèà" in text
    assert "�" not in text  # No replacement character


def test_latin1_html_without_charset_falls_back_to_cp1252():
    # Latin-1 encoded HTML without charset declaration
    latin1_body = b'<html><body><p>' + ("éèà " * 60).encode("iso-8859-1") + b'</p></body></html>'
    text, outcome = to_text(latin1_body, "text/html")
    assert outcome == "ok"
    assert "éèà" in text
    assert "�" not in text


def test_unclosed_nav_tag_doesnt_swallow_rest():
    # Unclosed <nav> should not prevent parsing the rest of the document
    html = b"<html><body><nav>Menu" + LONG.encode() + b"</body></html>"
    text, outcome = to_text(html, "text/html")
    assert outcome == "ok"
    assert "Attention is all you need" in text


def test_unclosed_script_tag_behaves_like_browser():
    # Unclosed <script> swallows the rest, matching browser behavior
    html = b"<html><body><script>var x=1" + LONG.encode() + b"</body></html>"
    text, outcome = to_text(html, "text/html")
    assert (text, outcome) == (None, "no-text-layer")


def test_aes_pdf_with_only_an_owner_password_extracts():
    text, outcome = to_text(make_aes_pdf(LONG), "application/pdf")
    assert outcome == "ok" and "Attention is all you need" in text


def test_aes_pdf_behind_a_user_password_is_unreadable_not_an_exception():
    assert to_text(make_aes_pdf(LONG, user_password="secret"), "application/pdf") == (None, "unreadable-pdf")


def test_any_parser_exception_is_unreadable_not_a_crash():
    # pypdf raises outside its own error classes on odd input: DependencyError for AES without the
    # crypto extra (found in the wild), a TypeError for a /Font entry that is a bare number.
    assert to_text(make_pdf("x", font_is_a_number=True), "application/pdf") == (None, "unreadable-pdf")


def test_cloudflare_challenge_title_is_a_bot_wall_regardless_of_length():
    html = (b"<html><head><title>Just a moment...</title></head><body><p>"
            + b"Checking your browser before accessing. " * 40 + b"</p></body></html>")
    assert to_text(html, "text/html") == (None, "bot-wall")


def test_challenge_title_matches_case_insensitively_and_with_attributes():
    html = b'<html><head><title class="x">JUST A MOMENT...</title></head><body><p>' + LONG.encode() + b"</p></body></html>"
    assert to_text(html, "text/html") == (None, "bot-wall")


def test_a_short_page_mentioning_a_captcha_is_a_bot_wall():
    body = "Please prove you are not a robot by solving this captcha challenge. " * 4  # ~280 chars, >= MIN_TEXT
    html = b"<html><body><p>" + body.encode() + b"</p></body></html>"
    assert to_text(html, "text/html") == (None, "bot-wall")


def test_captcha_mentioned_in_a_long_legitimate_page_is_not_a_bot_wall():
    body = "This article discusses captcha systems as an anti-bot measure in some detail. " * 20  # >1000 chars
    html = b"<html><body><p>" + body.encode() + b"</p></body></html>"
    text, outcome = to_text(html, "text/html")
    assert outcome == "ok" and "captcha" in text.lower()

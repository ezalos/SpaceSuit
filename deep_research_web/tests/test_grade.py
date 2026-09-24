# ABOUTME: Tests for citation grading against stubbed pages.
# ABOUTME: QUOTED reuses SpaceSuit verification; LIVE only proves the URL resolves.
from conftest import fetcher_stub

from deep_research_web.grade import Grade, count_grades, grade_citations
from deep_research_web.report import Citation

PAGE = "<html><body><p>The price is five dollars a month, billed yearly.</p></body></html>"


def _cits(*urls_and_spans):
    cits, content, pos = [], "", 0
    for n, (url, span) in enumerate(urls_and_spans, start=1):
        cits.append(Citation(n, url, "", "web_fetch", pos, pos + len(span)))
        content += span
        pos += len(span)
    return cits, content


def test_quoted_when_the_quoted_string_is_on_the_page():
    cits, content = _cits(("https://x.test/a", 'Alpha says "price is five dollars a month" today.'))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.QUOTED and g.quote == "price is five dollars a month"


def test_misquoted_when_the_page_is_live_but_the_quote_is_absent():
    cits, content = _cits(("https://x.test/a", 'Alpha says "price is nine dollars a month" today.'))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.MISQUOTED and "matched" in g.detail


def test_live_when_nothing_verbatim_to_match():
    cits, content = _cits(("https://x.test/a", "Alpha is cheap."))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.LIVE and g.quote is None


def test_dead_on_http_error_and_bare_domain():
    cits, content = _cits(("https://x.test/a", "Alpha is cheap."), ("https://x.test/", "Beta is fast."))
    a, b = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (404, "")}))
    assert a.grade is Grade.DEAD and a.detail == "HTTP 404"
    assert b.grade is Grade.DEAD and "bare domain" in b.detail


def test_unverifiable_on_network_failure_and_unreadable_content():
    cits, content = _cits(("https://x.test/gone", "Alpha is cheap."), ("https://x.test/empty", "Beta is fast."))
    a, b = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/empty": (200, "<html></html>")}))
    assert a.grade is Grade.UNVERIFIABLE and "fetch failed" in a.detail
    assert b.grade is Grade.UNVERIFIABLE and "no readable text" in b.detail


def test_one_grade_per_number_prefers_a_span_with_a_quote():
    cits = [Citation(1, "https://x.test/a", "", "", 0, 15), Citation(1, "https://x.test/a", "", "", 15, 63)]
    content = 'Alpha is cheap.Alpha says "price is five dollars a month" today.'
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.QUOTED


def test_no_verify_grades_everything_unchecked_without_fetching():
    cits, content = _cits(("https://x.test/a", "Alpha is cheap."))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({}), verify=False)
    assert g.grade is Grade.UNCHECKED


def test_count_grades_lists_every_grade_in_order():
    cits, content = _cits(("https://x.test/a", "Alpha is cheap."))
    counts = count_grades(grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)})))
    assert list(counts) == ["quoted", "live", "misquoted", "dead", "unverifiable", "unchecked"]
    assert counts == {"quoted": 0, "live": 1, "misquoted": 0, "dead": 0, "unverifiable": 0, "unchecked": 0}


def test_quoted_span_on_a_dead_page_is_dead_not_unverifiable():
    cits, content = _cits(("https://x.test/a", 'Alpha says "price is five dollars a month" today.'))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (404, "")}))
    assert g.grade is Grade.DEAD and g.detail == "HTTP 404" and g.quote == "price is five dollars a month"


def test_quoted_span_on_a_pdf_is_unverifiable():
    cits, content = _cits(("https://x.test/a.pdf", 'Alpha says "price is five dollars a month" today.'))
    pages = {"https://x.test/a.pdf": (200, "%PDF-1.7 binary", "application/pdf", False)}
    [g] = grade_citations(cits, content, fetcher=fetcher_stub(pages))
    assert g.grade is Grade.UNVERIFIABLE and "content type application/pdf" in g.detail


PAGE_CODE = "<html><body><p>Absolute end-effector poses (move_to): x, y, z and gripper.</p></body></html>"


def test_quoted_when_only_trailing_punctuation_differs():
    # The report puts its sentence period inside the quote marks; the page's sentence runs on.
    # A one-character punctuation difference is not a misquote, and calling it one made six of
    # eight citations on a correctly-quoted report look wrong.
    cits, content = _cits(("https://x.test/a", 'Alpha says "price is five dollars a month." Today.'))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.QUOTED


def test_quoted_when_the_quote_carries_markdown_inline_code():
    # Backticks are the report's markup, not the page's text.
    span = 'The spec says "Absolute end-effector poses (`move_to`): x, y, z and gripper." here.'
    cits, content = _cits(("https://x.test/a", span))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE_CODE)}))
    assert g.grade is Grade.QUOTED


def test_still_misquoted_when_the_words_themselves_differ():
    # The guard: trimming punctuation must not turn the check into a rubber stamp.
    cits, content = _cits(("https://x.test/a", 'Alpha says "price is nine dollars a month." Today.'))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.MISQUOTED


def test_trimming_must_not_drop_a_quote_under_the_evidence_floor():
    # "yearly......." is long enough raw and far too short trimmed; a short string matches
    # almost any page, so it must come back unverifiable rather than quoted.
    cits, content = _cits(("https://x.test/a", 'Alpha says "yearly......." today.'))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, PAGE)}))
    assert g.grade is Grade.UNVERIFIABLE and "short" in g.detail


ELIDED_PAGE = ("<html><body><p>Replacing the environment will cause the objects to move randomly. "
               "There are other notes here. Replacing it with main_table works.</p></body></html>")


def test_quoted_when_an_elided_quote_has_every_fragment_on_the_page():
    # "A ... B" is the ordinary convention for quoting two passages; demanding the joined
    # string verbatim called two real citations on the LIBERO run misquoted.
    span = ('It says "Replacing the environment will cause the objects to move randomly '
            '… Replacing it with main_table works" there.')
    cits, content = _cits(("https://x.test/a", span))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, ELIDED_PAGE)}))
    assert g.grade is Grade.QUOTED and "fragment" in g.detail


def test_misquoted_when_an_elided_fragment_is_absent():
    # The guard: every fragment must be there, not just the first one.
    span = ('It says "Replacing the environment will cause the objects to move randomly '
            '… Replacing it with a teapot works" there.')
    cits, content = _cits(("https://x.test/a", span))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, ELIDED_PAGE)}))
    assert g.grade is Grade.MISQUOTED


def test_elided_fragments_must_still_clear_the_evidence_floor_in_total():
    # Two tiny fragments match almost any page; together they are still not evidence.
    span = 'It says "randomly … works ... here" there.'
    cits, content = _cits(("https://x.test/a", span))
    [g] = grade_citations(cits, content, fetcher=fetcher_stub({"https://x.test/a": (200, ELIDED_PAGE)}))
    assert g.grade is not Grade.QUOTED

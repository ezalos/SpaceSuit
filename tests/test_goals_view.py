# ABOUTME: Tests for the read-only HTML report: escaping, approved links, unknown-vs-zero,
# ABOUTME: the persistent synthetic label, viewer-computed staleness and section order.

import re

import pytest

from goals import report as R
from goals import view as V
from tests.test_goals_report import (NOW, OBFUSCATED, cost, coverage, evidence_goal, goal, row,
                                     with_coverage)

HREF = re.compile(r'href="([^"]*)"')
SECTIONS = ("outcome", "usage", "price", "scope", "uncertainty", "attention", "budget")


def html_for(g=None, c=None):
    return V.render(R.build_report(g or goal(), c or cost(), now_utc=NOW))


def test_it_renders_a_complete_html_document():
    page = html_for()
    assert page.lstrip().lower().startswith("<!doctype html")
    assert page.rstrip().endswith("</html>")


def test_the_page_is_self_contained_with_no_remote_asset():
    page = html_for()
    assert "<script src=" not in page
    assert 'rel="stylesheet"' not in page
    assert "https://fonts." not in page


def test_every_value_from_the_inputs_is_escaped():
    g = goal(title='A <script>alert("x")</script> goal')
    page = html_for(g)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_synthetic_report_carries_a_persistent_synthetic_label():
    assert "SYNTHETIC EXAMPLE" in html_for()


def test_a_non_synthetic_report_carries_no_synthetic_label():
    page = html_for(goal(synthetic=False))
    assert "SYNTHETIC EXAMPLE" not in page


def preview_banner(page):
    banner = page[page.index('id="preview-banner"'):]
    return banner[:banner.index("</div>")]


def test_the_url_param_preview_of_a_synthetic_report_carries_the_synthetic_label():
    page = html_for()
    assert "?preview" in page
    banner = preview_banner(page)
    assert "SYNTHETIC EXAMPLE" in banner and "PREVIEW" in banner


def test_the_url_param_preview_of_a_real_report_is_never_apparent_live_acceptance():
    banner = preview_banner(html_for(goal(synthetic=False)))
    assert "PREVIEW" in banner
    assert "not an acceptance record" in banner


def test_every_link_is_relative_or_https():
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "run", "ref": "https://example.org/run/1"},
                                   {"label": "log", "ref": "runs/green.txt"}], "blocker": None})
    for href in HREF.findall(html_for(g)):
        assert href.startswith("https://") or not re.match(r"^[a-zA-Z]+:", href), href


def test_a_withheld_reference_is_shown_as_text_and_never_as_a_link():
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "local proof", "ref": "/home/someone/secret.txt"}],
                      "blocker": None})
    page = html_for(g)
    assert "/home/someone/secret.txt" not in page
    assert "withheld" in page.lower()
    assert "local proof" in page


def test_an_unknown_duration_reads_as_unknown_and_never_as_zero():
    g = goal(authorized_utc=None)
    page = html_for(g)
    block = page[page.index('id="time-to-acceptance"'):]
    block = block[:block.index("</div>")]
    assert "unknown" in block.lower()
    assert ">0<" not in block and "0m" not in block


def test_a_not_applicable_duration_says_so_rather_than_claiming_it_is_unknown():
    # An accepted goal has no "open work" elapsed time. That is not an unknown, and drawing the
    # unknown chip there would invent doubt about a figure nobody is missing.
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    page = html_for(g)
    block = page[page.index("Elapsed, open work"):]
    block = block[:block.index("</div>")]
    assert "not applicable" in block
    assert 'class="unknown"' not in block


def test_absent_bounds_read_as_no_bound_rather_than_unknown_for_a_whole_root_scope():
    # `whole-session-roots` takes no since/until. Drawing the unknown chip there invents doubt
    # about a bound the mode does not have.
    page = html_for()
    block = page[page.index('id="scope"'):]
    block = block[:block.index("</section>")]
    assert "no bound" in block
    assert 'class="unknown"' not in block


def test_a_day_bounded_scope_shows_its_bounds():
    g = goal(scope={"roots": ["root-a"], "mode": "utc-days", "since": "2026-09-01",
                    "until": "2026-09-10", "ownership": "dedicated"})
    snap = cost()
    snap["scope"]["since"], snap["scope"]["until"] = "2026-09-01", "2026-09-10"
    block = html_for(g, snap)
    assert "2026-09-01" in block and "2026-09-10" in block


def test_one_open_worker_interval_is_described_in_the_singular():
    g = goal(worker_intervals=[{"actor": "w-1", "start_utc": "2026-09-17T00:00:00Z",
                                "end_utc": None}])
    assert "1 worker interval has no end" in html_for(g)


def test_two_open_worker_intervals_are_described_in_the_plural():
    g = goal(worker_intervals=[{"actor": "w-1", "start_utc": "2026-09-17T00:00:00Z", "end_utc": None},
                               {"actor": "w-2", "start_utc": "2026-09-17T01:00:00Z", "end_utc": None}])
    assert "2 worker intervals have no end" in html_for(g)


def test_the_full_precision_figure_is_marked_as_the_engine_s_own_literal():
    snap = cost(rows=[row("root-a", usd="0.072899999999999993")])
    page = html_for(None, snap)
    assert "0.072899999999999993" in page
    assert "as the engine wrote it" in page


def test_a_single_model_price_source_is_not_described_in_the_plural():
    snap = cost()
    snap["prices"]["sources"] = {"override": {"at": "2026-09-12", "count": 1}}
    assert "1 model " in html_for(None, snap) or "1 model<" in html_for(None, snap)


def test_staleness_is_computed_by_the_viewer_not_frozen_into_the_page():
    page = html_for()
    assert 'data-snapshot-at="2026-09-17T11:30:00Z"' in page
    assert "Date.now()" in page
    assert "stale: true" not in page.lower()


def test_money_is_labelled_allocated_api_equivalent_and_never_cash():
    page = html_for()
    assert "allocated API-equivalent" in page
    assert "UNKNOWN - not connected" in page


def test_an_unknown_root_is_shown_as_unknown_coverage_not_as_zero_cost():
    g = goal(scope={"roots": ["root-a", "root-gone"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    snap = cost()
    snap["scope"]["roots"] = ["root-a", "root-gone"]
    snap["scope"]["resolvedRoots"] = ["root-a"]
    snap["scope"]["unknownRoots"] = ["root-gone"]
    page = html_for(g, snap)
    assert "1 requested root matched no transcript" in page or "roots unknown" in page.lower()
    assert "does not cover the whole requested scope" in page


def test_a_conflicted_snapshot_says_token_totals_are_unknown():
    snap = cost(rows=[row("root-a", cwConflict=5, withheldTurns=1, partial=True,
                          partialReasons=["count-conflict"])])
    page = html_for(None, snap)
    assert "contradict each other" in page


def test_sections_run_outcome_then_usage_then_price_then_scope_and_budget_last():
    page = html_for()
    order = [page.index(f'id="{name}"') for name in
             ("outcome", "usage", "price", "scope", "uncertainty", "budget")]
    assert order == sorted(order)


def test_the_page_is_readable_on_a_phone_and_in_both_themes():
    page = html_for()
    assert "prefers-color-scheme: dark" in page
    assert ':root[data-theme="dark"]' in page
    assert "--gutter: 16px" in page


def test_no_private_input_string_survives_into_the_page():
    from tests.test_goals_report import PRIVATE, private_inputs
    page = V.render(R.build_report(*private_inputs(), now_utc=NOW))
    for secret in PRIVATE:
        assert secret not in page, secret


def test_the_page_states_that_nothing_was_re_verified():
    assert "re-verified none of them" in html_for()


# --- I1: the report is substituted into the template exactly once -------------------------

def test_the_template_declares_exactly_one_body_slot():
    template = V.TEMPLATE.read_text(encoding="utf-8")
    assert template.count("{{BODY}}") == 1


def test_the_report_appears_exactly_once_in_the_page():
    page = html_for()
    assert page.count("<h1>") == 1
    assert page.count('<section id="') == len(SECTIONS)
    for name in SECTIONS:
        assert page.count(f'id="{name}"') == 1, name


def test_a_title_carrying_the_body_token_stays_literal_data():
    page = html_for(goal(title="{{BODY}} quarterly goal"))
    assert page.count("<h1>") == 1
    assert page.count('<section id="') == len(SECTIONS)
    title = page[page.index("<title>"):page.index("</title>")]
    assert len(title) < 200, len(title)
    assert "{{BODY}} quarterly goal" in title


def test_a_blocker_carrying_a_template_token_stays_literal_data():
    g = goal(outcome={"status": "blocked", "implementer": "w", "verifier": None,
                      "accepted_utc": None, "evidence": [],
                      "blocker": "{{TITLE}} and {{SNAPSHOT_AT}} are not substituted here"})
    page = html_for(g)
    assert page.count('<section id="') == len(SECTIONS)
    assert "{{TITLE}} and {{SNAPSHOT_AT}} are not substituted here" in page


def test_an_unknown_template_token_is_a_loud_failure_not_a_silent_hole(tmp_path, monkeypatch):
    broken = tmp_path / "broken.html"
    broken.write_text(V.TEMPLATE.read_text(encoding="utf-8").replace("{{BODY}}", "{{MYSTERY}}"))
    monkeypatch.setattr(V, "TEMPLATE", broken)
    with pytest.raises(RuntimeError, match="MYSTERY"):
        html_for()


def test_a_template_missing_its_body_slot_is_a_loud_failure(tmp_path, monkeypatch):
    broken = tmp_path / "broken.html"
    broken.write_text(V.TEMPLATE.read_text(encoding="utf-8").replace("{{BODY}}", ""))
    monkeypatch.setattr(V, "TEMPLATE", broken)
    with pytest.raises(RuntimeError, match="BODY"):
        html_for()


# --- I3: the page distinguishes a file digest from an object digest -----------------------

def test_the_page_says_the_raw_digest_is_unknown_when_built_from_an_object():
    page = html_for()
    provenance = page[page.index('class="provenance"'):page.index("</header>")]
    assert "canonical object sha256" in provenance
    assert "snapshot file sha256" in provenance
    assert "built from an in-memory object" in provenance


def test_the_page_shows_the_file_digest_when_there_is_a_file(tmp_path):
    import json as _json
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text(_json.dumps(goal()))
    cp.write_text(_json.dumps(cost(), default=str))
    page = V.render(R.load_report(gp, cp, now_utc=NOW))
    import hashlib
    assert hashlib.sha256(cp.read_bytes()).hexdigest() in page
    assert "sha256sum" in page


# --- M1: the page and the JSON withhold exactly the same references -----------------------

@pytest.mark.parametrize("ref", OBFUSCATED)
def test_an_obfuscated_reference_never_becomes_a_link(ref):
    page = html_for(evidence_goal(ref))
    assert ref.strip() not in page
    assert "withheld" in page
    for href in HREF.findall(page):
        assert href.startswith("https://") or not re.match(r"^[a-zA-Z]+:", href), href


def test_the_page_names_the_same_withholding_reason_as_the_json():
    report = R.build_report(evidence_goal("\tjavascript:alert(1)"), cost(), now_utc=NOW)
    kind = report["outcome"]["evidence"][0]["ref_kind"]
    assert f"reference withheld ({kind})" in V.render(report)


# --- M2: both daily caps are visible, including the unknown one ---------------------------

def multiday_goal():
    return goal(interventions=[
        # over minutes, under the checkpoint cap
        {"id": "a1", "at_utc": "2026-09-14T09:00:00Z", "required": True, "minutes": 26},
        # under both
        {"id": "b1", "at_utc": "2026-09-15T09:00:00Z", "required": True, "minutes": 5},
        # unknown minutes
        {"id": "c1", "at_utc": "2026-09-16T09:00:00Z", "required": True, "minutes": None},
        # over the checkpoint cap
        {"id": "d1", "at_utc": "2026-09-17T09:00:00Z", "required": True, "minutes": 2},
        {"id": "d2", "at_utc": "2026-09-17T10:00:00Z", "required": True, "minutes": 2},
        {"id": "d3", "at_utc": "2026-09-17T11:00:00Z", "required": True, "minutes": 2},
        {"id": "d4", "at_utc": "2026-09-18T09:00:00Z", "required": False, "minutes": 40},
    ])


def attention_rows(page):
    table = page[page.index("<tbody>"):page.index("</tbody>")]
    return [row_html for row_html in table.split("<tr>") if row_html.strip()]


def test_the_attention_table_shows_the_minute_cap_state_beside_the_checkpoint_state():
    page = html_for(multiday_goal())
    header = page[page.index("<thead>"):page.index("</thead>")]
    assert "checkpoint cap" in header
    assert "minute cap" in header


def cells(row_html):
    """The value of each cell, with the one-box-per-cell wrapper taken off."""
    found = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    return [re.sub(r'^<span class="cell">(.*)</span>$', r"\1", one, flags=re.S) for one in found]


def price_or_attention_head(page):
    """The attention table's own thead."""
    block = page[page.index('id="attention"'):]
    return block[block.index("<thead>"):block.index("</thead>")]


def phone_block():
    template = V.TEMPLATE.read_text(encoding="utf-8")
    block = template[template.index("@media (max-width: 420px)"):]
    return block[:block.index("</style>")]


def test_no_table_element_is_turned_into_a_block_at_phone_width():
    # `display:block` on a table drops the implicit table roles in every major engine, so the
    # header row stops being a header for assistive technology however carefully it is hidden.
    # The comparison page was fixed to keep real tables; this one must give the same answer.
    assert not re.search(
        r"(?m)^\s*[^{}]*\b(table|tbody|tr|td|caption)\b[^{}]*\{[^}]*display:\s*block",
        phone_block())


def test_no_cell_label_is_generated_content_only():
    # CSS generated content is not reliably read as text. A label a reader needs is a real element.
    template = V.TEMPLATE.read_text(encoding="utf-8")
    assert "content: attr(data-label)" not in template
    assert "td::before" not in template


def test_every_header_cell_declares_its_column_scope():
    page = html_for(multiday_goal())
    headers = re.findall(r"<th\b[^>]*>", page)
    assert headers
    for header in headers:
        assert 'scope="col"' in header, header


def test_a_table_too_wide_for_a_phone_scrolls_inside_a_labelled_focusable_region():
    page = html_for(multiday_goal())
    region = re.search(r'<div class="scroll" role="region" tabindex="0" aria-label="([^"]+)">', page)
    assert region, "the attention table has no focusable scroll region"
    assert region.group(1).strip()
    assert page[region.end():].startswith("<table")


def test_the_scroll_explanation_sits_outside_the_region_it_explains():
    # A sentence inside a 560px table scrolls away with it, so the explanation of the scroll would
    # itself be the thing you had to scroll to read.
    page = html_for(multiday_goal())
    region_at = page.index('<div class="scroll"')
    intro_at = page.index("scrolls sideways")
    assert intro_at < region_at


def test_every_table_wide_enough_to_overflow_sits_inside_a_scroll_region():
    """The body cannot scroll sideways if the only thing wider than it is inside a scroll
    container. `table.wide` sets a min-width past 390px; every one of them must be in a `.scroll`."""
    page = html_for(multiday_goal())
    for match in re.finditer(r'<table class="wide"', page):
        before = page[:match.start()]
        region_at = before.rfind('<div class="scroll"')
        assert region_at != -1, "a wide table sits outside any scroll region"
        assert "</div>" not in before[region_at:], "the scroll region closed before its table"


def test_no_rule_outside_the_scroll_region_sets_a_width_past_a_phone():
    template = V.TEMPLATE.read_text(encoding="utf-8")
    style = template[template.index("<style>"):template.index("</style>")]
    for width in re.findall(r"min-width:\s*(\d+)px", style):
        assert int(width) <= 390 or "table.wide" in style[:style.index(f"min-width: {width}px")][-80:]


def test_the_offscreen_idiom_is_not_the_deprecated_clip_rect():
    assert "clip: rect(" not in V.TEMPLATE.read_text(encoding="utf-8")


def test_every_attention_column_is_named_by_a_visible_header_cell():
    # The mechanism that names a column here is the header row, which stays visible and scrolls
    # with the table. Asserting a `data-label` attribute proved nothing: nothing rendered it.
    page = html_for(multiday_goal())
    headers = re.findall(r'<th scope="col">(.*?)</th>', price_or_attention_head(page), re.S)
    row = [r for r in attention_rows(page) if "2026-09-14" in r][0]
    assert len(headers) == len(cells(row))
    assert any("minute cap" in h for h in headers)
    assert any("checkpoint cap" in h for h in headers)


def test_the_visible_headers_keep_the_per_goal_qualifier():
    headers = re.findall(r'<th scope="col">(.*?)</th>',
                         price_or_attention_head(html_for(multiday_goal())), re.S)
    assert [h for h in headers if "cap" in h] == [
        "over checkpoint cap, this goal alone", "over minute cap, this goal alone"]


def test_a_day_over_the_minute_cap_but_under_the_checkpoint_cap_shows_both_states():
    # 26 required minutes against a 20-minute cap, in one checkpoint against a cap of two:
    # over on minutes, under on checkpoints. Both answers must be on the row.
    day = cells([r for r in attention_rows(html_for(multiday_goal())) if "2026-09-14" in r][0])
    assert day[0] == "2026-09-14"
    assert day[1] == "1" and day[2] == "26"
    assert day[-2] == "no"   # over checkpoint cap
    assert day[-1] == "yes"  # over minute cap


def test_a_day_with_unknown_minutes_answers_unknown_for_the_minute_cap():
    page = html_for(multiday_goal())
    day = [r for r in attention_rows(page) if "2026-09-16" in r][0]
    assert "unknown" in day


def test_the_attention_table_still_separates_this_goal_from_the_whole_pilot():
    page = html_for(multiday_goal())
    assert "this goal alone" in page
    assert "whole pilot" in page


# --- M4: the uncertainty list is readable on a phone, and nothing is concealed -------------

def test_the_uncertainty_list_stacks_its_code_above_its_text_at_phone_width():
    # Two separate substrings let the assertion survive deleting the rule it names: `display:
    # block` is also supplied by `.limit-code`. Match the rule itself.
    phone = phone_block()
    rule = phone[phone.index(".limits li"):]
    rule = rule[:rule.index("}")]
    assert "display: block" in rule


def test_a_long_limitation_code_can_wrap_rather_than_be_clipped():
    template = V.TEMPLATE.read_text(encoding="utf-8")
    code_rule = template[template.index(".limit-code {"):]
    code_rule = code_rule[:code_rule.index("}")]
    assert "nowrap" not in code_rule
    assert "overflow-wrap: anywhere" in code_rule


def test_the_page_never_conceals_overflow_instead_of_wrapping_it():
    template = V.TEMPLATE.read_text(encoding="utf-8")
    body_rule = template[template.index("\nbody {"):]
    body_rule = body_rule[:body_rule.index("}")]
    assert "overflow-x" not in body_rule, body_rule
    assert "overflow-wrap" in body_rule


# --- allocation coverage is visible, and separate from price completeness --------------------

def test_the_scope_section_shows_price_completeness_apart_from_source_coverage():
    page = html_for(None, with_coverage(coverage(False, 2)))
    scope = page[page.index('id="scope"'):page.index('id="uncertainty"')]
    assert "Source allocation coverage" in scope
    assert "2" in scope
    price = page[page.index('id="price"'):page.index('id="scope"')]
    assert "Pricing complete" in price


def test_incomplete_source_coverage_is_visible_above_the_budget_section():
    page = html_for(None, with_coverage(coverage(False, 2)))
    assert page.index("Source allocation coverage") < page.index('id="budget"')


def test_a_legacy_snapshot_draws_the_unknown_chip_for_source_coverage():
    page = html_for(None, with_coverage(None, drop=True))
    scope = page[page.index('id="scope"'):page.index('id="uncertainty"')]
    block = scope[scope.index("Source allocation coverage"):]
    assert 'class="unknown"' in block[:400]


def test_incomplete_source_coverage_states_the_count_and_disclaims_attribution():
    page = html_for(None, with_coverage(coverage(False, 3)))
    assert "3 shared message id" in page
    assert "not a producing-goal claim" in page


def test_the_engines_own_sentence_does_not_survive_onto_the_page():
    from tests.test_goals_report import MEANING
    assert MEANING not in html_for(None, with_coverage(coverage(False, 1)))


def test_the_page_never_prints_the_upstream_meaning_string():
    planted = "shared with 0f3ab2c1-11de-4d3e-9a77-aa11bb22cc33 under root-a"
    page = html_for(None, with_coverage(coverage(False, 1, meaning=planted)))
    assert planted not in page
    assert "0f3ab2c1" not in page and "root-a" not in page


def test_the_page_disclaims_the_day_bounds_the_count_ignores():
    page = html_for(None, with_coverage(coverage(False, 2)))
    block = page[page.index("Source allocation coverage"):]
    assert "day bounds" in block[:900] or "not clipped" in block[:900]


def test_the_page_renders_the_lineage_enum_not_producer_text():
    page = html_for(None, cost(lineageFrom="/home/ezalos/.claude/projects/private.jsonl"))
    assert "/home/ezalos" not in page and ".jsonl" not in page
    assert "unknown-lineage-descriptor" in page


def test_the_page_renders_the_known_lineage_enum():
    assert "path-nesting-unverified" in html_for()


def test_complete_source_coverage_says_so_without_a_count_of_ids():
    page = html_for(None, with_coverage(coverage(True, 0)))
    scope = page[page.index('id="scope"'):page.index('id="uncertainty"')]
    block = scope[scope.index("Source allocation coverage"):]
    assert "complete" in block[:300].lower()
    assert "shared message id" not in block[:300]


def test_the_page_never_shows_a_chart_or_a_meter_for_coverage():
    page = html_for(None, with_coverage(coverage(False, 2)))
    for forbidden in ("<svg", "<canvas", "<progress", "<meter", "role=\"progressbar\""):
        assert forbidden not in page, forbidden


def test_no_upstream_identifier_reaches_the_page():
    snap = with_coverage(coverage(False, 1))
    snap["scope"]["allocationCoverage"]["allocatedElsewhere"] = {
        "msg_01private": "/home/someone/private/repo/root-a.jsonl"}
    page = V.render(R.build_report(goal(), snap, now_utc=NOW))
    assert "msg_01private" not in page and "/home/someone/private" not in page


def test_the_source_coverage_wording_survives_in_the_uncertainty_section():
    page = html_for(None, with_coverage(coverage(False, 1)))
    uncertainty = page[page.index('id="uncertainty"'):page.index('id="attention"')]
    assert "source-allocation-outside-scope" in uncertainty


def test_a_legacy_snapshot_names_its_own_limitation_code():
    page = html_for(None, with_coverage(None, drop=True))
    uncertainty = page[page.index('id="uncertainty"'):page.index('id="attention"')]
    assert "source-allocation-coverage-unknown" in uncertainty


# --- I-2 / M3 / M8 on the page ---------------------------------------------------------------

def test_the_price_row_is_named_for_the_combined_verdict():
    page = html_for(None, with_coverage(coverage(False, 1)))
    assert "Covers every requested root" not in page
    assert "Whole source scope covered" in page


def test_the_page_explains_what_the_combined_verdict_requires():
    page = html_for()
    price = page[page.index('id="price"'):page.index('id="scope"')]
    assert "requested root" in price
    assert "allocation coverage" in price


def test_a_rowless_snapshot_says_so_on_the_page():
    page = html_for(None, cost(rows=[]))
    assert "no priced rows" in page.lower()
    assert "not zero" in page.lower()


def test_the_attention_section_says_absent_days_are_unrecorded():
    g = goal(interventions=[{"id": "i-1", "at_utc": "2026-09-17T02:00:00Z",
                             "required": True, "minutes": 5}])
    page = html_for(g)
    block = page[page.index('id="attention"'):]
    assert "not recorded" in block[:block.index("</section>")].lower()


def test_the_evidence_source_reaches_the_page():
    g = goal(evidence_source={"label": "the libero campaign ledger", "revision": "r7"})
    page = html_for(g)
    assert "the libero campaign ledger" in page
    assert "r7" in page


def test_a_report_without_an_evidence_source_says_nothing_about_one():
    """Coupled to the field the page actually renders. The previous assertion looked for a
    CAPITALISED `Evidence source`, which this page never writes at all, so it held whether or not
    a source was present."""
    with_source = html_for(goal(evidence_source={"label": "the libero campaign ledger",
                                                 "revision": "r7"}))
    without = html_for()
    provenance = without[without.index('class="provenance"'):without.index("</header>")]
    assert "evidence source" in with_source
    assert "evidence source" not in provenance


def test_the_attention_cells_carry_no_inert_data_label():
    # `.cell-label` is display:none at every width on this page and no `content: attr(data-label)`
    # rule exists, so the attribute rendered nothing and assistive technology never read it. The
    # table relies on its visible scrolling header instead.
    page = html_for(multiday_goal())
    assert "data-label" not in page


def test_one_boolean_is_named_the_same_way_everywhere_it_appears():
    """One verdict, one name - in the price row, in the scope row, and in the sentence that
    explains it. Two phrasings for one boolean is how a reader ends up thinking there are two."""
    page = html_for(None, with_coverage(coverage(False, 1)))
    price = page[page.index('id="price"'):page.index('id="scope"')]
    scope = page[page.index('id="scope"'):page.index('id="uncertainty"')]
    assert "Whole source scope covered" in price
    assert "Whole source scope covered" in scope
    assert "Whole requested scope covered" not in page
    assert "Covers every requested root" not in page

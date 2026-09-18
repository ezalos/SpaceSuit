# ABOUTME: Tests for the comparison HTML document: escaping, link safety, the persistent synthetic
# ABOUTME: label, unknown-vs-zero, viewer-computed staleness, no chart or progress bar, no raw leaks.

import hashlib
import json
import re

import pytest

from goals import comparison as C
from goals import comparison_view as V
from tests.test_goals_comparison import (AS_OF, NOW, OPEN_AS_OF, OPEN_NOW, build, candidate_shape,
                                         live_arms, pair, protocol, stage, work_class)
from tests.test_goals_report import OBFUSCATED

HREF = re.compile(r'href="([^"]*)"')
SECTIONS = ("posture", "classes", "elapsed", "attention", "budget", "price", "uncertainty", "sources")


def page_for(tmp_path, proto=None, **kwargs):
    return V.render_comparison(build(tmp_path, proto, **kwargs))


def section(page, ident):
    body = page[page.index(f'id="{ident}"'):]
    return body[:body.index("</section>")]


def test_it_renders_a_complete_html_document(tmp_path):
    page = page_for(tmp_path)
    assert page.lstrip().lower().startswith("<!doctype html")
    assert page.rstrip().endswith("</html>")


def test_the_page_is_self_contained_with_no_remote_asset(tmp_path):
    page = page_for(tmp_path)
    assert "<script src=" not in page
    assert 'rel="stylesheet"' not in page
    assert "https://fonts." not in page
    assert "<img" not in page


def test_it_uses_its_own_template_and_never_the_goal_report_one(tmp_path):
    assert V.TEMPLATE.name == "comparison.html"
    page = page_for(tmp_path)
    assert "delegation comparison" in page.lower()
    # The single-goal report's own eyebrow: the two documents share a design language, not a file.
    assert '<p class="eyebrow">goal report</p>' not in page


def test_every_section_the_brief_names_is_present_and_in_order(tmp_path):
    page = page_for(tmp_path)
    positions = [page.index(f'id="{ident}"') for ident in SECTIONS]
    assert positions == sorted(positions)


def test_every_value_from_the_protocol_is_escaped(tmp_path):
    page = page_for(tmp_path, protocol(cohort_id='<script>alert("x")</script>'))
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_synthetic_comparison_carries_a_persistent_synthetic_label(tmp_path):
    assert "SYNTHETIC EXAMPLE" in page_for(tmp_path)


def test_a_comparison_with_no_synthetic_input_carries_no_synthetic_label(tmp_path):
    proto = candidate_shape(synthetic=False)
    assert "SYNTHETIC EXAMPLE" not in page_for(tmp_path, proto, arms=live_arms(proto))


def test_the_synthetic_label_survives_a_candidate_shaped_comparison(tmp_path):
    page = page_for(tmp_path, candidate_shape())
    assert "SYNTHETIC EXAMPLE" in page
    assert "inconclusive" in section(page, "posture")


# --- the posture is never an approval ------------------------------------------------------

def test_the_posture_is_spelled_out_with_its_reason_codes(tmp_path):
    body = section(page_for(tmp_path), "posture")
    assert "budget-admission-not-attested" in body
    assert "inconclusive" in body


def test_a_candidate_posture_still_says_it_is_not_an_approval(tmp_path):
    proto = candidate_shape(synthetic=False)
    page = page_for(tmp_path, proto, arms=live_arms(proto))
    body = section(page, "posture")
    assert "candidate" in body
    assert "not an approval" in body.lower()


@pytest.mark.parametrize("word", ["promoted", "approved", "approve this", "ship it"])
def test_no_page_state_ever_reads_as_an_approval(tmp_path, word):
    proto = candidate_shape(synthetic=False)
    page = page_for(tmp_path, proto, arms=live_arms(proto))
    assert not re.search(rf"\b{re.escape(word)}\b", page.replace("not an approval", ""), re.I)


# --- forms: a table and tiles, never a chart or a progress bar --------------------------------

def test_the_page_draws_no_chart(tmp_path):
    page = page_for(tmp_path)
    for forbidden in ("<svg", "<canvas", "chart", "sparkline"):
        assert forbidden not in page.lower()


def test_the_page_draws_no_progress_or_budget_bar(tmp_path):
    page = page_for(tmp_path)
    for forbidden in ("<progress", 'role="progressbar"', "progress-bar", "meter"):
        assert forbidden not in page.lower()


def test_the_two_arms_are_drawn_identically_so_no_colour_declares_a_winner(tmp_path):
    body = section(page_for(tmp_path), "classes")
    assert body.count('data-arm="baseline"') == body.count('data-arm="delegated"')
    assert "winner" not in body.lower() and "better" not in body.lower()


# --- the comparison itself ---------------------------------------------------------------------

def test_each_class_shows_every_classification_count(tmp_path):
    body = section(page_for(tmp_path, arms={"g-base-1": {"status": "failed"}}), "classes")
    for label in ("accepted", "failed", "blocked", "abandoned", "censored", "pending"):
        assert label in body.lower()


def test_the_median_is_never_shown_without_its_denominator_and_censored_count(tmp_path):
    body = section(page_for(tmp_path, arms={"g-base-1": {"status": "in_progress"}}), "classes")
    assert "accepted-only" in body.lower()
    # the qualifier travels with the figure, in the same cell
    cell = body[body.index("accepted-only"):]
    cell = cell[:cell.index("</td>") + 5] if "</td>" in cell else cell
    assert "of 3" in body or "2 of 3" in body
    assert "censored" in body.lower()


def test_an_unknown_median_is_drawn_as_an_unknown_and_never_as_zero(tmp_path):
    arms = {gid: {"status": "failed"} for gid in ("g-base-1", "g-base-2", "g-base-3")}
    body = section(page_for(tmp_path, arms=arms), "classes")
    cell = re.search(r'<td data-arm="baseline" data-metric="median"[^>]*>(.*?)</td>',
                     body, re.S).group(1)
    assert 'class="unknown"' in cell
    # Not a duration, not a zero: the only digits in the cell belong to its own denominators.
    assert not re.search(r"\d+[dhms]\b", cell.split('class="tile-note"')[0])
    delegated = re.search(r'<td data-arm="delegated" data-metric="median"[^>]*>(.*?)</td>',
                          body, re.S).group(1)
    assert 'class="unknown"' not in delegated


def test_the_elapsed_time_definition_is_stated_on_the_page(tmp_path):
    body = section(page_for(tmp_path), "elapsed")
    assert "authoriz" in body.lower()
    assert "censor" in body.lower()
    assert "utc-day" in body.lower()


# --- attention and budget -----------------------------------------------------------------------

def test_a_calendar_day_with_no_record_reads_as_not_recorded(tmp_path):
    body = section(page_for(tmp_path), "attention")
    assert "not recorded" in body.lower()
    assert "pilot-wide" in body.lower()


def test_an_unknown_required_minute_count_reads_as_unknown(tmp_path):
    body = section(page_for(tmp_path, interventions=[
        {"id": "c-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": None}]),
        "attention")
    assert 'class="unknown"' in body


def test_the_budget_attestation_is_never_drawn_as_a_cash_check(tmp_path):
    body = section(page_for(tmp_path), "budget")
    assert "not-attested" in body
    cash = re.search(r"<dt>Cash spent</dt><dd>(.*?)</dd>", body, re.S).group(1)
    assert 'class="unknown"' in cash and "$" not in cash
    assert "UNKNOWN - not connected" in cash


def test_an_attested_budget_says_it_is_an_attestation_not_a_provider_check(tmp_path):
    proto = protocol()
    proto["budget_admission"] = {"status": "paid-route-bound-verified",
                                 "checked_utc": "2026-09-17T00:00:00Z",
                                 "evidence_ref": "evidence/admission.txt"}
    body = section(page_for(tmp_path, proto), "budget")
    assert "attestation" in body.lower()
    assert "provider" in body.lower()


def test_the_allocated_figure_is_labelled_allocated_and_never_cash(tmp_path):
    body = section(page_for(tmp_path), "price")
    assert "allocated API-equivalent" in body
    assert "not cash" in body.lower()


# --- references and links -------------------------------------------------------------------------

def test_every_link_is_relative_or_https(tmp_path):
    proto = protocol()
    proto["work_classes"] = [work_class(ref="https://example.org/oracles/bounded-code.md")]
    for href in HREF.findall(page_for(tmp_path, proto)):
        assert href.startswith("https://") or not re.match(r"^[a-zA-Z]+:", href), href


@pytest.mark.parametrize("ref", OBFUSCATED)
def test_a_withheld_oracle_reference_is_shown_as_text_and_never_as_a_link(tmp_path, ref):
    proto = protocol()
    proto["work_classes"] = [work_class(ref=ref)]
    page = page_for(tmp_path, proto)
    assert "withheld" in page.lower()
    assert ref.strip() not in page
    for href in HREF.findall(page):
        assert href.startswith("https://") or not re.match(r"^[a-zA-Z]+:", href), href


def test_the_page_links_nothing_the_comparison_withheld(tmp_path):
    # Not "no dangerous scheme" - NO link at all. A traversing relative path passes a scheme check
    # and would still be an href, so the page is pinned to the comparison's own verdict.
    proto = protocol()
    proto["work_classes"] = [work_class(ref="../../etc/passwd")]
    proto["policy"]["controls_evidence_ref"] = None
    page = page_for(tmp_path, proto)
    assert "etc/passwd" not in page
    assert HREF.findall(page) == []
    assert "withheld" in page.lower()


def test_the_safe_oracle_reference_is_linked_once(tmp_path):
    body = section(page_for(tmp_path), "sources")
    assert "oracles/bounded-code.md" in body


# --- provenance and staleness ------------------------------------------------------------------------

def test_the_input_fingerprints_are_shown_and_labelled_as_private_input_fingerprints(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    page = V.render_comparison(C.load_comparison(proto_path, obs_path, now_utc=NOW))
    assert hashlib.sha256(proto_path.read_bytes()).hexdigest() in page
    assert hashlib.sha256(obs_path.read_bytes()).hexdigest() in page
    assert "private input" in page.lower()


def test_staleness_is_anchored_on_the_observations_as_of_time_and_read_from_the_viewer_clock(tmp_path):
    page = page_for(tmp_path)
    assert f'data-as-of="{AS_OF}"' in page
    assert 'id="as-of-age"' in page
    assert "Date.now()" in page


def test_the_page_never_leaks_a_raw_root_a_path_or_a_report_hash(tmp_path):
    proto_path, obs_path = stage(tmp_path, raw=True)
    page = V.render_comparison(C.load_comparison(proto_path, obs_path, now_utc=NOW))
    assert "root-base-1" not in page and "root-del-1" not in page
    assert "reports/g-base-1.json" not in page
    assert str(tmp_path) not in page
    assert "model-a" not in page
    snapshot = json.loads((tmp_path / "reports" / "g-base-1.json").read_text())
    assert snapshot["source"]["snapshot_sha256"] not in page


def test_an_observing_comparison_never_looks_like_a_finished_result(tmp_path):
    page = page_for(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                    arms={"g-base-1": {"status": "in_progress"}})
    body = section(page, "posture")
    assert "observing" in body
    assert "pending" in page.lower()


# --- table semantics survive mobile width ------------------------------------------------------

def mobile_rules(page):
    style = page[page.index("<style>"):page.index("</style>")]
    return style[style.index("@media (max-width: 420px)"):]


def test_no_table_element_is_turned_into_a_block_at_mobile_width(tmp_path):
    # `display:block` on a table drops the implicit table roles in every major engine, so the
    # header row stops being a header for assistive technology no matter how it is hidden.
    rules = mobile_rules(page_for(tmp_path))
    assert not re.search(r"(?m)^\s*[^{}]*\b(table|tbody|tr|td)\b[^{}]*\{[^}]*display:\s*block",
                         rules)


def test_the_header_row_is_hidden_offscreen_rather_than_removed(tmp_path):
    page = page_for(tmp_path)
    rules = mobile_rules(page)
    block = rules[rules.index("thead"):]
    block = block[:block.index("}")]
    assert "position: absolute" in block
    assert "display: none" not in block
    assert page.count("<thead>") == page.count("</thead>") >= 3


def test_every_table_keeps_one_header_cell_per_column(tmp_path):
    page = page_for(tmp_path)
    tables = re.findall(r"<table[^>]*>(.*?)</table>", page, re.S)
    assert tables
    for block in tables:
        head = re.search(r"<thead><tr>(.*?)</tr></thead>", block, re.S)
        assert head, block[:160]
        columns = len(re.findall(r"<th", head.group(1)))
        first_row = re.search(r"<tbody><tr>(.*?)</tr>", block, re.S)
        if first_row:
            assert len(re.findall(r"<td", first_row.group(1))) == columns


# --- the fix round's own states, drawn honestly --------------------------------------------------

def test_an_unresolved_arm_is_drawn_as_unresolved_and_never_as_a_clean_outcome(tmp_path):
    page = page_for(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": "blocked"}})
    body = section(page, "classes")
    assert "unresolved" in body.lower()
    assert "terminal-attestation-missing" in page


def test_the_controls_attestation_is_shown_and_labelled_unverified(tmp_path):
    body = section(page_for(tmp_path), "budget")
    assert "controls" in body.lower()
    assert "attested" in body.lower()
    assert "not verified" in page_for(tmp_path).lower()


def test_a_mixed_cohort_never_headlines_candidate_on_the_page(tmp_path):
    proto = candidate_shape(synthetic=False)
    proto["work_classes"] = [work_class("bounded-code"), work_class("bounded-docs")]
    proto["pairs"] = [pair(i) for i in range(1, 4)] + [pair(i, "bounded-docs") for i in range(4, 7)]
    arms = live_arms(proto)
    for index in (4, 5, 6):
        arms[f"g-del-{index}"] |= {"accepted": "2026-09-22T00:00:00Z"}
    page = page_for(tmp_path, proto, arms=arms)
    body = section(page, "posture")
    assert "inconclusive" in body
    assert "mixed-class-results" in body


def test_the_median_cell_names_every_outcome_count(tmp_path):
    page = page_for(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": "blocked"}})
    body = section(page, "classes")
    cell = re.search(r'<td data-arm="baseline" data-metric="median"[^>]*>(.*?)</td>',
                     body, re.S).group(1)
    for name in ("accepted", "failed", "blocked", "abandoned", "censored", "unresolved", "pending"):
        assert name in cell, name


# --- a rate a reader can compare is never drawn over an unresolved arm --------------------------

def arm_cell(page, metric, side="baseline"):
    body = section(page, "classes")
    return re.search(rf'<td data-arm="{side}" data-metric="{metric}"[^>]*>(.*?)</td>',
                     body, re.S).group(1)


def test_an_acceptance_rate_over_an_unresolved_arm_reads_unknown_not_a_percentage(tmp_path):
    # Two rows below sits "Unresolved 1". A reader takes the direction from the percentages, and
    # a gate three sections down does not undo a number already read.
    page = page_for(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": "blocked"}})
    cell = arm_cell(page, "acceptance")
    assert 'class="unknown"' in cell
    assert "%" not in cell
    assert "unresolved" in cell.lower()


def test_the_other_side_still_shows_its_rate_when_only_one_side_is_unresolved(tmp_path):
    page = page_for(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": "blocked"}})
    assert "100.0%" in arm_cell(page, "acceptance", "delegated")


def test_a_defect_total_over_an_unresolved_arm_reads_unknown(tmp_path):
    page = page_for(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": "blocked"}})
    cell = arm_cell(page, "defects")
    assert 'class="unknown"' in cell
    assert "unresolved" in cell.lower()


def test_a_fully_resolved_side_still_shows_its_acceptance_percentage(tmp_path):
    assert "100.0%" in arm_cell(page_for(tmp_path), "acceptance")


# --- the pair note wraps at its delimiter, never mid-word ---------------------------------------

def test_a_matched_field_offers_a_break_at_its_delimiter(tmp_path):
    # `change_kind=parser` has no break opportunity in a 128px column, so a browser breaks it
    # mid-word. One intentional opportunity at the `=` is all it needs.
    body = section(page_for(tmp_path), "classes")
    assert "change_kind=<wbr>parser" in body
    assert "scope_band=<wbr>small" in body


def test_the_pair_note_never_uses_nowrap_which_would_overflow_instead(tmp_path):
    style = page_for(tmp_path)
    style = style[style.index("<style>"):style.index("</style>")]
    # `white-space: nowrap` is legitimate on the offscreen header; nowhere else.
    assert style.replace("white-space: nowrap", "").count("nowrap") == 0


def test_a_matched_value_is_still_escaped(tmp_path):
    proto = protocol()
    for entry in proto["pairs"]:
        entry["matched"]["change_kind"] = '<script>alert("x")</script>'
    page = page_for(tmp_path, proto)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


# --- the qualifier beside a null rate never denies its own reason --------------------------------

def test_the_defect_qualifier_never_claims_every_arm_attested_while_naming_unattested_arms(tmp_path):
    # The exact sentence the r2 reviewer was handed: "the rate is unknown BECAUSE arms did not
    # attest" and "3 of 3 arms attested", in one breath. The reassuring number beside the unknown
    # flag is the shape D1 exists to remove.
    page = page_for(tmp_path, escaped={"g-del-1": None})
    cell = arm_cell(page, "defects", "delegated")
    assert "unattested-arms: 3 of 3 arms attested and placed" not in cell
    assert "attested and placed" not in cell
    assert "2 of 3 arms" in cell
    assert "unattested-arms" in cell


def test_the_defect_qualifier_counts_attestation_not_placement(tmp_path):
    # Every arm placed, one of them silent: the population that matters here is the attested one.
    cell = arm_cell(page_for(tmp_path, escaped={"g-del-1": None}), "defects", "delegated")
    assert "attested by 2 of 3 arms" in cell
    assert "unattested-arms: 1 of 3 arms" in cell
    assert "unresolved" not in cell.lower()


def test_an_unresolved_side_whose_every_arm_attested_says_so_on_both_dimensions(tmp_path):
    # Three attestations, two arms placed. The old sentence split the difference and was wrong on
    # whichever dimension you read it for.
    cell = arm_cell(page_for(tmp_path, attest_terminal=False,
                             arms={"g-base-1": {"status": "blocked"}}), "defects")
    assert "attested by 3 of 3 arms" in cell
    assert "unresolved-arms: 1 of 3 arms" in cell
    assert "unattested-arms" not in cell


def test_both_unknown_causes_are_stated_without_either_proving_the_other(tmp_path):
    cell = arm_cell(page_for(tmp_path, attest_terminal=False,
                             arms={"g-base-1": {"status": "blocked"}},
                             escaped={"g-base-2": None}), "defects")
    assert "attested by 2 of 3 arms" in cell
    assert "unattested-arms: 1 of 3 arms" in cell
    assert "unresolved-arms: 1 of 3 arms" in cell
    assert 'class="unknown"' in cell


def test_a_fully_known_defect_population_shows_its_total_and_its_denominator(tmp_path):
    cell = arm_cell(page_for(tmp_path), "defects")
    assert 'class="unknown"' not in cell
    assert "attested by 3 of 3 arms" in cell
    assert "unattested-arms" not in cell and "unresolved-arms" not in cell


def test_the_acceptance_qualifier_counts_placement_and_names_the_unresolved_arms(tmp_path):
    cell = arm_cell(page_for(tmp_path, attest_terminal=False,
                             arms={"g-base-1": {"status": "blocked"}}), "acceptance")
    assert "2 of 3 arms placed" in cell
    assert "1 unresolved" in cell
    assert "attested" not in cell


def test_a_fully_placed_side_states_its_whole_population(tmp_path):
    cell = arm_cell(page_for(tmp_path), "acceptance")
    assert "3 of 3 arms" in cell or "accepted of 3 arms" in cell
    assert "unresolved-arms" not in cell


# --- the page names the coverage cause it actually gated on ---------------------------------

def test_the_page_names_allocation_outside_scope_not_an_unresolved_root(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"allocation": False}})
    assert "allocated to a source outside" in page
    assert "leaves a requested root unresolved" not in page


def test_the_page_names_the_unresolved_root_when_that_is_the_cause(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"unknown_root": True}})
    assert "leaves a requested root unresolved" in page
    assert "allocated to a source outside" not in page


def test_the_page_names_the_legacy_coverage_unknown_cause(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"allocation": None}})
    # the apostrophe is escaped in the page, so match either side of it
    assert "predates the engine" in page and "allocation-coverage verdict" in page


def test_no_producer_prose_or_lineage_text_reaches_the_comparison_page(tmp_path):
    from tests.test_goals_report import MEANING
    page = page_for(tmp_path, arms={"g-base-1": {"allocation": False}})
    assert MEANING not in page
    assert "not metadata-verified" not in page


# --- the coverage grid must name the question its cell answers -------------------------------
# The cell is G1's COMBINED roots-and-allocation boolean. Under a header naming roots only, an arm
# that resolved every one of its roots was told, in a table, that it did not.

def price_grid(page):
    block = page[page.index('id="price"'):page.index("</section>", page.index('id="price"'))]
    table = block[block.index("<table"):block.index("</table>")]
    return table


def grid_headers(page):
    return re.findall(r'<th scope="col">(.*?)</th>', price_grid(page), re.S)


def grid_rows(page):
    body = price_grid(page)
    body = body[body.index("<tbody>"):]
    return [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            for row in body.split("<tr>") if "<td" in row]


def cell_text(cell):
    return re.sub(r"<[^>]+>", "", cell).strip()


def test_the_coverage_column_does_not_claim_to_be_about_roots_alone(tmp_path):
    headers = grid_headers(page_for(tmp_path))
    assert "covers every root" not in headers
    assert any("whole source scope" in h for h in headers), headers


def test_an_arm_that_resolved_every_root_but_lost_allocation_reads_no_truthfully(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"allocation": False}})
    headers = grid_headers(page)
    column = headers.index([h for h in headers if "whole source scope" in h][0])
    answers = {cell_text(row[1]): cell_text(row[column]) for row in grid_rows(page)}
    assert answers["baseline"] == "no"
    # ...and the reader is never told a root went unresolved, because none did.
    assert "covers every root" not in page
    assert "leaves a requested root unresolved" not in page


def test_an_unresolved_root_also_reads_no_under_the_same_heading(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"unknown_root": True}})
    headers = grid_headers(page)
    column = headers.index([h for h in headers if "whole source scope" in h][0])
    answers = {cell_text(row[1]): cell_text(row[column]) for row in grid_rows(page)}
    assert answers["baseline"] == "no"


def test_a_clean_comparison_reads_yes_under_the_same_heading(tmp_path):
    page = page_for(tmp_path)
    headers = grid_headers(page)
    column = headers.index([h for h in headers if "whole source scope" in h][0])
    for row in grid_rows(page):
        assert cell_text(row[column]) == "yes", row


def test_the_grid_explains_what_the_whole_scope_answer_requires(tmp_path):
    page = page_for(tmp_path)
    block = page[page.index('id="price"'):page.index("</section>", page.index('id="price"'))]
    intro = block[:block.index("<table")]
    assert "requested root" in intro
    assert "allocation" in intro


def test_the_explanation_sits_outside_the_scrolling_region(tmp_path):
    page = page_for(tmp_path)
    block = page[page.index('id="price"'):page.index("</section>", page.index('id="price"'))]
    assert block.index("whole source scope") < block.index('<div class="scroll"') or \
        block.index("requested root") < block.index('<div class="scroll"')


def test_every_grid_cell_still_carries_its_column_label_for_a_phone(tmp_path):
    page = page_for(tmp_path)
    headers = grid_headers(page)
    body = price_grid(page)
    body = body[body.index("<tbody>"):]
    for row in body.split("<tr>"):
        if "<td" not in row:
            continue
        labels = re.findall(r'<td data-label="([^"]+)">', row)
        assert len(labels) == len(headers)
        assert any("whole source scope" in label for label in labels), labels


def test_the_renamed_column_is_escaped_like_every_other_header(tmp_path):
    page = page_for(tmp_path)
    for header in grid_headers(page):
        assert "<" not in header and ">" not in header, header


def test_no_raw_root_or_source_identifier_appears_in_the_grid(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"allocation": False}})
    grid = price_grid(page)
    assert "root-" not in grid
    assert ".jsonl" not in grid


def test_the_grid_intro_names_all_three_conditions_of_the_boolean_it_explains(tmp_path):
    """The cell is `roots resolved AND allocation complete AND rows present`. An explanation
    naming two of the three sends a reader hunting for a problem that may not exist."""
    page = page_for(tmp_path, arms={"g-base-1": {"rowless": True}})
    block = page[page.index('id="price"'):page.index("</section>", page.index('id="price"'))]
    intro = block[:block.index("<table")]
    assert "which of the three" in intro
    assert "which of the two" not in intro
    assert "requested root" in intro
    assert "allocation coverage" in intro
    assert "priced row" in intro


def test_a_rowless_arm_is_explained_by_the_sentence_above_its_own_grid(tmp_path):
    page = page_for(tmp_path, arms={"g-base-1": {"rowless": True}})
    assert "no-priced-rows" in page
    block = page[page.index('id="price"'):page.index("</section>", page.index('id="price"'))]
    assert "priced row" in block[:block.index("<table")]

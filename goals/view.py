# ABOUTME: Renders one goal report as a self-contained read-only HTML document. Every value is
# ABOUTME: escaped, every link is relative or https, and an unknown is drawn as an unknown.

from __future__ import annotations

import html
import re
from pathlib import Path

from . import references

TEMPLATE = Path(__file__).with_name("view.html")
# ONE pass over the template, so a substituted VALUE is never rescanned for tokens. No ordering of
# repeated `str.replace` calls can achieve that: the old chain expanded a token that arrived inside
# a goal title, and filled a second {{BODY}} living in the template's own comment, so every page
# carried a hidden second copy of the whole report.
SLOT = re.compile(r"\{\{([A-Z_]+)\}\}")

SYNTHETIC_LABEL = "SYNTHETIC EXAMPLE"
PREVIEW_LABEL = "PREVIEW"
UNKNOWN = '<span class="unknown">unknown</span>'

# What the reader is looking at, per section. This is the one structural device the page uses,
# and it carries the distinction the whole report exists to keep: measured, attested, configured.
EYEBROWS = {
    "outcome": "attested - not re-verified here",
    "attention": "attested - this goal alone",
    "usage": "measured upstream",
    "price": "allocated - not cash",
    "scope": "requested scope",
    "uncertainty": "what this cannot tell you",
    "budget": "configured policy",
}

# The column headers, and nothing else. There was a second "stacked label" per column, emitted as
# `data-label` for a phone reader - but this page has no `content: attr(data-label)` rule and keeps
# `.cell-label` display:none at every width, so nothing ever rendered it and assistive technology
# does not read attributes. What names a column here is the VISIBLE header row, which scrolls with
# the table inside its region.
ATTENTION_COLUMNS = (
    "day",
    "required",
    "required minutes",
    "optional",
    "over checkpoint cap, this goal alone",
    "over minute cap, this goal alone",
)

CAUSE_TEXT = {
    "rate-missing": "a model or cache rate is missing from the price table, so the subtotal sits "
                    "BELOW the truth",
    "unknown-ttl": "a cache write could not be placed on a TTL, so it is priced nowhere",
    "count-conflict": "a turn's own counts contradict each other, so neither reading was billed",
}


def esc(value):
    return html.escape(str(value), quote=True)


def _number(value):
    return f"{value:,}" if isinstance(value, int) else esc(value)


def _value_or_unknown(value, *, formatter=_number):
    return UNKNOWN if value is None else formatter(value)


def _tile(label, value, note=None, tile_id=None):
    ident = f' id="{esc(tile_id)}"' if tile_id else ""
    parts = [f'<div class="tile"{ident}>',
             f'<span class="tile-label">{esc(label)}</span>',
             f'<span class="tile-value">{value}</span>']
    if note:
        parts.append(f'<span class="tile-note">{esc(note)}</span>')
    parts.append("</div>")
    return "".join(parts)


def _cell(value, label=None):
    """ONE box per cell: the value and, where a header row is not beside it, the label that names
    it. The label is a real element rather than generated content, so it is read like any other
    text - CSS `content:` is not reliably exposed as text at all."""
    head = f'<span class="cell-label">{esc(label)}</span>' if label else ""
    return f'<span class="cell">{head}{value}</span>'


def _scroll(label, table, intro=None):
    """A table too wide for a phone SCROLLS; it is never turned into a stack of blocks.

    `display: block` on a table drops the implicit table roles in every major engine, so the header
    row stops being a header for assistive technology however carefully it is hidden. A focusable
    region keeps the real table and reaches every cell by keyboard.

    `intro` is rendered OUTSIDE the region: a sentence inside a 560px-wide table scrolls away with
    it, so the explanation of the scroll would itself be the thing you had to scroll to read.
    """
    lead = f'<p class="note">{intro}</p>' if intro else ""
    return (f'{lead}<div class="scroll" role="region" tabindex="0" aria-label="{esc(label)}">'
            f"{table}</div>")


def _facts(pairs):
    body = "".join(f"<dt>{esc(name)}</dt><dd>{value}</dd>" for name, value in pairs)
    return f'<dl class="facts">{body}</dl>'


def _section(ident, heading, body):
    return (f'<section id="{ident}">'
            f'<p class="eyebrow">{esc(EYEBROWS[ident])}</p>'
            f"<h2>{esc(heading)}</h2>{body}</section>")


def _duration_cell(span):
    if span.get("known"):
        return f'{esc(span["human"])} <span class="tile-note">{esc(span["seconds"])}s</span>'
    reason = span.get("reason") or "not applicable"
    return f'{UNKNOWN} <span class="tile-note">{esc(reason)}</span>'


def _status_dot(state):
    return f'<span class="dot {state}" aria-hidden="true"></span>'


def _header(report):
    goal = report["goal"]
    source = report["source"]
    accepted = report["outcome"]["accepted"]
    state = "good" if accepted else ("warning" if report["outcome"]["status"] == "blocked" else "")
    banners = ""
    if goal["synthetic"]:
        banners += (f'<div class="banner">{esc(SYNTHETIC_LABEL)} - synthetic input. '
                    "Nothing here describes real work, real usage or real money.</div>")
    # The preview state is a URL parameter, so it can never be mistaken for a live acceptance:
    # it always announces itself. On synthetic input it repeats the synthetic label rather than
    # replacing it; on real input it says plainly that a preview is not an acceptance record -
    # calling a real report "synthetic" would be its own lie.
    preview_kind = f"{esc(SYNTHETIC_LABEL)}. " if goal["synthetic"] else ""
    banners += (f'<div class="banner" id="preview-banner">{esc(PREVIEW_LABEL)} - '
                f'{preview_kind}This view was opened with ?preview and is an example '
                "rendering, not an acceptance record.</div>")
    # The manifest names the ledger its evidence came from. It reached the JSON and never this
    # page, while the comparison page has printed it all along. Both fields are `_text`-validated
    # upstream and escaped here.
    source_label = source.get("evidence_source")
    evidence = ""
    if source_label:
        revision = source_label.get("revision")
        evidence = (f'<br>evidence source: {esc(source_label["label"])}'
                    + (f' @ {esc(revision)}' if revision else ""))
    sources = " &middot; ".join(
        f'{esc(entry["label"])} {entry["model_count"]} '
        f'{"model" if entry["model_count"] == 1 else "models"}'
        + (f' @ {esc(entry["at"])}' if entry.get("at") else " (undated)")
        for entry in source["prices_sources"]) or "no price table"
    return (
        '<header class="page">'
        '<p class="eyebrow">goal report</p>'
        f'<h1>{esc(goal["title"])}</h1>'
        f'<p><span class="chip">{_status_dot(state)}{esc(report["outcome"]["status"])}</span> '
        f'<span class="chip">{esc(goal["id"])}</span></p>'
        f"{banners}"
        f'<p class="provenance">snapshot {esc(source["snapshot_at"])} '
        '<span id="snapshot-age"></span><br>'
        f'engine cache v{esc(source["engine_cache_version"])} &middot; '
        f'time precision {esc(source["time_precision"])} &middot; '
        f'prices {esc(source["prices_sha256"])}<br>'
        f'price sources: {sources}<br>'
        # Two different fingerprints of two different things. Printing the object digest under the
        # word "snapshot sha256" made a reviewer's `sha256sum` disagree with the receipt.
        f'snapshot file sha256 '
        f'{esc(source["snapshot_sha256"]) if source["snapshot_sha256"] else UNKNOWN}<br>'
        f'<span class="tile-note">{esc(source["snapshot_sha256_note"])}</span><br>'
        f'canonical object sha256 {esc(source["snapshot_canonical_sha256"])}<br>'
        f'<span class="tile-note">{esc(source["snapshot_canonical_recipe"])}</span><br>'
        f'report generated {esc(report["generated_utc"])}{evidence}</p>'
        "</header>")


def _outcome_section(report):
    outcome = report["outcome"]
    time = report["time"]
    worker = time["worker_time"]
    body = _facts([
        ("Status", esc(outcome["status"])),
        ("Implementer", _value_or_unknown(outcome["implementer"], formatter=esc)),
        ("Verifier", _value_or_unknown(outcome["verifier"], formatter=esc)),
        ("Authorized", _value_or_unknown(time["authorized_utc"], formatter=esc)),
        ("Accepted", _value_or_unknown(outcome["accepted_utc"], formatter=esc)),
    ])
    if outcome["blocker"]:
        body += f'<div class="banner blocker"><strong>Blocked:</strong> {esc(outcome["blocker"])}</div>'

    body += '<div class="tiles">'
    body += _tile("Time to acceptance", _duration_cell(time["time_to_acceptance"]),
                  tile_id="time-to-acceptance")
    elapsed = time["elapsed_open"]
    # Not applicable is not unknown: an accepted goal is not missing an elapsed time, it has none.
    body += _tile("Elapsed, open work",
                  _duration_cell(elapsed) if elapsed["applicable"]
                  else f'<span class="tile-note">not applicable - {esc(elapsed["reason"])}</span>')
    body += _tile("Aggregate worker time",
                  f'{esc(worker["aggregate_known_human"])}',
                  note="summed across intervals; NOT wall-clock elapsed time")
    body += "</div>"

    notes = []
    open_count = worker["open_interval_count"]
    if open_count:
        notes.append(f'{open_count} worker interval{"" if open_count == 1 else "s"} '
                     f'{"has" if open_count == 1 else "have"} no end and '
                     f'{"is" if open_count == 1 else "are"} counted as unknown, never closed at now.')
    # Tri-state, and the page says which of the three it is - "they do not overlap" and "we cannot
    # tell" are different answers and only one of them is a claim.
    if worker["overlapping"] is True:
        notes.append("Worker intervals overlap, so this total can exceed the elapsed time and is "
                     "not an additive partition of phases.")
    elif worker["overlapping"] is None:
        notes.append(f'Whether the intervals overlap is UNKNOWN: {worker["overlap_reason"]}.')
    for note in notes:
        body += f'<p class="note">{esc(note)}</p>'

    if outcome["evidence"]:
        items = []
        for item in outcome["evidence"]:
            # The same predicate the report used, asked again here: the page can never link
            # something the JSON withheld, or withhold something the JSON exported.
            href = references.link_target(item["ref"])
            if href:
                ref = f'<a class="evidence-ref" href="{esc(href)}">{esc(href)}</a>'
            else:
                ref = (f'<span class="evidence-ref">reference withheld '
                       f'({esc(item["ref_kind"])})</span>')
            items.append(f'<li><span class="evidence-label">{esc(item["label"])}</span>{ref}</li>')
        body += f'<ul class="plain">{"".join(items)}</ul>'
    else:
        body += f'<p class="note">No evidence reference is cited. {UNKNOWN}</p>'
    body += ('<p class="note">Declared attestations copied from the manifest. This report re-ran, '
             "re-checked and re-verified none of them.</p>")
    return _section("outcome", "Outcome and evidence", body)


def _usage_section(report):
    usage = report["usage"]
    body = '<div class="tiles">'
    for label, key in (("Turns", "turns"), ("Input tokens", "input_tokens"),
                       ("Output tokens", "output_tokens"), ("Cache read", "cache_read_tokens"),
                       ("Cache write 5m", "cache_write_5m"), ("Cache write 1h", "cache_write_1h")):
        body += _tile(label, _number(usage[key]))
    body += "</div>"
    body += _facts([
        ("Session rows summed", _number(usage["rows"])),
        ("Cache write, unknown TTL", _number(usage["cache_write_unknown_ttl"])),
        ("Cache write, contradicted", _number(usage["cache_write_conflict"])),
        ("Turns withheld from pricing", _number(usage["withheld_turns"])),
        ("Turns priced at a long-context tier", _number(usage["tiered_turns"])),
        ("Distinct models seen", _number(usage["distinct_model_count"])),
        ("Reasoning tokens",
         _value_or_unknown(usage["reasoning_tokens"]["value"])
         + ' <span class="tile-note">already inside output tokens; never added again</span>'),
    ])
    if not usage["normalized_total_known"]:
        body += ('<p class="note">Normalized token totals are UNKNOWN: at least one turn\'s own '
                 "counts contradict each other, so no reading of it can be defended.</p>")
    return _section("usage", "Usage measured by the cost engine", body)


def _price_section(report):
    money = report["allocated_api_equivalent"]
    body = '<div class="tiles">'
    body += _tile(money["label"], f'${esc(money["display_usd"])}',
                  note="known priced subtotal, not a bill")
    body += _tile("Pricing complete", "yes" if money["pricing_complete"] else "no",
                  note=None if money["pricing_complete"] else "see the causes below")
    body += "</div>"
    body += _facts([
        # The unrounded sum can carry the engine's own binary-float artefacts. It is reproduced,
        # not cleaned up: the moment this report "tidies" an upstream figure it stops being a receipt.
        ("Unrounded sum", f'${esc(money["known_subtotal_usd"])}'
                          ' <span class="tile-note">as the engine wrote it, summed as decimals</span>'),
        ("Rows priced partially", _number(money["rows_partial"])),
        ("Models with no rate", _number(money["unpriced_model_count"])),
        ("Whole source scope covered",
         "yes" if money["whole_source_scope_covered"] else "no"),
        ("Attribution basis", esc(money["attribution_basis"])),
    ])
    if money["partial_reasons"]:
        causes = "".join(f'<li><span class="limit-code">{esc(cause)}</span>'
                         f'<span>{esc(CAUSE_TEXT.get(cause, cause))}</span></li>'
                         for cause in money["partial_reasons"])
        body += f'<ul class="plain limits">{causes}</ul>'
    body += ('<p class="note"><strong>Whole source scope covered</strong> answers yes only when '
             "every requested root resolved AND the engine's source allocation coverage is known "
             "and complete AND the snapshot carried at least one priced row; a no does not say "
             "which of the three fell short - the scope section below names that. It is never a "
             "statement about roots alone.</p>")
    if not report["usage"]["rows"]:
        body += ('<p class="note">This snapshot carried <strong>no priced rows</strong>. The '
                 "subtotal is $0.00 because summing no rows gives zero - that is an unknown, "
                 "not zero work and not free work.</p>")
    body += ('<p class="note">This is an allocated API-equivalent value recomputed from token '
             "counts by the upstream engine. It is not cash, not an invoice, and not proof of "
             "which goal produced the work.</p>")
    return _section("price", "Allocated API-equivalent", body)


def _allocation_line(allocation):
    """What the engine says about whether the selected sources hold all of their own work.

    Three answers drawn as three different things: complete, incomplete with its count, and - for a
    snapshot older than the verdict - unknown. Never a bar or a percentage: this is a count of
    shared message ids, and a meter would invite reading it as a fraction of the goal's work.
    """
    if not allocation["known"]:
        return (f'{UNKNOWN} <span class="tile-note">this snapshot predates the engine\'s '
                "allocation-coverage verdict, so it is not read as complete</span>")
    if allocation["complete"]:
        return ('complete <span class="tile-note">the selected sources hold the work allocated to '
                "them</span>")
    # Every word of this is G1's own. The engine's sentence is validated upstream of here and then
    # dropped, so nothing a producer wrote can reach the page through this row.
    count = allocation["allocated_elsewhere_ids"]
    return (f'incomplete <span class="tile-note">{_number(count)} shared message '
            f'id{"" if count == 1 else "s"} in the selected sources are allocated to a source '
            "OUTSIDE this scope and are counted there instead, once, corpus-wide. This is a count "
            "of shared message ids: not a producing-goal claim, and not a second monetary total. "
            "It is measured across the selected sources and is NOT clipped to any since/until day "
            "bounds. No money and no token count is missing from what was selected.</span>")


def _scope_section(report):
    scope = report["scope"]
    # `whole-session-roots` takes no day bounds at all, so an absent bound there is not an unknown.
    unbounded = f'<span class="tile-note">no bound - {esc(scope["mode"])}</span>'

    def bound(value):
        if value is not None:
            return esc(value)
        return unbounded if scope["mode"] == "whole-session-roots" else UNKNOWN

    money = report["allocated_api_equivalent"]
    body = _facts([
        ("Mode", esc(scope["mode"])),
        ("Ownership", esc(scope["ownership"])),
        ("Roots requested", _number(scope["roots_requested"])),
        ("Roots resolved", _number(scope["roots_resolved"])),
        ("Roots unknown", _number(scope["roots_unknown"])),
        # Two different questions, side by side and never merged: is what was selected fully
        # PRICED, and does the selection HOLD all of its own work? A scope can answer yes to the
        # first and no to the second, which is exactly the case this row exists to show.
        ("Pricing complete", "yes" if money["pricing_complete"] else "no"),
        ("Source allocation coverage", _allocation_line(scope["allocation_coverage"])),
        # The same words as the price section and the comparison grid. One boolean, one name.
        ("Whole source scope covered", "yes" if scope["coverage_complete"] else "no"),
        ("Since", bound(scope["since"])),
        ("Until", bound(scope["until"])),
        ("Lineage", esc(report["source"]["lineage"])),
    ])
    if scope["roots_unknown"]:
        body += (f'<p class="note">{scope["roots_unknown"]} requested root matched no transcript. '
                 "Its usage is UNKNOWN, not zero.</p>")
    return _section("scope", "Scope and coverage", body)


def _uncertainty_section(report):
    items = "".join(f'<li><span class="limit-code">{esc(item["code"])}</span>'
                    f'<span>{esc(item["text"])}</span></li>'
                    for item in report["limitations"])
    return _section("uncertainty", "What this report cannot tell you",
                    f'<ul class="plain limits">{items}</ul>')


def _attention_section(report):
    attention = report["attention"]
    rows = []
    for day in attention["days"]:
        minutes = _number(day["required_minutes_known"])
        if day["required_minutes_unknown_count"]:
            minutes += f' + {day["required_minutes_unknown_count"]}&times;{UNKNOWN}'
        # Both caps get a column. The minute one is a tri-state: a day whose minutes are partly
        # unknown has no answer, and blanking it or printing "no" would both be claims.
        over_minutes = day["required_minutes_exceeds_cap_alone"]
        cells = [
            esc(day["date"]),
            _number(day["required_count"]),
            minutes,
            _number(day["optional_count"]),
            "yes" if day["required_checkpoints_exceeds_cap_alone"] else "no",
            UNKNOWN if over_minutes is None else ("yes" if over_minutes else "no"),
        ]
        # No `data-label` here: on this page `.cell-label` is display:none at every width and no
        # `content: attr(data-label)` rule exists, so the attribute rendered nothing and assistive
        # technology never read it. This table's header row stays VISIBLE and scrolls with the
        # table, which is what actually names each column.
        rows.append("<tr>" + "".join(f"<td>{_cell(value)}</td>" for value in cells) + "</tr>")
    head = "".join(f'<th scope="col">{esc(header)}</th>' for header in ATTENTION_COLUMNS)
    table = _scroll(
        "this goal's attention ledger",
        '<table class="wide"><caption>Local days in '
        f'{esc(attention["timezone"])}. Required and optional counted apart.</caption>'
        f"<thead><tr>{head}</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>',
        intro=(f'Caps are {esc(attention["required_checkpoints_per_day"])} checkpoint(s) and '
               f'{esc(attention["required_minutes_per_day"])} minute(s) per day. Six columns: the '
               "two cap answers sit to the right, and the table scrolls sideways on a narrow "
               "screen.")) if rows else \
        f'<p class="note">No intervention is recorded for this goal. {UNKNOWN} for the pilot.</p>'
    body = table + (f'<p class="note">Only days this goal recorded an intervention on appear '
                    f'above. A day that is absent is <strong>not recorded</strong> - '
                    f'{UNKNOWN} - never a day with zero attention; this report has no notion of '
                    "the goal's day span, so how many such days there were is unknown.</p>"
                    '<p class="note">These are THIS goal\'s contribution to each local day, never '
                    "a statement that the whole pilot stayed under its shared daily cap.</p>")
    return _section("attention", "Human attention", body)


def _budget_section(report):
    budget = report["budget"]
    body = _facts([
        ("Monthly LLM cap", f'${esc(budget["monthly_llm_cap_usd"])}'),
        ("Pilot incremental allowance", f'${esc(budget["pilot_incremental_cap_usd"])}'),
        ("Critical reserve", f'${esc(budget["critical_reserve_usd"])}'),
        ("Cash spent", f'{UNKNOWN} <span class="tile-note">{esc(budget["cash_status"])}</span>'),
        ("Cash headroom", f'{UNKNOWN} <span class="tile-note">{esc(budget["cash_status"])}</span>'),
    ])
    body += f'<p class="note">{esc(budget["note"])}</p>'
    return _section("budget", "Budget policy", body)


def render(report: dict) -> str:
    """Render the report as one self-contained HTML document. Read-only; nothing is fetched."""
    body = "".join([
        _header(report),
        _outcome_section(report),
        _usage_section(report),
        _price_section(report),
        _scope_section(report),
        _uncertainty_section(report),
        _attention_section(report),
        _budget_section(report),
        f'<footer>{esc(report["source"]["note"])}</footer>',
    ])
    values = {"TITLE": esc(report["goal"]["title"]),
              "SNAPSHOT_AT": esc(report["source"]["snapshot_at"]),
              "BODY": body}
    return _fill(TEMPLATE.read_text(encoding="utf-8"), values)


def _fill(template, values):
    """Substitute every slot in ONE pass, and require the template to declare each slot once.

    A value that happens to contain `{{BODY}}` is therefore literal data: `re.sub` never looks at
    what it just wrote. An unknown or missing slot is a template defect, so it is raised rather
    than rendered as a silent hole.
    """
    filled = {name: 0 for name in values}

    def take(match):
        name = match.group(1)
        if name not in values:
            raise RuntimeError(f"the report template names an unknown slot {{{{{name}}}}}")
        filled[name] += 1
        return values[name]

    page = SLOT.sub(take, template)
    for name, count in filled.items():
        if count != 1:
            raise RuntimeError(f"the report template must declare {{{{{name}}}}} exactly once, "
                               f"found {count}")
    return page

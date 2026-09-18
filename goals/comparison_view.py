# ABOUTME: Renders one delegation comparison as a self-contained read-only HTML document. The two
# ABOUTME: arms are drawn identically; direction is written in words and never encoded in a colour.

from __future__ import annotations

from pathlib import Path

from . import references
# The one-pass slot filler and the escape helper are this package's own, reused rather than
# re-implemented: a second copy of the substitution rule is how a template defect comes back.
from .view import UNKNOWN, _fill, esc

TEMPLATE = Path(__file__).with_name("comparison.html")

SYNTHETIC_LABEL = "SYNTHETIC EXAMPLE"

# What the reader is looking at, per section. Same structural device as the single-goal report,
# with the epistemic classes this document actually has.
EYEBROWS = {
    "posture": "measurement posture - never an approval",
    "classes": "classified against the frozen horizon",
    "elapsed": "how elapsed time is defined here",
    "attention": "attested - the whole pilot",
    "budget": "attested - not a provider check",
    "price": "allocated - not cash",
    "uncertainty": "what this cannot tell you",
    "sources": "private input fingerprints",
}

POSTURE_TEXT = {
    "not-started": "The protocol window has not opened. Nothing is being compared yet.",
    "observing": "Arms are still pending before the horizon. Observation continues; no direction "
                 "is claimed.",
    "inconclusive": "The evidence does not settle the question either way. That is not a negative "
                    "verdict and not a promotion.",
    "candidate": "Every gate this protocol defines is met by the observed evidence.",
    "not-candidate": "The evidence is complete and the comparison went the other way.",
}

# Seven answers, and never an eighth. Each is spelled out so a count is never read from a colour.
CLASSIFICATION_ROWS = (
    ("accepted", "Accepted inside the horizon"),
    ("failed", "Failed, attested"),
    ("blocked", "Blocked, attested"),
    ("abandoned", "Abandoned, attested"),
    ("censored", "Censored at the cutoff"),
    ("pending", "Pending"),
    ("unresolved", "Unresolved"),
)

GATE_ANSWER = {True: "met", False: "not met", None: "unknown"}


def _number(value):
    return f"{value:,}" if isinstance(value, int) else esc(value)


def _or_unknown(value, *, formatter=_number):
    return UNKNOWN if value is None else formatter(value)


def _facts(pairs):
    body = "".join(f"<dt>{esc(name)}</dt><dd>{value}</dd>" for name, value in pairs)
    return f'<dl class="facts">{body}</dl>'


def _tile(label, value, note=None):
    parts = ['<div class="tile">', f'<span class="tile-label">{esc(label)}</span>',
             f'<span class="tile-value">{value}</span>']
    if note:
        parts.append(f'<span class="tile-note">{esc(note)}</span>')
    parts.append("</div>")
    return "".join(parts)


def _scroll(label, table, intro=None):
    """A table too wide for a phone SCROLLS; it is never turned into a stack of blocks.

    `display: block` on a table drops the implicit table roles in every major engine, so the
    header row stops being a header for assistive technology however carefully it is hidden. A
    focusable scroll region keeps the real table and reaches every cell by keyboard.

    `intro` is rendered OUTSIDE the region: a sentence inside a 560px-wide table scrolls away with
    it, so the explanation of the scroll would itself be the thing you had to scroll to read.
    """
    lead = f'<p class="note">{esc(intro)}</p>' if intro else ""
    return (f'{lead}<div class="scroll" role="region" tabindex="0" aria-label="{esc(label)}">'
            f"{table}</div>")


def _section(ident, heading, body):
    return (f'<section id="{ident}">'
            f'<p class="eyebrow">{esc(EYEBROWS[ident])}</p>'
            f"<h2>{esc(heading)}</h2>{body}</section>")


def _codes(entries):
    items = "".join(f'<li><span class="limit-code">{esc(item["code"])}</span>'
                    f'<span>{esc(item["text"])}</span></li>' for item in entries)
    return f'<ul class="plain limits">{items}</ul>' if entries else ""


def _reference_cell(entry):
    """The same predicate the JSON asked, asked again: the page can never link what was withheld."""
    if entry.get("ref_kind") == "absent":
        return f'{UNKNOWN} <span class="tile-note">no reference cited</span>'
    href = references.link_target(entry.get("ref"))
    if href:
        return f'<a href="{esc(href)}">{esc(href)}</a>'
    return f'<span class="tile-note">reference withheld ({esc(entry.get("ref_kind"))})</span>'


def _header(comparison):
    protocol = comparison["protocol"]
    window = protocol["window"]
    sources = comparison["sources"]
    banners = ""
    if comparison["synthetic"]:
        banners += (f'<div class="banner">{esc(SYNTHETIC_LABEL)} - synthetic input. Nothing here '
                    "describes real work, real usage or real money.</div>")
    return (
        '<header class="page">'
        '<p class="eyebrow">delegation comparison</p>'
        f'<h1>{esc(protocol["cohort_id"])}</h1>'
        f'<p><span class="posture">{esc(comparison["status"])}</span> '
        f'<span class="chip">{esc(protocol["changed_dimension"])}</span> '
        f'<span class="chip">{_number(protocol["pair_count"])} pairs / '
        f'{_number(protocol["arm_count"])} arms</span></p>'
        f"{banners}"
        f'<p class="provenance">observed to {esc(sources["as_of_utc"])} '
        '<span id="as-of-age"></span><br>'
        f'window {esc(window["start_utc"])} &rarr; {esc(window["end_utc"])} '
        f'({_number(window["horizon_hours"])}h horizon)<br>'
        f'frozen {esc(protocol["created_utc"])} &middot; local days in '
        f'{esc(protocol["timezone"])}<br>'
        f'baseline {esc(protocol["baseline_value"])} &middot; '
        f'delegated {esc(protocol["delegated_value"])}<br>'
        f'generated {esc(comparison["generated_utc"])}</p>'
        "</header>")


def _posture_section(comparison):
    body = _facts([
        ("Posture", f'<span class="posture">{esc(comparison["status"])}</span>'),
        ("What that means", esc(POSTURE_TEXT[comparison["status"]])),
        ("Classes", " &middot; ".join(f'{esc(entry["id"])}: {esc(entry["status"])}'
                                      for entry in comparison["classes"])),
    ])
    body += f'<div class="banner plain">{esc(comparison["promotion"]["note"])}</div>'
    body += "<h3>Reason codes</h3>"
    body += _codes(comparison["rationale"]) or '<p class="note">No reason code applies.</p>'
    return _section("posture", "Decision posture", body)


def _cell(value, note=None, label=None, note_html=None):
    """ONE box per cell: a value, the label that names it at phone width, and the qualifier that
    makes it readable. They are the same answer, so they never come apart - and they are real
    elements, not generated content, so assistive technology reads them like any other text.

    `note` may be a list, in which case each entry gets its own line: a 120px column cannot wrap
    `change_kind=parser · scope_band=small` anywhere but mid-word. `note_html` is the one escape
    hatch, for a note that carries markup of its own; its caller escapes every piece it builds.
    """
    head = f'<span class="cell-label">{esc(label)}</span>' if label else ""
    notes = [note] if isinstance(note, str) else list(note or [])
    tail = "".join(f'<span class="tile-note">{esc(one)}</span>' for one in notes if one)
    tail += "".join(f'<span class="tile-note">{one}</span>' for one in (note_html or []) if one)
    return f'<span class="cell">{head}{value}{tail}</span>'


def _matched_note(name, value):
    """`change_kind=parser` has no break opportunity, so in a narrow column a browser breaks it
    mid-word. One INTENTIONAL opportunity at the delimiter is all it needs: the key and the value
    each stay whole and the pair splits where a reader would split it. `nowrap` would instead push
    the column past the viewport, and truncating would hide the value outright."""
    return f"{esc(name)}=<wbr>{esc(value)}"


def _rate(block, key, suffix=""):
    """A rate whose population holds an arm nobody could place is UNKNOWN, never a percentage a
    reader will compare against the other side. The reason travels in the note beside it."""
    if block[key] is None:
        return UNKNOWN
    return f'{esc(block[key])}{suffix}'


def _acceptance_note(block):
    """Acceptance is unknown for exactly one reason - arms nobody could place - so its population
    is the PLACED one, and the arms that were not placed are named as such."""
    clauses = [f'{block["accepted"]} accepted of {block["arm_count"]} arms']
    if block["unresolved_arm_count"]:
        clauses.append(f'{block["reason"]}: {block["known_arm_count"]} of {block["arm_count"]} '
                       f'arms placed, {block["unresolved_arm_count"]} unresolved')
    return clauses


def _defect_note(block):
    """The defect measure has its OWN population, and it is the ATTESTED one.

    Borrowing acceptance's placed count printed `unattested-arms: 3 of 3 arms attested` - a
    sentence that denies the very reason it is giving, and the one number that would make the
    `unknown` beside it read as a bug rather than a finding. Each cause now states the dimension
    it is actually about, and when both apply both are stated: neither population proves the other.
    """
    total, attested, arms = block["total_escaped"], block["attested_arms"], block["arm_count"]
    clauses = [f"{total} escaped, attested by {attested} of {arms} arms"]
    unattested = arms - attested
    if unattested:
        clauses.append(f"unattested-arms: {unattested} of {arms} arms attested no count")
    if block["unresolved_arm_count"]:
        clauses.append(f'unresolved-arms: {block["unresolved_arm_count"]} of {arms} arms never '
                       "reached an outcome")
    return clauses


def _arm_row(label, metric, values, notes=None):
    notes = notes or {}
    cells = ""
    for side in ("baseline", "delegated"):
        cells += (f'<td data-arm="{side}" data-metric="{metric}" data-label="{side}">'
                  f"{_cell(values[side], notes.get(side), side)}</td>")
    return f'<tr><td data-label="metric">{esc(label)}</td>{cells}</tr>'


def _defect_value(defects):
    """A total over the arms that attested. When some arm did not attest - or never reached an
    outcome at all - the headline is the chip, because a bare `0` beside such an arm reads as "no
    defects", which nobody established. The attested total stays in the note beside it."""
    if defects["reason"] or not defects["attested_arms"]:
        return UNKNOWN
    return _number(defects["total_escaped"])


def _class_block(entry, protocol):
    arms = entry["arms"]
    head = (f'<h3>{esc(entry["id"])} &middot; {esc(entry["status"])}</h3>'
            f'<p class="note">{_number(entry["pair_count"])} pair(s), '
            f'{_number(entry["fully_observed_pairs"])} fully observed. Matched on '
            f'{esc(", ".join(entry["matching_fields"]))}.</p>')

    rows = ""
    for key, label in CLASSIFICATION_ROWS:
        rows += _arm_row(label, key, {side: _number(arms[side]["classification"][key])
                                      for side in ("baseline", "delegated")})
    rows += _arm_row("Acceptance rate", "acceptance", {
        side: _rate(arms[side]["acceptance"], "rate_percent", "%")
        for side in ("baseline", "delegated")}, {
        side: _acceptance_note(arms[side]["acceptance"])
        for side in ("baseline", "delegated")})
    # The counts line travels IN the cell; the full sentence sits under the table. Neither the
    # figure nor the reader is ever handed a median without every outcome behind it.
    rows += _arm_row("Accepted-only median latency", "median", {
        side: _or_unknown(arms[side]["latency"]["median_human"], formatter=esc)
        for side in ("baseline", "delegated")}, {
        side: arms[side]["latency"]["counts_summary"] for side in ("baseline", "delegated")})
    rows += _arm_row("Escaped defects, attested", "defects", {
        side: _defect_value(arms[side]["defects"]) for side in ("baseline", "delegated")}, {
        side: _defect_note(arms[side]["defects"])
        for side in ("baseline", "delegated")})
    rows += _arm_row("Allocated API-equivalent", "allocated", {
        side: f'${esc(arms[side]["allocated_api_equivalent"]["display_usd"])}'
        for side in ("baseline", "delegated")}, {
        side: "known priced subtotal, not cash" for side in ("baseline", "delegated")})

    table = _scroll(
        f"the {entry['id']} arm ledger",
        f'<table class="ledger"><caption>Both arms are drawn identically; which one is ahead '
        f'is written out in the gates below, never in a colour.</caption>'
        f'<thead><tr><th scope="col">measure</th>'
        f'<th scope="col">baseline &middot; {esc(protocol["baseline_value"])}</th>'
        f'<th scope="col">delegated &middot; {esc(protocol["delegated_value"])}</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>")
    for side in ("baseline", "delegated"):
        table += (f'<p class="note">{esc(side)}: '
                  f'{esc(arms[side]["latency"]["qualification"])}</p>')

    gates = "".join(
        f'<li><span class="limit-code">{esc(GATE_ANSWER[gate["met"]])}</span>'
        f'<span>{esc(gate["text"])}</span></li>' for gate in entry["gates"])
    gate_list = f'<ul class="plain limits">{gates}</ul>'

    pair_rows, reasons = "", {}
    for row in entry["pairs"]:
        cells = ""
        for side in ("baseline", "delegated"):
            cell = row[side]
            # An unresolved arm carries its reason CODE in the same box as the word, so it can
            # never be skimmed as one more clean outcome. The sentence behind the code is spelled
            # out under the table, where there is room to read it.
            note = cell["classification_reason"] or cell["latency_human"]
            if cell["classification_reason_text"]:
                reasons[cell["classification_reason"]] = cell["classification_reason_text"]
            cells += (f'<td data-arm="{side}" data-metric="pair" data-label="{side}">'
                      f'{_cell(esc(cell["classification"]), note, side)}</td>')
        matched = [_matched_note(name, value) for name, value in sorted(row["matched"].items())]
        pair_rows += (f'<tr><td data-label="pair">{_cell(esc(row["id"]), note_html=matched)}</td>'
                      f"{cells}</tr>")
    pair_table = _scroll(
        f"the {entry['id']} pair outcomes",
        f'<table class="ledger"><caption>Every pair, and how each arm ended against the '
        f'horizon. A censored or attested-terminal arm never deletes its pair; an unresolved one '
        f'leaves it unobserved.</caption>'
        f'<thead><tr><th scope="col">pair</th><th scope="col">baseline</th>'
        f'<th scope="col">delegated</th></tr></thead>'
        f"<tbody>{pair_rows}</tbody></table>")
    if reasons:
        pair_table += _codes([{"code": code, "text": text}
                              for code, text in sorted(reasons.items())])

    return (head + table + "<h3>Gates</h3>" + gate_list + "<h3>Reason codes</h3>"
            + (_codes(entry["rationale"]) or '<p class="note">No reason code applies.</p>')
            + pair_table)


def _classes_section(comparison):
    body = "".join(_class_block(entry, comparison["protocol"]) for entry in comparison["classes"])
    partition = comparison["source_roots"]
    body += _facts([
        ("Arms", _number(partition["arm_count"])),
        ("Distinct source fingerprints", _number(partition["fingerprint_count"])),
        ("Overlapping arms", _number(partition["overlapping_arm_pairs"])),
        ("Exact partition", "yes" if partition["partition_exact"] else "no"),
    ])
    body += f'<p class="note">{esc(partition["note"])}</p>'
    return _section("classes", "Arm by arm", body)


def _elapsed_section(comparison):
    elapsed = comparison["elapsed"]
    body = _facts([
        ("Measure", esc(elapsed["measure"])),
        ("Horizon ends", esc(elapsed["horizon_end_utc"])),
        ("Observed to", esc(elapsed["observation_cutoff_utc"])),
        ("An instant counts when", esc(elapsed["bounds"])),
        ("Censoring", esc(elapsed["censoring"])),
        ("Source precision", esc(elapsed["engine_time_precision"])),
    ])
    body += f'<p class="note">{esc(elapsed["note"])}</p>'
    return _section("elapsed", "What elapsed time means here", body)


def _attention_section(comparison):
    attention = comparison["attention"]
    columns = (("day", "day"), ("required", "required checkpoints"),
               ("required minutes", "required minutes"), ("optional", "optional"),
               ("over checkpoint cap", "over checkpoint cap"),
               ("over minute cap", "over minute cap"))
    rows = ""
    for day in attention["days"]:
        minutes = _number(day["required_minutes_known"])
        if day["required_minutes_unknown_count"]:
            minutes += f' + {day["required_minutes_unknown_count"]}&times;&nbsp;{UNKNOWN}'
        over_minutes = day["required_minutes_exceeds_cap"]
        cells = [esc(day["date"]), _number(day["required_count"]), minutes,
                 _number(day["optional_count"]),
                 "yes" if day["required_checkpoints_exceeds_cap"] else "no",
                 UNKNOWN if over_minutes is None else ("yes" if over_minutes else "no")]
        rows += "<tr>" + "".join(f'<td data-label="{esc(label)}">{_cell(value)}</td>'
                                 for (_, label), value in zip(columns, cells)) + "</tr>"
    head = "".join(f'<th scope="col">{esc(header)}</th>' for header, _ in columns)
    if rows:
        table = _scroll(
            "the pilot attention ledger",
            f'<table class="wide"><caption>Local days in {esc(attention["timezone"])}, '
            f'{esc(attention["scope"])}.</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>",
            intro=(f'Caps are {attention["required_checkpoints_per_day"]} checkpoint(s) and '
                   f'{attention["required_minutes_per_day"]} minute(s) per day. Six columns: the '
                   "two cap answers sit to the right, and the table scrolls sideways on a narrow "
                   "screen."))
    else:
        table = (f'<p class="note">No intervention is recorded for this pilot. {UNKNOWN} - '
                 "that is not a pilot with zero attention.</p>")
    caps = attention["within_caps"]
    body = table + _facts([
        ("Scope", esc(attention["scope"])),
        ("Days with a record", _number(attention["recorded_day_count"])),
        ("Days with a REQUIRED record",
         _number(attention["required_day_count"]) if attention["required_day_count"]
         else f'{_number(0)} <span class="tile-note">nothing establishes the required caps</span>'),
        ("Within the required caps",
         UNKNOWN if caps is None else ("yes" if caps else "no")),
        ("Days without a record", f'{UNKNOWN} <span class="tile-note">'
                                  f'{esc(attention["unrecorded_days_note"])}</span>'),
        ("Interventions", _number(attention["intervention_count"])),
        ("Identical duplicates dropped", _number(attention["deduped_count"])),
    ])
    body += f'<p class="note">{esc(attention["note"])}</p>'
    return _section("attention", "Human attention", body)


def _budget_section(comparison):
    admission = comparison["budget_admission"]
    controls = comparison["controls"]
    cash = comparison["cash"]
    policy = comparison["protocol"]["policy"]
    body = "<h3>Controls</h3>"
    body += _facts([
        ("Changed dimension", esc(controls["changed_dimension"])),
        ("Baseline", esc(controls["baseline_value"])),
        ("Delegated", esc(controls["delegated_value"])),
        ("Held the same", esc(", ".join(f"{name}={value}" for name, value
                                        in sorted(controls["shared_controls"].items())))
         or UNKNOWN),
        ("Controls attested", "yes" if controls["attested"] else UNKNOWN),
        ("Controls verified", "no"),
        ("Controls reference", _reference_cell(controls["evidence"])),
    ])
    body += f'<p class="note">{esc(controls["note"])}</p>'
    body += "<h3>Budget</h3>"
    body += _facts([
        ("Admission status", esc(admission["status"])),
        ("Attested", "yes" if admission["attested"] else "no"),
        ("Checked at", _or_unknown(admission["checked_utc"], formatter=esc)),
        ("Attestation reference", _reference_cell(admission["evidence"])),
        ("Provider checked", "no"),
        ("Monthly LLM cap", f'${esc(policy["monthly_llm_cap_usd"])}'),
        ("Pilot incremental allowance", f'${esc(policy["pilot_incremental_cap_usd"])}'),
        ("Critical reserve", f'${esc(policy["critical_reserve_usd"])}'),
        ("Cash spent", f'{UNKNOWN} <span class="tile-note">{esc(cash["status"])}</span>'),
        ("Cash headroom", f'{UNKNOWN} <span class="tile-note">{esc(cash["status"])}</span>'),
    ])
    body += f'<p class="note">{esc(admission["note"])}</p>'
    body += f'<p class="note">{esc(cash["note"])}</p>'
    return _section("budget", "Controls and budget", body)


def _price_section(comparison):
    body = '<div class="tiles">'
    for entry in comparison["classes"]:
        for side in ("baseline", "delegated"):
            money = entry["arms"][side]["allocated_api_equivalent"]
            body += _tile(f'{entry["id"]} / {side}', f'${esc(money["display_usd"])}',
                          note=money["label"])
    body += "</div>"
    # Three yes/no answers per arm side. As a fact list that is a dozen near-identical sentences
    # the eye cannot scan; as a grid the one `no` is the thing that stands out.
    # "covers every root" named ONE of the two conditions this cell reports. The value is G1's
    # COMBINED verdict, so an arm that resolved every one of its roots and fell short only on
    # allocation coverage was told, in a table, that it did not cover its roots. The heading now
    # names what the cell actually answers; WHICH of the two conditions failed stays in the
    # rationale and the limitations, where it already is.
    columns = ("class", "arm", "pricing complete", "whole source scope covered",
               "token totals known")
    rows = ""
    for entry in comparison["classes"]:
        for side in ("baseline", "delegated"):
            money = entry["arms"][side]["allocated_api_equivalent"]
            cells = [esc(entry["id"]), side,
                     "yes" if money["pricing_complete"] else "no",
                     "yes" if money["coverage_complete"] else "no",
                     "yes" if money["normalized_tokens_known"] else "no"]
            rows += "<tr>" + "".join(f'<td data-label="{esc(label)}">{_cell(value)}</td>'
                                     for label, value in zip(columns, cells)) + "</tr>"
    body += _scroll(
        "the coverage behind each subtotal",
        '<table class="wide"><caption>One row per class and arm.</caption><thead><tr>'
        + "".join(f'<th scope="col">{esc(label)}</th>' for label in columns)
        + f"</tr></thead><tbody>{rows}</tbody></table>",
        # THREE conditions, not two. The cell is `roots resolved AND allocation known-and-complete
        # AND at least one priced row`; naming two of them sends a reader hunting for a root or an
        # allocation problem that a rowless arm does not have. Same wording as the G1 page's twin.
        intro="Coverage behind each subtotal. An incomplete answer means the figure sits BELOW "
              "the truth, never that the work was free. Whole source scope covered answers yes "
              "only when an arm resolved every requested root AND its allocation coverage is "
              "known and complete AND its snapshot carried at least one priced row; a no does "
              "not say which of the three fell short - the rationale and the limitations below "
              "name that. The table scrolls sideways on a narrow screen.")
    body += ('<p class="note">Descriptive only. These are allocated API-equivalent values the '
             "upstream engine recomputed from token counts - not cash, not an invoice, and never "
             "used here as a spending admission.</p>")
    return _section("price", "Allocated API-equivalent", body)


def _uncertainty_section(comparison):
    return _section("uncertainty", "What this comparison cannot tell you",
                    _codes(comparison["limitations"]))


def _sources_section(comparison):
    sources = comparison["sources"]
    provenance = sources["report_provenance"]
    defects = sources["defect_evidence"]
    rows = [
        ("Cohort", esc(sources["cohort_id"])),
        ("Observed to", esc(sources["as_of_utc"])),
        ("Protocol file sha256", esc(sources["protocol_sha256"])),
        ("Ledger file sha256", esc(sources["observations_sha256"])),
    ]
    for oracle in sources["acceptance_oracles"]:
        rows.append((f'Acceptance oracle, {oracle["work_class"]}', _reference_cell(oracle)))
    for label in sources["evidence_labels"]:
        rows.append(("Evidence source", esc(label["label"])
                     + (f' <span class="tile-note">{esc(label["revision"])}</span>'
                        if label["revision"] else "")))
    terminal = sources["terminal_evidence"]
    rows += [
        ("Report bytes checked against the ledger", _number(provenance["bytes_frozen"])),
        ("Reports with raw-byte snapshot provenance", _number(provenance["raw_bytes"])),
        ("Reports built in memory", _number(provenance["in_memory_object"])),
        ("Defect attestations citing a reference", _number(defects["cited"])),
        ("Defect references withheld as unsafe", _number(defects["withheld"])),
        ("Terminal attestations citing a reference", _number(terminal["cited"])),
        ("Terminal references withheld as unsafe", _number(terminal["withheld"])),
    ]
    body = _facts(rows)
    body += f'<p class="note">{esc(sources["note"])}</p>'
    body += f'<p class="note">{esc(provenance["note"])}</p>'
    body += f'<p class="note">{esc(defects["note"])}</p>'
    body += f'<p class="note">{esc(terminal["note"])}</p>'
    return _section("sources", "Sources", body)


def render_comparison(comparison: dict) -> str:
    """Render the comparison as one self-contained HTML document. Read-only; nothing is fetched."""
    body = "".join([
        _header(comparison),
        _posture_section(comparison),
        _classes_section(comparison),
        _elapsed_section(comparison),
        _attention_section(comparison),
        _budget_section(comparison),
        _price_section(comparison),
        _uncertainty_section(comparison),
        _sources_section(comparison),
        f'<footer>{esc(comparison["promotion"]["note"])}</footer>',
    ])
    values = {"TITLE": esc(f'Delegation comparison - {comparison["protocol"]["cohort_id"]}'),
              "AS_OF": esc(comparison["sources"]["as_of_utc"]),
              "BODY": body}
    return _fill(TEMPLATE.read_text(encoding="utf-8"), values)

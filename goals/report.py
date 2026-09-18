# ABOUTME: Builds one sanitized goal report from a goal manifest plus ONE cost snapshot the
# ABOUTME: existing engine already emitted. It aggregates that snapshot; it never prices anything.

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import references

# The manifest schema this reporter understands. A different version is refused, never guessed.
SCHEMA_VERSION = 1

# The upstream contract. These are the repaired engine's own values; a structural or version
# mismatch is an error, because the alternative - falling back to Claude Code's own dollar
# payload - is a number we already know to be wrong.
SUPPORTED_COST_CACHE_VERSION = 6
SUPPORTED_TIME_PRECISION = "utc-day"
SUPPORTED_GROUP = "session"
REQUIRED_COST_FIELDS = ("at", "group", "engine", "prices", "scope", "lineageFrom",
                        "rows", "sessions", "unpriced")
ROW_COUNTERS = ("turns", "in", "out", "cacheRead", "cw5m", "cw1h", "cwUnknownTtl",
                "cwConflict", "withheldTurns", "tieredTurns")
REQUIRED_ROW_FIELDS = ROW_COUNTERS + ("key", "usd", "partial", "partialReasons", "models")
# Three different stories about an incomplete subtotal. A missing rate means the number sits
# BELOW the truth; contradictory counts mean the engine refused to guess in either direction;
# an unknown TTL means a cache write could not be placed on a rate. They never collapse into one.
PARTIAL_REASONS = ("count-conflict", "rate-missing", "unknown-ttl")

STATUSES = ("in_progress", "blocked", "accepted", "failed", "abandoned", "reopened")
OPEN_STATUSES = ("in_progress", "blocked", "reopened")
OWNERSHIPS = ("dedicated", "shared", "unknown")
SCOPE_MODES = ("whole-session-roots", "utc-days")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

MONEY_LABEL = "allocated API-equivalent"
CASH_STATUS = "UNKNOWN - not connected"

LIMITATION_TEXT = {
    "allocated-not-cash":
        "The money figure is an allocated API-equivalent recomputed from token counts by the "
        "upstream engine. It is not cash, not an invoice and not a bill.",
    "source-allocation-not-attribution":
        "Rows are a deterministic SOURCE ALLOCATION - which transcript a turn is counted under - "
        "never verified evidence of which goal produced the work.",
    "lineage-path-derived":
        "Descendant lineage comes from path nesting, not from verified metadata, so even complete "
        "pricing is not proof of the true producing goal.",
    "acceptance-is-attestation":
        "Outcome, implementer, verifier and evidence are declared attestations copied from the "
        "manifest. This report re-ran, re-checked and re-verified none of them.",
    "single-goal-contribution":
        "Attention figures are THIS goal's contribution to a local day, not a statement that the "
        "whole pilot stayed under its daily cap.",
    "coverage-incomplete-unknown-roots":
        "At least one requested root matched no transcript. Its usage is UNKNOWN, not zero, so the "
        "subtotal does not cover the whole requested scope.",
    "pricing-incomplete":
        "Pricing is incomplete for at least one row; the subtotal is a known priced subtotal, not a total.",
    "token-totals-unknown-conflict":
        "At least one turn's own counts contradict each other, so normalized token totals are unknown.",
    # Selection coverage, not price partialness. The money is right - each message is billed once,
    # corpus-wide, under its own source - but some of what these sources contain was billed under a
    # source OUTSIDE the selection, so the selection does not hold all of its own work.
    "source-allocation-outside-scope":
        "The selected source scope contains shared message ids whose deterministic global source "
        "allocation falls OUTSIDE it; they are counted there instead, once, corpus-wide. The count "
        "is a number of shared message ids, not a producing-goal claim and not a second monetary "
        "total. It is measured over the selected sources and is NOT clipped to any since/until day "
        "bounds. Pricing is unaffected: no money and no token count is missing from what was "
        "selected.",
    "source-allocation-coverage-unknown":
        "This snapshot predates the engine's allocation-coverage verdict, so whether the selected "
        "sources hold all of their own work is UNKNOWN. It is not read as complete, and scope "
        "coverage is reported as incomplete rather than true.",
    "no-priced-rows":
        "The engine resolved this scope and returned NO priced row for it. A scope with nothing in "
        "it is an unknown, not a measured zero: the subtotal below is $0.00 because summing no rows "
        "gives zero, and that is not evidence the work was free or that none was done. Source "
        "coverage and pricing completeness are both reported as incomplete.",
    "source-lineage-descriptor-withheld":
        "The snapshot describes its lineage with a value this report does not recognise. The text "
        "is producer-supplied and is withheld rather than printed, because it can carry a path or "
        "an identifier. Lineage is therefore reported as an unknown descriptor - qualified, not "
        "exact - and nothing else about the snapshot is affected.",
    "shared-scope-unqualified":
        "The scope is declared SHARED: more than this goal's work is inside it and no per-goal "
        "allocation is attempted or implied.",
    "ownership-unknown":
        "Scope ownership is declared UNKNOWN, so nothing here is qualified as this goal's own usage.",
    "acceptance-time-unknown":
        "The goal is accepted but carries no acceptance timestamp, so time to acceptance stays unknown.",
    "authorization-unknown":
        "No authorization timestamp was supplied. It is never inferred from a file time, a session "
        "timestamp or the current clock, so durations anchored on it stay unknown.",
    "time-to-acceptance-unordered":
        "The declared acceptance time precedes the declared authorization time. Neither is rewritten "
        "and the interval is not computed.",
    "worker-intervals-open":
        "At least one worker interval has no end. It is counted as unknown, never closed at now.",
    "worker-intervals-overlap":
        "Worker intervals overlap, so aggregate worker time is not an additive partition of phases "
        "and is not wall-clock elapsed time.",
    "worker-intervals-overlap-unknown":
        "Whether the worker intervals overlap is UNKNOWN: at least one has no end, so an overlap "
        "with it is possible and cannot be ruled out. Aggregate worker time is not a partition.",
    "required-minutes-unknown":
        "At least one required intervention carries no minutes. Missing minutes stay unknown and are "
        "never inferred from time spent waiting for a reply.",
    "evidence-ref-withheld":
        "At least one evidence reference was withheld from this report because it is a private "
        "absolute path or an unapproved URL scheme. Its label is retained.",
}

SOURCE_NOTE = (
    "One snapshot of the existing cost engine, grouped by session over an explicit root set. "
    "`session-cost` is not an alternative source for this report: its own-transcript scope is a "
    "different question from corpus allocation."
)


class ReportError(Exception):
    """Invalid or unsupported input. Carries a cause, never the offending payload."""


# --- small typed helpers ---------------------------------------------------------------

def _obj(value, what):
    if not isinstance(value, dict):
        raise ReportError(f"{what} must be a JSON object")
    return value


def _field(mapping, key, what):
    if key not in mapping:
        raise ReportError(f"{what} is missing the required field '{key}'")
    return mapping[key]


def _text(value, what, *, allow_none=False):
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f"{what} must be a non-empty string")
    return value


def _flag(value, what):
    if not isinstance(value, bool):
        raise ReportError(f"{what} must be true or false")
    return value


def _count(value, what):
    # A bool is an int in Python and would sail through a naive check; a count of `True` turns is
    # exactly the kind of upstream accident this reporter exists to refuse.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportError(f"{what} must be a non-negative integer, and never a boolean or a fraction")
    if value < 0:
        raise ReportError(f"{what} must be a non-negative integer")
    return value


def _number(value, what, *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ReportError(f"{what} must be a non-negative number")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ReportError(f"{what} must be a finite number")
        number = value
    elif isinstance(value, (int, float)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ReportError(f"{what} must be a finite number")
    else:
        raise ReportError(f"{what} must be a non-negative number")
    if number < 0:
        raise ReportError(f"{what} must be a non-negative number")
    return number


def _number_out(value):
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _money(value, what):
    # Money arrives as the JSON literal the engine wrote. A binary float has already lost the
    # decimal representation by the time we see it, so it is refused rather than rounded.
    if isinstance(value, bool) or isinstance(value, float):
        raise ReportError(
            f"{what} must arrive as a Decimal (load the snapshot with parse_float=Decimal), "
            "not as a binary float")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ReportError(f"{what} is not a valid decimal amount") from None
    else:
        raise ReportError(f"{what} is not a valid decimal amount")
    if not amount.is_finite():
        raise ReportError(f"{what} must be a finite decimal amount")
    if amount < 0:
        raise ReportError(f"{what} must not be negative")
    return amount


def _utc(value, what, *, allow_none=False):
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f"{what} must be a UTC timestamp string")
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ReportError(f"{what} is not a valid ISO-8601 timestamp") from None
    if moment.tzinfo is None:
        raise ReportError(f"{what} must carry an explicit UTC offset (a naive timestamp is ambiguous)")
    if moment.utcoffset() != timezone.utc.utcoffset(None):
        raise ReportError(f"{what} must be in UTC (Z or +00:00), not a local offset")
    return moment.astimezone(timezone.utc)


def _utc_out(moment):
    """Emit the instant with the precision it arrived at - padding it to microseconds would claim
    a precision the source never wrote."""
    if moment is None:
        return None
    text = moment.isoformat().replace("+00:00", "Z")
    if "." in text:
        head, fraction = text[:-1].split(".")
        fraction = fraction.rstrip("0")
        text = f"{head}.{fraction}Z" if fraction else f"{head}Z"
    return text


def _day(value, what, *, allow_none=True):
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not DAY_RE.match(value):
        raise ReportError(
            f"{what} must be a YYYY-MM-DD day: the engine's time precision is '{SUPPORTED_TIME_PRECISION}', "
            "so sub-day bounds are not supported")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ReportError(f"{what} is not a real calendar day") from None
    return value


# The engine's explanatory sentence is a PRODUCER-CONTRACT check and nothing more. No character
# class can tell prose from an identifier - a UUID, a bare root handle and a 64-hex digest are all
# "plain printable text" - so the sentence is validated for shape and then DROPPED. What this report
# says about coverage is G1's own fixed wording below, which cannot carry an id because it is a
# constant. Nothing here is sanitized and then exported.
MEANING_MAX = 400
MEANING_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# The exported vocabulary: fixed G1 constants, never producer text.
ALLOCATION_DESCRIPTIONS = {
    True: "selected-sources-hold-their-own-allocation",
    False: "selected-source-allocation-outside-scope",
    None: "source-allocation-coverage-unknown",
}
# The upstream counter is taken over the selected SOURCES and is not clipped to `since`/`until`, so
# a day-bounded report must not let a reader infer a per-window count.
ALLOCATION_COUNT_WINDOW = "selected-sources-all-dates"

# `lineageFrom` is producer text that used to be exported and rendered verbatim - an absolute path
# in it reached the page intact. Only the known engine contract value maps to a descriptor; any
# other string is withheld, and the report says so rather than refusing an otherwise good snapshot.
LINEAGE_DESCRIPTORS = {"path-nesting (not metadata-verified)": "path-nesting-unverified"}
LINEAGE_UNKNOWN = "unknown-lineage-descriptor"


def _meaning(value, what):
    """Check the producer's sentence and return nothing that will be exported.

    A bounded, control-free string is a signal about the producer's contract. It is not evidence
    that the text is identity-free, and it is rendered nowhere, so a path or an identifier inside it
    is a producer smell rather than a leak - and a cosmetic rewording upstream must not throw away
    an otherwise correct report.
    """
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f"{what} must be a non-empty explanatory string")
    text = value.strip()
    if len(text) > MEANING_MAX:
        raise ReportError(f"{what} is longer than {MEANING_MAX} characters; it is a sentence, "
                          "not a payload")
    if MEANING_CONTROL.search(text):
        raise ReportError(f"{what} must be printable text with no control characters")
    return None


def _allocation_coverage(scope):
    """The engine's SELECTION-coverage verdict: does the selected scope hold all of its own work?

    It is not price partialness, not a second money total and not evidence of a producing goal. A
    selection can be perfectly priced and still not contain everything allocated to it.

    An older snapshot has no verdict at all. That is reported as UNKNOWN and treated as incomplete,
    never as complete: reading a missing field as "yes" is how a legacy input quietly becomes a
    claim nobody made.
    """
    if "allocationCoverage" not in scope:
        return {"known": False, "complete": None, "allocated_elsewhere_ids": None,
                "description_code": ALLOCATION_DESCRIPTIONS[None], "count_window": None}
    raw = scope["allocationCoverage"]
    if not isinstance(raw, dict):
        raise ReportError("cost snapshot scope allocationCoverage must be a JSON object")
    complete = _flag(_field(raw, "complete", "cost snapshot scope allocationCoverage"),
                     "allocationCoverage complete")
    count = _count(_field(raw, "allocatedElsewhereIds", "cost snapshot scope allocationCoverage"),
                   "allocationCoverage allocatedElsewhereIds")
    # Checked, then dropped: what the report says is G1's own constant, not this string.
    _meaning(_field(raw, "meaning", "cost snapshot scope allocationCoverage"),
             "allocationCoverage meaning")
    # The two fields answer the same question and must agree; a "complete" verdict carrying a count
    # contradicts itself, and so does an incomplete one carrying none.
    if complete and count:
        raise ReportError("allocationCoverage says coverage is complete but counts "
                          f"{count} id(s) allocated elsewhere; the two contradict each other")
    if not complete and not count:
        raise ReportError("allocationCoverage says coverage is incomplete but counts no id "
                          "allocated elsewhere; the two contradict each other")
    return {"known": True, "complete": complete, "allocated_elsewhere_ids": count,
            "description_code": ALLOCATION_DESCRIPTIONS[complete],
            "count_window": ALLOCATION_COUNT_WINDOW}


def _source_day(value, what):
    """Price-table provenance dates itself either way: the shipped table writes a plain day, a
    fetched one writes a timestamp. Both are kept at the engine's own display precision - the day.
    """
    if value is None:
        return None
    if isinstance(value, str) and DAY_RE.match(value.strip()):
        return _day(value.strip(), what)
    moment = _utc(value, what)
    return moment.date().isoformat()


def _duration(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = [f"{days}d", f"{hours}h", f"{minutes}m"]
    return " ".join(parts[0 if days else (1 if hours else 2):])


# --- goal manifest -----------------------------------------------------------------------

def _read_goal(goal):
    _obj(goal, "the goal manifest")
    version = _field(goal, "schema_version", "the goal manifest")
    if version != SCHEMA_VERSION:
        raise ReportError(f"unsupported goal manifest schema_version {version!r}; this reporter reads {SCHEMA_VERSION}")

    tz_name = _text(_field(goal, "timezone", "the goal manifest"), "goal timezone")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ReportError(f"goal timezone {tz_name!r} is not a known IANA timezone") from None

    parsed = {
        "goal_id": _text(_field(goal, "goal_id", "the goal manifest"), "goal_id"),
        "title": _text(_field(goal, "title", "the goal manifest"), "title"),
        "timezone": tz_name,
        "tz": tz,
        "authorized_utc": _utc(goal.get("authorized_utc"), "authorized_utc", allow_none=True),
        "synthetic": _flag(goal.get("synthetic", False), "synthetic"),
        # The key the scope handles are derived under. It is read here and never written anywhere.
        "scope_fingerprint_salt": _salt(_field(goal, "scope_fingerprint_salt",
                                               "the goal manifest")),
        "scope": _read_scope(_obj(_field(goal, "scope", "the goal manifest"), "goal scope")),
        "outcome": _read_outcome(_obj(_field(goal, "outcome", "the goal manifest"), "goal outcome")),
        "worker_intervals": _read_intervals(goal.get("worker_intervals", [])),
        "interventions": _read_interventions(goal.get("interventions", [])),
        "policy": _read_policy(_obj(_field(goal, "policy", "the goal manifest"), "goal policy")),
        "evidence_source": _read_evidence_source(goal.get("evidence_source")),
    }
    return parsed


def _read_scope(scope):
    roots = _field(scope, "roots", "goal scope")
    if not isinstance(roots, list) or not roots:
        raise ReportError("goal scope roots must be a non-empty list of explicit root ids")
    for root in roots:
        _text(root, "each entry of goal scope roots")
    if len(set(roots)) != len(roots):
        raise ReportError("goal scope roots contains a duplicate root id")

    mode = _field(scope, "mode", "goal scope")
    if mode not in SCOPE_MODES:
        raise ReportError(f"goal scope mode must be one of {', '.join(SCOPE_MODES)}")
    since = _day(scope.get("since"), "goal scope since")
    until = _day(scope.get("until"), "goal scope until")
    if mode == "whole-session-roots" and (since or until):
        raise ReportError("goal scope mode 'whole-session-roots' takes no since/until bounds")
    if mode == "utc-days" and not (since or until):
        raise ReportError("goal scope mode 'utc-days' needs at least one of since/until")
    if since and until and since > until:
        raise ReportError("goal scope since is after until")

    ownership = _field(scope, "ownership", "goal scope")
    if ownership not in OWNERSHIPS:
        raise ReportError(f"goal scope ownership must be one of {', '.join(OWNERSHIPS)}")
    return {"roots": list(roots), "mode": mode, "since": since, "until": until, "ownership": ownership}


def _read_outcome(outcome):
    status = _field(outcome, "status", "goal outcome")
    if status not in STATUSES:
        raise ReportError(f"goal outcome status must be one of {', '.join(STATUSES)}")
    implementer = _text(outcome.get("implementer"), "goal outcome implementer", allow_none=True)
    verifier = _text(outcome.get("verifier"), "goal outcome verifier", allow_none=True)
    accepted_utc = _utc(outcome.get("accepted_utc"), "accepted_utc", allow_none=True)
    blocker = _text(outcome.get("blocker"), "goal outcome blocker", allow_none=True)

    raw_evidence = outcome.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ReportError("goal outcome evidence must be a list")
    evidence = []
    for item in raw_evidence:
        _obj(item, "each evidence entry")
        label = _text(_field(item, "label", "an evidence entry"), "evidence label")
        ref = _text(_field(item, "ref", "an evidence entry"), "evidence ref")
        # The page asks the same predicate before it links anything, so the two layers cannot
        # disagree about what is safe or about why something was withheld.
        kind, kept = references.classify(ref)
        evidence.append({"label": label, "ref": kept, "ref_kind": kind})

    if status == "accepted":
        if not implementer or not verifier:
            raise ReportError("an accepted goal must declare both an implementer and a verifier")
        if implementer == verifier:
            raise ReportError("an accepted goal needs a verifier distinct from its implementer")
        if not evidence:
            raise ReportError("an accepted goal must cite at least one evidence reference")
    return {"status": status, "implementer": implementer, "verifier": verifier,
            "accepted_utc": accepted_utc, "evidence": evidence, "blocker": blocker}


def _read_intervals(intervals):
    if not isinstance(intervals, list):
        raise ReportError("worker_intervals must be a list")
    out = []
    for item in intervals:
        _obj(item, "each worker interval")
        actor = _text(_field(item, "actor", "a worker interval"), "worker interval actor")
        start = _utc(_field(item, "start_utc", "a worker interval"), "worker interval start_utc")
        end = _utc(item.get("end_utc"), "worker interval end_utc", allow_none=True)
        if end is not None and end < start:
            raise ReportError("a worker interval's end_utc precedes its start_utc")
        out.append({"actor": actor, "start": start, "end": end})
    return out


def _read_interventions(interventions):
    if not isinstance(interventions, list):
        raise ReportError("interventions must be a list")
    seen = {}
    deduped = 0
    for item in interventions:
        _obj(item, "each intervention")
        entry = {
            "id": _text(_field(item, "id", "an intervention"), "intervention id"),
            "at": _utc(_field(item, "at_utc", "an intervention"), "intervention at_utc"),
            "required": _flag(_field(item, "required", "an intervention"), "intervention required"),
            "minutes": _number(item.get("minutes"), "intervention minutes", allow_none=True),
        }
        previous = seen.get(entry["id"])
        if previous is None:
            seen[entry["id"]] = entry
        elif previous == entry:
            deduped += 1
        else:
            raise ReportError(f"intervention id {entry['id']!r} appears twice with different content")
    return {"entries": sorted(seen.values(), key=lambda e: (e["at"], e["id"])), "deduped": deduped}


def _read_policy(policy):
    out = {}
    for key in ("monthly_llm_cap_usd", "pilot_incremental_cap_usd", "critical_reserve_usd"):
        raw = _field(policy, key, "goal policy")
        if not isinstance(raw, str):
            raise ReportError(f"goal policy {key} must be a decimal string")
        _money(raw, f"goal policy {key}")
        out[key] = raw
    out["required_checkpoints_per_day"] = _count(
        _field(policy, "required_checkpoints_per_day", "goal policy"),
        "goal policy required_checkpoints_per_day")
    out["required_minutes_per_day"] = _number(
        _field(policy, "required_minutes_per_day", "goal policy"),
        "goal policy required_minutes_per_day")
    return out


def _read_evidence_source(source):
    if source is None:
        return None
    _obj(source, "evidence_source")
    return {"label": _text(_field(source, "label", "evidence_source"), "evidence_source label"),
            "revision": _text(source.get("revision"), "evidence_source revision", allow_none=True)}


# --- cost snapshot -----------------------------------------------------------------------

def _read_cost(cost):
    _obj(cost, "the cost snapshot")
    for key in REQUIRED_COST_FIELDS:
        _field(cost, key, "the cost snapshot")

    group = cost["group"]
    if group != SUPPORTED_GROUP:
        raise ReportError(f"the cost snapshot must be grouped by '{SUPPORTED_GROUP}', not {group!r}")

    engine = _obj(cost["engine"], "cost snapshot engine")
    version = _field(engine, "costCacheVersion", "cost snapshot engine")
    if version != SUPPORTED_COST_CACHE_VERSION:
        raise ReportError(
            f"unsupported engine costCacheVersion {version!r}; this reporter reads "
            f"{SUPPORTED_COST_CACHE_VERSION}. Re-run the cost engine rather than reading an older snapshot.")
    precision = _field(engine, "timePrecision", "cost snapshot engine")
    if precision != SUPPORTED_TIME_PRECISION:
        raise ReportError(f"unsupported engine timePrecision {precision!r}; this reporter reads "
                          f"{SUPPORTED_TIME_PRECISION}")

    prices = _obj(cost["prices"], "cost snapshot prices")
    fingerprint = _text(_field(prices, "sha256", "cost snapshot prices"), "cost snapshot prices sha256")
    sources = _obj(_field(prices, "sources", "cost snapshot prices"), "cost snapshot prices sources")
    price_sources = []
    for label, meta in sorted(sources.items()):
        _obj(meta, "each cost snapshot price source")
        # `from` is a local path on the machine that produced the snapshot. The label, the date and
        # the model count are what a reader needs; the path is private and stays behind.
        price_sources.append({"label": _text(label, "a price source label"),
                              "at": _source_day(meta.get("at"), f"price source {label} at"),
                              "model_count": _count(meta.get("count", 0), f"price source {label} count")})

    scope = _obj(cost["scope"], "cost snapshot scope")
    roots = scope.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ReportError("the cost snapshot must carry the explicit scope roots it was run for")
    for root in roots:
        _text(root, "each cost snapshot scope root")
    if len(set(roots)) != len(roots):
        raise ReportError("the cost snapshot scope roots contains a duplicate root id")
    if scope.get("repo") is not None:
        raise ReportError("the cost snapshot carries a repo selector; this report takes an explicit "
                          "root set, and a repo scope answers a different question")
    resolved = scope.get("resolvedRoots") or []
    unknown = scope.get("unknownRoots") or []
    for name, value in (("resolvedRoots", resolved), ("unknownRoots", unknown)):
        if not isinstance(value, list):
            raise ReportError(f"cost snapshot scope {name} must be a list")
        for root in value:
            _text(root, f"each cost snapshot scope {name} entry")
        if len(set(value)) != len(value):
            raise ReportError(f"cost snapshot scope {name} contains a duplicate root id")
    # Coverage is only meaningful if every requested root is accounted for exactly once. Without
    # this, a root that is in neither list is silently dropped and the report claims COMPLETE
    # coverage over work nobody priced - the one mistake this reporter exists to refuse.
    requested, found, missing = set(roots), set(resolved), set(unknown)
    both = found & missing
    if both:
        raise ReportError(f"{len(both)} root(s) appear in both resolvedRoots and unknownRoots; "
                          "a root is resolved or unknown, never both")
    if found - requested:
        raise ReportError("cost snapshot scope resolvedRoots names a root that was not requested")
    if missing - requested:
        raise ReportError("cost snapshot scope unknownRoots names a root that was not requested")
    if requested - (found | missing):
        raise ReportError("cost snapshot scope leaves a requested root out of both resolvedRoots "
                          "and unknownRoots; coverage cannot be established from it")

    unpriced = cost["unpriced"]
    if not isinstance(unpriced, list):
        raise ReportError("cost snapshot unpriced must be a list")

    # Producer text in, ENUM out. Only the known engine contract value has a descriptor; anything
    # else is withheld rather than printed, because this field reached the rendered page verbatim
    # and an absolute path in it went with it.
    lineage_raw = _text(cost["lineageFrom"], "cost snapshot lineageFrom")
    return {
        "at": _utc(cost["at"], "cost snapshot at"),
        "lineage": LINEAGE_DESCRIPTORS.get(lineage_raw, LINEAGE_UNKNOWN),
        "lineage_known": lineage_raw in LINEAGE_DESCRIPTORS,
        "cache_version": version,
        "time_precision": precision,
        "prices_sha256": fingerprint,
        "prices_sources": price_sources,
        "roots": list(roots),
        "resolved_roots": list(resolved),
        "unknown_roots": list(unknown),
        "since": _day(scope.get("since"), "cost snapshot scope since"),
        "until": _day(scope.get("until"), "cost snapshot scope until"),
        "allocation_coverage": _allocation_coverage(scope),
        "unpriced_count": len(unpriced),
        "rows": _read_rows(cost["rows"]),
    }


def _read_rows(rows):
    if not isinstance(rows, list):
        raise ReportError("cost snapshot rows must be a list")
    out = []
    keys = set()
    for raw in rows:
        _obj(raw, "each cost snapshot row")
        for key in REQUIRED_ROW_FIELDS:
            _field(raw, key, "a cost snapshot row")
        key = _text(raw["key"], "a cost snapshot row key")
        if key in keys:
            raise ReportError("the cost snapshot contains a duplicate row key; rows must be summed once")
        keys.add(key)

        row = {name: _count(raw[name], f"row counter {name!r}") for name in ROW_COUNTERS}
        row["key"] = key
        row["usd"] = _money(raw["usd"], "row usd")
        row["partial"] = _flag(raw["partial"], "row partial")
        reasons = raw["partialReasons"]
        if not isinstance(reasons, list):
            raise ReportError("row partialReasons must be a list")
        for reason in reasons:
            if reason not in PARTIAL_REASONS:
                raise ReportError(f"row partialReasons carries an unrecognised cause {reason!r}; "
                                  f"this reporter knows {', '.join(PARTIAL_REASONS)}")
        row["reasons"] = list(reasons)
        models = raw["models"]
        if not isinstance(models, list):
            raise ReportError("row models must be a list")
        row["models"] = [_text(m, "a row model id") for m in models]
        if "think" in raw:
            think = _count(raw["think"], "row counter 'think'")
            if think > row["out"]:
                raise ReportError("row counter 'think' exceeds its own output tokens; reasoning "
                                  "tokens are a SUBSET of output, never an addition to it")
            row["think"] = think
        out.append(row)
    return out


def _cross_check(goal, cost):
    if sorted(goal["scope"]["roots"]) != sorted(cost["roots"]):
        raise ReportError("the cost snapshot was run for a different root set than the goal manifest "
                          "requests; this report never reinterprets a snapshot's scope")
    if goal["scope"]["since"] != cost["since"]:
        raise ReportError("the cost snapshot's since bound does not match the goal manifest")
    if goal["scope"]["until"] != cost["until"]:
        raise ReportError("the cost snapshot's until bound does not match the goal manifest")


# --- sections -----------------------------------------------------------------------------

def _usage_section(cost):
    rows = cost["rows"]
    totals = {name: sum(row[name] for row in rows) for name in ROW_COUNTERS}
    think_known = bool(rows) and all("think" in row for row in rows)
    models = sorted({model for row in rows for model in row["models"]})
    return {
        "rows": len(rows),
        "turns": totals["turns"],
        "input_tokens": totals["in"],
        "output_tokens": totals["out"],
        "cache_read_tokens": totals["cacheRead"],
        "cache_write_5m": totals["cw5m"],
        "cache_write_1h": totals["cw1h"],
        "cache_write_unknown_ttl": totals["cwUnknownTtl"],
        "cache_write_conflict": totals["cwConflict"],
        "withheld_turns": totals["withheldTurns"],
        "tiered_turns": totals["tieredTurns"],
        "reasoning_tokens": {
            "known": think_known,
            "value": sum(row["think"] for row in rows) if think_known else None,
            "subset_of": "output_tokens",
            "note": "reasoning tokens are already inside output_tokens and are never added again",
        },
        # A turn whose own counts contradict each other cannot be normalized in either direction.
        "normalized_total_known": totals["cwConflict"] == 0,
        "distinct_model_count": len(models),
    }, totals


def _money_section(goal, cost, totals):
    rows = cost["rows"]
    subtotal = sum((row["usd"] for row in rows), Decimal(0))
    reasons = sorted({reason for row in rows for reason in row["reasons"]})
    # A scope with no row at all has not been priced completely - it has not been priced. The
    # subtotal below is still `0`, because that is what summing no rows gives; what changes is
    # that the report stops calling that a complete answer.
    complete = (bool(rows) and not any(row["partial"] for row in rows)
                and cost["unpriced_count"] == 0)
    ownership = goal["scope"]["ownership"]
    basis = {
        "dedicated": "the manifest declares a dedicated scope; declared, not verified",
        "shared": "the manifest declares a SHARED scope; no per-goal allocation is attempted",
        "unknown": "scope ownership is unknown; nothing is qualified as this goal's own usage",
    }[ownership]
    return {
        "label": MONEY_LABEL,
        "known_subtotal_usd": str(subtotal),
        # Full precision is carried above; this is the single place a figure is rounded, for display.
        "display_usd": str(subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "is_cash": False,
        "pricing_complete": complete,
        "partial_reasons": reasons,
        "rows_partial": sum(1 for row in rows if row["partial"]),
        "unpriced_model_count": cost["unpriced_count"],
        "withheld_turns": totals["withheldTurns"],
        # The name says what the boolean combines. It was `covers_every_requested_root` while it
        # only asked about roots; since it also requires a known-complete allocation verdict, that
        # name made the page state that a goal had not covered its roots while the scope section
        # two rows down said zero roots were unknown.
        "whole_source_scope_covered": _source_coverage_complete(cost),
        # Never True: a complete price is still not evidence of the producing goal.
        "attributable_to_this_goal": None if ownership == "dedicated" else False,
        "attribution_basis": basis,
        "qualification": "a known priced subtotal of allocated API-equivalent value, not a goal bill",
    }


def _time_section(goal, now):
    authorized = goal["authorized_utc"]
    accepted = goal["outcome"]["accepted_utc"]
    status = goal["outcome"]["status"]

    if authorized is None:
        to_acceptance = {"known": False, "seconds": None, "human": None, "reason": "authorized_utc unknown"}
    elif accepted is None:
        to_acceptance = {"known": False, "seconds": None, "human": None, "reason": "accepted_utc unknown"}
    elif accepted < authorized:
        to_acceptance = {"known": False, "seconds": None, "human": None,
               "reason": "accepted_utc precedes authorized_utc"}
    else:
        seconds = int((accepted - authorized).total_seconds())
        to_acceptance = {"known": True, "seconds": seconds, "human": _duration(seconds), "reason": None}

    if status not in OPEN_STATUSES:
        elapsed = {"applicable": False, "known": False, "seconds": None, "human": None,
                   "reason": f"status {status!r} is not open work"}
    elif authorized is None:
        elapsed = {"applicable": True, "known": False, "seconds": None, "human": None,
                   "reason": "authorized_utc unknown"}
    elif now < authorized:
        elapsed = {"applicable": True, "known": False, "seconds": None, "human": None,
                   "reason": "now_utc precedes authorized_utc"}
    else:
        seconds = int((now - authorized).total_seconds())
        elapsed = {"applicable": True, "known": True, "seconds": seconds,
                   "human": _duration(seconds), "reason": None}

    intervals = goal["worker_intervals"]
    closed = [i for i in intervals if i["end"] is not None]
    known_seconds = int(sum((i["end"] - i["start"]).total_seconds() for i in closed))
    overlapping, overlap_reason = _overlap_state(intervals)
    worker = {
        "aggregate_known_seconds": known_seconds,
        "aggregate_known_human": _duration(known_seconds),
        "interval_count": len(intervals),
        "open_interval_count": len(intervals) - len(closed),
        "actor_count": len({i["actor"] for i in intervals}),
        "overlapping": overlapping,
        "overlap_reason": overlap_reason,
        "is_wall_time": False,
        "note": "aggregate worker time across intervals; never the goal's wall-clock duration",
    }
    return {"authorized_utc": _utc_out(authorized), "now_utc": _utc_out(now),
            "time_to_acceptance": to_acceptance, "elapsed_open": elapsed, "worker_time": worker}


OVERLAP_UNKNOWN_REASON = ("at least one worker interval has no end, so an overlap with it is "
                          "possible but cannot be established")


def _overlap_state(intervals):
    """Do the worker intervals overlap? true / false / unknown - never a confident false.

    Intervals are half-open `[start, end)`, so two that merely touch do not overlap and a
    zero-length one overlaps nothing. An interval with no end is `[start, ...)`: its end is
    UNKNOWN, but it does contain its own start instant, which is what lets some open cases be
    decided. Filtering the open ones out before looking - the previous behaviour - answered a
    provable overlap with a flat `false`.
    """
    nonempty = [i for i in intervals if i["end"] is not None and i["end"] > i["start"]]
    opens = sorted(i["start"] for i in intervals if i["end"] is None)

    reach = None
    for interval in sorted(nonempty, key=lambda i: i["start"]):
        if reach is not None and interval["start"] < reach:
            return True, None
        reach = interval["end"] if reach is None else max(reach, interval["end"])
    # An open interval's own start sitting inside a closed one is a certainty, not a guess.
    for start in opens:
        if any(i["start"] <= start < i["end"] for i in nonempty):
            return True, None
    # Two intervals that both begin at the same instant both contain it.
    if len(opens) != len(set(opens)):
        return True, None

    possible = len(opens) > 1 or any(start < i["start"] for start in opens for i in nonempty)
    return (None, OVERLAP_UNKNOWN_REASON) if possible else (False, None)


def _attention_section(goal):
    policy = goal["policy"]
    tz = goal["tz"]
    checkpoint_cap = policy["required_checkpoints_per_day"]
    minute_cap = policy["required_minutes_per_day"]
    days = {}
    for entry in goal["interventions"]["entries"]:
        date = entry["at"].astimezone(tz).date().isoformat()
        day = days.setdefault(date, {"date": date, "required_count": 0, "optional_count": 0,
                                     "required_minutes": Decimal(0), "optional_minutes": Decimal(0),
                                     "required_unknown": 0, "optional_unknown": 0})
        side = "required" if entry["required"] else "optional"
        day[f"{side}_count"] += 1
        if entry["minutes"] is None:
            day[f"{side}_unknown"] += 1
        else:
            day[f"{side}_minutes"] += entry["minutes"]

    out = []
    unknown_required = False
    for date in sorted(days):
        day = days[date]
        unknown_required = unknown_required or day["required_unknown"] > 0
        if day["required_minutes"] > minute_cap:
            over_minutes = True
        elif day["required_unknown"]:
            over_minutes = None  # known minutes fit, but not all of them are known
        else:
            over_minutes = False
        out.append({
            "date": date,
            "required_count": day["required_count"],
            "optional_count": day["optional_count"],
            "required_minutes_known": _number_out(day["required_minutes"]),
            "optional_minutes_known": _number_out(day["optional_minutes"]),
            "required_minutes_unknown_count": day["required_unknown"],
            "optional_minutes_unknown_count": day["optional_unknown"],
            "required_checkpoints_exceeds_cap_alone": day["required_count"] > checkpoint_cap,
            "required_minutes_exceeds_cap_alone": over_minutes,
        })
    return {
        "timezone": goal["timezone"],
        "required_checkpoints_per_day": checkpoint_cap,
        "required_minutes_per_day": _number_out(minute_cap),
        "intervention_count": len(goal["interventions"]["entries"]),
        "deduped_count": goal["interventions"]["deduped"],
        # Only days this goal RECORDED an intervention on appear above. A day with no row is not a
        # day with no attention: this report has no notion of the goal's day span, so how many days
        # went unrecorded is unknown - and a missing row is never read as a zero.
        "unrecorded_days": None,
        "unrecorded_days_note": ("days absent from this goal's intervention records are NOT "
                                 "recorded rather than zero; how many there were is unknown"),
        "days": out,
        # A single goal can never establish that the whole pilot stayed under a shared daily cap.
        "whole_pilot_compliance": None,
        "note": "this goal's contribution to each local day, required and optional counted apart",
    }, unknown_required


def _budget_section(goal):
    policy = goal["policy"]
    return {
        "monthly_llm_cap_usd": policy["monthly_llm_cap_usd"],
        "pilot_incremental_cap_usd": policy["pilot_incremental_cap_usd"],
        "critical_reserve_usd": policy["critical_reserve_usd"],
        "cash_spend_usd": None,
        "cash_headroom_usd": None,
        "cash_status": CASH_STATUS,
        "note": ("caps are configuration for display. Allocated API-equivalent totals neither consume "
                 "nor prove the actual monthly budget, and nothing here admits or blocks spending."),
    }


def _limitations(codes):
    return [{"code": code, "text": LIMITATION_TEXT[code]} for code in codes if code in LIMITATION_TEXT]


CANONICAL_RECIPE = ("sha256 of json.dumps(cost, sort_keys=True, default=str, "
                    "separators=(',',':')).encode('utf-8') - a digest of the PARSED object, not "
                    "of any file, and NOT comparable with sha256sum")
RAW_DIGEST_NOTE = "sha256sum of the cost snapshot file, over the exact bytes that were parsed"
NO_RAW_DIGEST_NOTE = ("unknown: this report was built from an in-memory object, not a file, so "
                      "there are no raw bytes to fingerprint")


def _source_coverage_complete(cost):
    """Three conditions, all required: every requested root resolved, the engine saying the
    selected sources hold all of their own work, and at least one priced row to say it about. Any
    one unmet - or unknown - leaves the scope incomplete. None of them says anything about whether
    what WAS selected is priced correctly."""
    return (not cost["unknown_roots"]
            and cost["allocation_coverage"]["complete"] is True
            and bool(cost["rows"]))


def _canonical_hash(cost):
    """A digest of the parsed object. Two files differing only in whitespace share it - which is
    exactly why it is NOT the file's sha256 and is never labelled as one."""
    canonical = json.dumps(cost, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- keyed scope handles ---------------------------------------------------------------------
# A raw root id never leaves this report, and that is right. But a reader comparing two goals
# still has to answer one question about their scopes: are they the same set, or disjoint? These
# handles answer exactly that and nothing else.
#
# They are KEYED, not merely hashed. An unsalted digest of an id is a confirmation oracle: anyone
# holding a small dictionary of plausible root ids can hash each one and read the answer off the
# report. Under HMAC with a private manifest salt that attack needs the salt, which never leaves
# the manifest. The domain prefix keeps a root handle from colliding with a digest of the same
# string taken for another purpose, and the `v2` in it marks the change of representation: a v1
# unkeyed report is refused by a comparison rather than accepted as if it were opaque.

ROOT_FINGERPRINT_DOMAIN = b"goal-root-v2\x00"
ROOT_FINGERPRINT_ALGORITHM = "hmac-sha256(scope_fingerprint_salt, goal-root-v2\\0<root-id>)"
SCOPE_FINGERPRINT_NOTE = (
    "Opaque, deterministic handles for the REQUESTED roots, keyed by a private manifest salt that "
    "never leaves it. The same roots in any order share them; one different root, bound, mode or "
    "salt does not. They let a comparison prove two scopes are identical or disjoint without "
    "handling a root id, and without the salt they cannot be confirmed against a guessed one.")

# A salt is a key, so it is held to a key's standard: long enough and varied enough that a
# dictionary of plausible root ids buys nothing. Neither check proves entropy - they refuse the
# two ways a hand-written salt is actually weak: too short, or one character repeated.
SALT_MIN_LENGTH = 32
SALT_MIN_DISTINCT = 12
SALT_RE = re.compile(r"^[!-~]+$")


def _salt(value, what="scope_fingerprint_salt"):
    if not isinstance(value, str) or not SALT_RE.match(value or ""):
        raise ReportError(f"{what} must be a private string of printable ASCII characters, with "
                          "no spaces and no control characters")
    if len(value) < SALT_MIN_LENGTH:
        raise ReportError(f"{what} must be at least {SALT_MIN_LENGTH} characters; it is a key, "
                          "not a label")
    if len(set(value)) < SALT_MIN_DISTINCT:
        raise ReportError(f"{what} must use at least {SALT_MIN_DISTINCT} distinct characters; a "
                          "repeated character is not a key")
    return value


def root_fingerprint(root_id: str, salt: str) -> str:
    """One root's keyed handle, domain-separated so it means nothing anywhere else."""
    return hmac.new(salt.encode("utf-8"), ROOT_FINGERPRINT_DOMAIN + root_id.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def scope_fingerprint(root_fingerprints, mode, since, until, engine_time_precision, salt) -> str:
    """One handle for the whole SELECTION: the roots plus the bounds and precision they were read
    at. Two goals that requested the same roots over different days do not share it."""
    payload = {"root_fingerprints": list(root_fingerprints), "mode": mode, "since": since,
               "until": until, "engine_time_precision": engine_time_precision}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(salt.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _scope_fingerprints(goal, cost):
    scope, salt = goal["scope"], goal["scope_fingerprint_salt"]
    digests = sorted(root_fingerprint(root, salt) for root in scope["roots"])
    return {
        "root_fingerprints": digests,
        "scope_fingerprint": scope_fingerprint(digests, scope["mode"], scope["since"],
                                               scope["until"], cost["time_precision"], salt),
        "root_fingerprint_algorithm": ROOT_FINGERPRINT_ALGORITHM,
        "note": SCOPE_FINGERPRINT_NOTE,
    }


# --- public face ---------------------------------------------------------------------------

def build_report(goal: dict, cost: dict, *, now_utc: str, snapshot_sha256: str | None = None) -> dict:
    """Build the sanitized report from a goal manifest and ONE cost snapshot.

    `cost` must be parsed with `parse_float=Decimal` so monetary values keep the decimal
    representation the engine wrote. Nothing here re-prices, re-collects or re-verifies anything.

    `snapshot_sha256` is the sha256 of the FILE those bytes came from, which only a caller that
    read a file can know. `load_report` supplies it; a caller passing a dict cannot, so the raw
    digest is reported as unknown rather than faked from the parsed object.
    """
    now = _utc(now_utc, "now_utc")
    parsed_goal = _read_goal(goal)
    parsed_cost = _read_cost(cost)
    _cross_check(parsed_goal, parsed_cost)

    usage, totals = _usage_section(parsed_cost)
    money = _money_section(parsed_goal, parsed_cost, totals)
    time_section = _time_section(parsed_goal, now)
    attention, minutes_unknown = _attention_section(parsed_goal)
    outcome = parsed_goal["outcome"]

    codes = ["allocated-not-cash", "source-allocation-not-attribution", "lineage-path-derived",
             "acceptance-is-attestation", "single-goal-contribution"]
    if parsed_cost["unknown_roots"]:
        codes.append("coverage-incomplete-unknown-roots")
    allocation = parsed_cost["allocation_coverage"]
    if not allocation["known"]:
        codes.append("source-allocation-coverage-unknown")
    elif allocation["complete"] is False:
        codes.append("source-allocation-outside-scope")
    if not parsed_cost["rows"]:
        codes.append("no-priced-rows")
    if not parsed_cost["lineage_known"]:
        codes.append("source-lineage-descriptor-withheld")
    if not money["pricing_complete"]:
        codes.append("pricing-incomplete")
    if not usage["normalized_total_known"]:
        codes.append("token-totals-unknown-conflict")
    if parsed_goal["scope"]["ownership"] == "shared":
        codes.append("shared-scope-unqualified")
    if parsed_goal["scope"]["ownership"] == "unknown":
        codes.append("ownership-unknown")
    if parsed_goal["authorized_utc"] is None:
        codes.append("authorization-unknown")
    if outcome["status"] == "accepted" and outcome["accepted_utc"] is None:
        codes.append("acceptance-time-unknown")
    if time_section["time_to_acceptance"]["reason"] == "accepted_utc precedes authorized_utc":
        codes.append("time-to-acceptance-unordered")
    if time_section["worker_time"]["open_interval_count"]:
        codes.append("worker-intervals-open")
    if time_section["worker_time"]["overlapping"] is None:
        codes.append("worker-intervals-overlap-unknown")
    if time_section["worker_time"]["overlapping"]:
        codes.append("worker-intervals-overlap")
    if minutes_unknown:
        codes.append("required-minutes-unknown")
    if any(item["ref"] is None for item in outcome["evidence"]):
        codes.append("evidence-ref-withheld")

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "goal-report",
        "generated_utc": _utc_out(now),
        "goal": {"id": parsed_goal["goal_id"], "title": parsed_goal["title"],
                 "timezone": parsed_goal["timezone"], "synthetic": parsed_goal["synthetic"]},
        "outcome": {
            "status": outcome["status"],
            "accepted": outcome["status"] == "accepted",
            "implementer": outcome["implementer"],
            "verifier": outcome["verifier"],
            "accepted_utc": _utc_out(outcome["accepted_utc"]),
            "blocker": outcome["blocker"],
            "evidence": outcome["evidence"],
            "evidence_count": len(outcome["evidence"]),
            "reverified_by_this_report": False,
        },
        "time": time_section,
        "usage": usage,
        "allocated_api_equivalent": money,
        "scope": {
            "mode": parsed_goal["scope"]["mode"],
            "ownership": parsed_goal["scope"]["ownership"],
            "since": parsed_goal["scope"]["since"],
            "until": parsed_goal["scope"]["until"],
            "roots_requested": len(parsed_goal["scope"]["roots"]),
            "roots_resolved": len(parsed_cost["resolved_roots"]),
            "roots_unknown": len(parsed_cost["unknown_roots"]),
            "allocation_coverage": parsed_cost["allocation_coverage"],
            "coverage_complete": _source_coverage_complete(parsed_cost),
        },
        "budget": _budget_section(parsed_goal),
        "attention": attention,
        "source": {
            "snapshot_at": _utc_out(parsed_cost["at"]),
            "engine_cache_version": parsed_cost["cache_version"],
            "time_precision": parsed_cost["time_precision"],
            "prices_sha256": parsed_cost["prices_sha256"],
            "prices_sources": parsed_cost["prices_sources"],
            "snapshot_sha256": snapshot_sha256,
            "snapshot_sha256_note": RAW_DIGEST_NOTE if snapshot_sha256 else NO_RAW_DIGEST_NOTE,
            "snapshot_canonical_sha256": _canonical_hash(cost),
            "snapshot_canonical_recipe": CANONICAL_RECIPE,
            "scope": _scope_fingerprints(parsed_goal, parsed_cost),
            "lineage": parsed_cost["lineage"],
            "evidence_source": parsed_goal["evidence_source"],
            "note": SOURCE_NOTE,
        },
        "limitations": _limitations(codes),
    }


def _read_json(path: Path, what: str):
    """Read a file ONCE and return (its exact bytes, what those same bytes parse to).

    One read, so the digest and the report can never describe two different versions of a file
    that changed between them.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise ReportError(f"cannot read the {what}: {error.strerror}") from None
    try:
        # parse_float=Decimal is the whole point: it keeps the engine's own decimal representation.
        return raw, json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except UnicodeDecodeError:
        raise ReportError(f"the {what} is not valid UTF-8") from None
    except json.JSONDecodeError as error:
        raise ReportError(f"the {what} is not valid JSON (line {error.lineno}, column {error.colno})") from None


def load_report(goal_path: Path, cost_path: Path, *, now_utc: str) -> dict:
    """Read both inputs from disk (money as Decimal) and build the report. Inputs are read-only."""
    _, goal = _read_json(goal_path, "goal manifest")
    raw_cost, cost = _read_json(cost_path, "cost snapshot")
    return build_report(goal, cost, now_utc=now_utc,
                        snapshot_sha256=hashlib.sha256(raw_cost).hexdigest())


def utc_now() -> str:
    """The current UTC instant, for report generation only - never as an authorization time."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_report(report: dict, path: Path) -> None:
    """Write the report atomically: private temp file in the same directory, fsync, replace.

    Serialization happens before anything touches the filesystem, so a report that cannot be
    serialized never replaces - or truncates - an existing one.
    """
    try:
        payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    except TypeError:
        raise ReportError("the report contains a value that cannot be written as JSON") from None
    write_text_atomic(payload, path)


def write_text_atomic(payload: str, path: Path) -> None:
    """Replace `path` with `payload` atomically, 0600 from creation. Errors leave it untouched."""
    path = Path(path)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise

# ABOUTME: Reads a FROZEN comparison protocol plus an observation ledger of goal reports and says
# ABOUTME: what the evidence supports. It measures a posture; it never promotes, prices or spends.

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import references
from . import report as R

# The two input schemas this reader understands. A different version is refused, never guessed.
SCHEMA_VERSION = 1
PROTOCOL_KIND = "goal-comparison-protocol"
OBSERVATIONS_KIND = "goal-comparison-observations"
REPORT_KIND = "goal-comparison-report"

# A frozen protocol is small ON PURPOSE. Twelve arms is the most a person can hold in their head,
# and a protocol that grows after the fact is no longer the thing anyone agreed to measure.
MAX_WORK_CLASSES = 2
MAX_PAIRS = 6
MAX_HORIZON_HOURS = 336
MIN_FULLY_OBSERVED_PAIRS = 3
# The delegated arm must be at least a fifth faster. Anything smaller is inside the noise of six
# hand-matched pairs, and this document exists to refuse a median that only looks like a result.
LATENCY_IMPROVEMENT = Decimal("0.8")

STATUSES = ("not-started", "observing", "inconclusive", "candidate", "not-candidate")
# Seven answers, and never an eighth. `unresolved` is the one that carries the whole repair: a
# goal report states a STATUS, never the instant that status was reached, so an outcome nobody
# attested with a timestamp is not a result - it is a gap, and it is drawn as one.
CLASSIFICATIONS = ("accepted", "failed", "blocked", "abandoned", "censored", "pending", "unresolved")
# A non-acceptance the goal reporter cannot date. Reaching one of these classifications takes an
# EXTERNAL attestation in the observation ledger; the report's own word is not enough, because G1
# calls `blocked` open work and `reopened` exists precisely because an outcome is not absorbing.
TERMINAL_NON_ACCEPTANCE = ("failed", "blocked", "abandoned")
ADMISSION_STATUSES = ("not-attested", "included-only-overflow-disabled", "paid-route-bound-verified")
ATTESTED_STATUSES = ADMISSION_STATUSES[1:]

SIDES = ("baseline", "delegated")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTROL_RE = references.CONTROL

NO_PROMOTION_NOTE = (
    "A candidate is a MEASUREMENT POSTURE: the evidence collected under this protocol is "
    "consistent with the delegated arm being better. It is not an approval, not a decision and "
    "not a promotion. Nothing here routes work, admits a cost or changes a subscription.")

CASH_STATUS = R.CASH_STATUS

GATE_TEXT = {
    "fully-observed-pairs": f"at least {MIN_FULLY_OBSERVED_PAIRS} pairs have both arms resolved",
    "source-coverage-complete": "every arm report resolves all of its requested roots, carries a "
                                "known and complete source allocation coverage verdict, prices its "
                                "rows completely and has normalised token totals",
    "budget-admission-attested": "the protocol's budget admission is attested",
    "controls-attested": "the protocol cites evidence that the arms ran under the declared shared "
                         "controls",
    "attention-within-caps": "at least one required-intervention day is recorded, every one has "
                             "known minutes, and neither the checkpoint cap nor the minute cap is "
                             "exceeded",
    "defect-attestations-known": "both arms carry an escaped-defect attestation for every goal",
    "delegated-acceptance-not-lower": "the delegated acceptance rate is no lower than the baseline's",
    "delegated-defect-rate-not-higher": "the delegated escaped-defect rate is no higher than the "
                                        "baseline's",
    "delegated-latency-lower": "the accepted-only median latency of the delegated arm is at least "
                               "20% lower than the baseline's",
}
# Which gates are EVIDENCE (must all hold before any verdict is possible) and which are
# DIRECTIONAL (they are the verdict). Named, never a slice: adding a gate in the middle of the
# list used to silently move one from one kind to the other.
GATE_KIND = {
    "fully-observed-pairs": "evidence",
    "source-coverage-complete": "evidence",
    "budget-admission-attested": "evidence",
    "controls-attested": "evidence",
    "attention-within-caps": "evidence",
    "defect-attestations-known": "evidence",
    "delegated-acceptance-not-lower": "directional",
    "delegated-defect-rate-not-higher": "directional",
    "delegated-latency-lower": "directional",
}
GATE_ORDER = tuple(GATE_TEXT)

# Why one arm could not be placed against the horizon. Each is a distinct gap, not one vague one.
UNRESOLVED_REASONS = {
    "acceptance-time-unknown": "the report is accepted but carries no usable acceptance interval",
    "accepted-before-window": "the declared acceptance predates the protocol window",
    "accepted-after-horizon": "the declared acceptance falls after the horizon",
    "accepted-after-cutoff": "the declared acceptance falls after the ledger's observation cutoff",
    "terminal-attestation-missing": "no ledger attestation dates this non-acceptance",
    "terminal-evidence-withheld": "the terminal attestation cites a reference this report may not "
                                  "carry",
    "terminal-instant-outside-window": "the attested terminal instant falls outside the window",
    "terminal-instant-after-cutoff": "the attested terminal instant falls after the ledger's "
                                     "observation cutoff",
    "reopened-may-hide-acceptance": "a reopened goal may hide a prior acceptance, so its history "
                                    "is not established",
    "open-at-cutoff-after-horizon": "the arm was still open when the ledger last looked, and that "
                                    "look was at or after the horizon; the observation censors it "
                                    "rather than resolving it",
    # The latency this comparison medians is `accepted - authorized`. Bounding only the acceptance
    # bounded one END of that interval: an arm authorized before the protocol existed measured
    # pre-window waiting as if it had happened under the protocol, and carried the improvement gate
    # on the strength of when its clock was started.
    "authorization-before-window": "the declared authorization predates the protocol window, so "
                                   "the interval it starts is not work done under the protocol",
    "authorization-after-acceptance": "the declared authorization falls after the declared "
                                      "acceptance, so the interval between them is not a latency",
    "authorization-after-horizon": "the declared authorization falls after the horizon",
    "authorization-after-cutoff": "the declared authorization falls after the ledger's observation "
                                  "cutoff",
}

RATIONALE_TEXT = {
    "window-not-open": "the protocol window has not opened at this generation instant",
    "arms-pending": "at least one arm is still pending before the horizon; observation continues",
    "fully-observed-pairs-below-minimum":
        f"fewer than {MIN_FULLY_OBSERVED_PAIRS} pairs have both arms resolved; a pending or "
        "unresolved arm leaves its pair unobserved",
    # Three separate causes. They were one code whose sentence named unresolved roots, so an arm
    # that resolved every root and failed on ALLOCATION sent a reader hunting for a root that does
    # not exist. The verdict was right and the explanation was false.
    "requested-roots-unresolved": "at least one arm report leaves a requested root unresolved, so "
                                  "its usage is unknown rather than zero",
    "source-allocation-outside-scope":
        "at least one arm's selected sources contain shared message ids allocated to a source "
        "outside that arm's scope; the arm resolved its roots and is priced completely, but its "
        "selection does not hold all of its own work",
    "source-allocation-coverage-unknown":
        "at least one arm's snapshot predates the engine's allocation-coverage verdict, so whether "
        "its selected sources hold all of their own work is unknown rather than complete",
    "pricing-incomplete": "at least one arm report carries a partially priced subtotal",
    "no-priced-rows": "at least one arm's snapshot resolved its scope and returned no priced row "
                      "at all; a subtotal of zero over nothing is an unknown, not measured zero "
                      "work",
    "token-totals-unknown": "at least one arm report has contradicted token counts",
    "budget-admission-not-attested": "the protocol declares no budget admission attestation",
    "controls-not-attested": "the protocol cites no usable evidence that the arms ran under the "
                             "declared shared controls",
    "arms-unresolved": "at least one arm could not be placed against the horizon, so every "
                       "directional answer that would count it is unknown",
    "attention-not-recorded": "no intervention is recorded at all, which is unknown rather than zero",
    "required-attention-not-recorded":
        "no REQUIRED intervention is recorded on any day, so nothing establishes that the pilot "
        "stayed inside its required-checkpoint or required-minute cap",
    "mixed-class-results": "one work class is a candidate and another decisively is not; the "
                           "cohort headline is neither",
    "required-minutes-unknown": "at least one required intervention carries no minutes",
    "checkpoint-cap-exceeded": "a local day exceeds the required-checkpoint cap",
    "minute-cap-exceeded": "a local day exceeds the required-minute cap",
    "defect-attestation-unknown": "at least one arm has no escaped-defect attestation",
    "defect-rate-unknown": "the escaped-defect rate cannot be computed from a partial attestation",
    "delegated-acceptance-rate-lower": "the delegated acceptance rate is lower than the baseline's",
    "delegated-defect-rate-higher": "the delegated escaped-defect rate is higher than the baseline's",
    "latency-median-unknown": "an accepted-only median is unknown on at least one arm",
    "latency-improvement-below-threshold":
        "the delegated accepted-only median is not at least 20% lower than the baseline's",
    "all-gates-met": "every gate this protocol defines is met by the observed evidence",
    "synthetic-inputs": "at least one input is declared synthetic, so no class can read beyond "
                        "inconclusive however the metrics fall",
}

LIMITATION_TEXT = {
    "allocated-not-cash":
        "Every money figure is an allocated API-equivalent the upstream engine recomputed from "
        "token counts. It is not cash, not an invoice and not a bill, and no attestation here "
        "turns it into one.",
    "source-allocation-not-attribution":
        "An arm's roots are a SOURCE ALLOCATION - which transcripts its usage was counted under. "
        "An exact, disjoint partition proves the arms did not share a source. It is still not "
        "evidence that a goal produced the turns counted under it.",
    "acceptance-is-attestation":
        "Outcome, acceptance time, escaped defects and budget admission are declared attestations "
        "copied from the protocol, the ledger and the goal reports. This document re-ran, "
        "re-checked and re-verified none of them.",
    "utc-day-precision":
        "The source engine's time precision is utc-day. Sub-day allocation is unsupported, and no "
        "usage is split between two goals that shared a day.",
    "no-promotion":
        "No status here promotes, approves or admits anything. A candidate is a measurement "
        "posture; the decision stays with a person.",
    "synthetic-inputs":
        "At least one input is declared synthetic. Nothing in this document describes real work, "
        "real usage or real money, and no class can read beyond inconclusive.",
    "censored-observations":
        "At least one arm is censored: the ledger's last look at it, taken at or after the "
        "horizon, still found it open. Censored arms contribute no latency and are never dropped.",
    "censored-attested-at-cutoff":
        "A censored arm's state is what the ledger ATTESTED at its observation cutoff. It is not "
        "proof of what that arm was doing at any other instant inside the window.",
    "unresolved-observations":
        "At least one arm could not be placed against the horizon at all. A goal report states a "
        "status, never the instant it was reached, so a failure, a block or an abandonment counts "
        "only when the ledger attests when it happened and cites evidence for it. Until then the "
        "arm is UNRESOLVED: it is not a clean outcome, it does not settle its pair, and every "
        "rate that would count it is unknown.",
    "pending-observations":
        "At least one arm is still pending at the ledger's observation cutoff, which falls before "
        "the horizon. Its pair is not yet fully observed.",
    "terminal-instant-attested-not-verified":
        "An attested terminal instant is a declaration in the observation ledger with a reference "
        "beside it. This document did not open that reference or re-establish the outcome.",
    "controls-attested-not-verified":
        "The shared controls - model policy, review policy, and the claim that only the changed "
        "dimension differed - are DECLARED in the protocol. Nothing here observed the model an arm "
        "actually used, the review it actually got, or the route it was actually run on.",
    "matching-is-declared-not-verified":
        "The pairs are matched on fields the protocol declares. This document never inferred, "
        "re-derived or cross-checked a matched field, a shared control or a policy against the "
        "goal reports, so the claim that the arms are comparable is an attestation like the rest.",
    "small-sample-no-statistical-weight":
        "At most six hand-matched pairs sit behind every figure here. A median over that many "
        "arms carries no statistical weight and no confidence interval is implied by it.",
    "defect-attestation-unknown":
        "At least one arm carries no escaped-defect count. That is UNKNOWN, never zero, and no "
        "defect count is inferred from an arm's status.",
    "required-minutes-unknown":
        "At least one required intervention carries no minutes. Missing minutes stay unknown and "
        "are never inferred from elapsed time.",
    "intervention-days-not-recorded":
        "Interventions are a pilot-wide ledger. A calendar day with no record is NOT RECORDED, "
        "never a day with zero attention, so the days below are not a complete calendar.",
    "source-provenance-unfingerprinted":
        "At least one goal report was built from an in-memory object rather than a file, so its "
        "upstream snapshot has no raw-byte digest. That is a missing receipt, not a wrong one.",
    "budget-admission-not-attested":
        "The protocol declares no budget admission. Nothing here checked a provider, a subscription "
        "or a bill, and a directional candidate is blocked until an attestation exists.",
    "budget-admission-is-attestation":
        "The budget admission is an attestation with a timestamp and a reference. It is not a "
        "provider check, not a cash balance and not proof that a route stayed inside a cap.",
    "evidence-ref-withheld":
        "At least one reference was withheld because it is a private path or an unapproved URL "
        "scheme. Its class is named; its value is not.",
    "ledger-references-private":
        "The ledger's own report paths, file digests, terminal attestations and defect evidence "
        "references never enter this document. Only whether an attestation cites one, and its "
        "safety class, are reported.",
    "report-bytes-frozen":
        "Every goal report was checked against the digest its own observation froze, so a report "
        "edited after the ledger recorded it is refused rather than read. That binds the bytes; "
        "it does not make the claims inside them true.",
}


class ComparisonError(R.ReportError):
    """Invalid or unsupported comparison input. Carries a cause, never the offending payload."""


# The primitives below are this package's own, reused rather than re-implemented: a second copy of
# "what is a valid UTC instant" or "what is a non-negative count" is how two modules start
# disagreeing about the same input.
_obj = R._obj
_field = R._field
_text = R._text
_flag = R._flag
_count = R._count
_number = R._number
_number_out = R._number_out
_utc = R._utc
_utc_out = R._utc_out
_day = R._day
_duration = R._duration


def _fail(message):
    raise ComparisonError(message)


def _scalar(value, what):
    """A control or an arm value is a scalar, so a second change cannot hide inside a structure."""
    if isinstance(value, bool) or isinstance(value, (str, int)):
        return value
    _fail(f"{what} must be a scalar (a string, a whole number or a boolean)")


def _decimal_string(value, what):
    """Money crosses this boundary as the decimal STRING the goal reporter wrote. A JSON number has
    already lost that representation by the time it is parsed, so it is refused, never re-read."""
    if not isinstance(value, str):
        _fail(f"{what} must arrive as a decimal string, exactly as the goal reporter writes it")
    return R._money(value, what)


def _sha256(value, what):
    if not isinstance(value, str) or not SHA256_RE.match(value):
        _fail(f"{what} must be a lowercase hex sha256 digest")
    return value


def _reference(ref, what, *, required=True):
    """Classify a reference once, here, with the predicate the whole package shares."""
    if ref is None:
        if required:
            _fail(f"{what} is required")
        return {"ref": None, "ref_kind": "absent"}
    _text(ref, what)
    kind, safe = references.classify(ref)
    return {"ref": safe, "ref_kind": kind}


# --- the frozen protocol ---------------------------------------------------------------------

def _read_protocol(payload):
    protocol = _obj(payload, "the comparison protocol")
    version = _field(protocol, "schema_version", "the comparison protocol")
    if version != SCHEMA_VERSION:
        _fail(f"unsupported comparison protocol schema_version {version!r}; this reader takes "
              f"{SCHEMA_VERSION}")
    if _field(protocol, "kind", "the comparison protocol") != PROTOCOL_KIND:
        _fail(f"the comparison protocol kind must be {PROTOCOL_KIND!r}")

    tz_name = _text(_field(protocol, "timezone", "the comparison protocol"), "protocol timezone")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        _fail(f"protocol timezone {tz_name!r} is not a known IANA timezone")

    created = _utc(_field(protocol, "created_utc", "the comparison protocol"), "created_utc")
    window = _obj(_field(protocol, "window", "the comparison protocol"), "protocol window")
    start = _utc(_field(window, "start_utc", "protocol window"), "window start_utc")
    hours = _field(window, "horizon_hours", "protocol window")
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1 or hours > MAX_HORIZON_HOURS:
        _fail("window horizon_hours must be a whole number of hours between 1 and "
              f"{MAX_HORIZON_HOURS}")
    if created > start:
        _fail("the protocol's created_utc is after its own window start; a protocol is frozen "
              "before the window it describes opens")

    return {
        "cohort_id": _text(_field(protocol, "cohort_id", "the comparison protocol"), "cohort_id"),
        "synthetic": _flag(_field(protocol, "synthetic", "the comparison protocol"), "synthetic"),
        # The same private key the arm manifests derived their handles under. Read, never written.
        "salt": R._salt(_field(protocol, "scope_fingerprint_salt", "the comparison protocol")),
        "created": created,
        "timezone": tz_name,
        "tz": tz,
        "start": start,
        "horizon_hours": hours,
        "horizon_end": start + timedelta(hours=hours),
        "policy": _read_policy(_obj(_field(protocol, "policy", "the comparison protocol"),
                                    "protocol policy")),
        "admission": _read_admission(_obj(_field(protocol, "budget_admission",
                                                 "the comparison protocol"), "budget_admission"),
                                     created),
        "work_classes": _read_work_classes(_field(protocol, "work_classes", "the comparison protocol")),
        "pairs": None,  # filled below, once the classes are known
        "raw_pairs": _field(protocol, "pairs", "the comparison protocol"),
    }


def _read_policy(policy):
    changed = _text(_field(policy, "changed_dimension", "protocol policy"), "changed_dimension")
    controls = _obj(_field(policy, "shared_controls", "protocol policy"), "shared_controls")
    for name, value in controls.items():
        _text(name, "each shared control name")
        _scalar(value, f"shared control {name!r}")
    if changed in controls:
        _fail("the changed_dimension also appears in shared_controls; a controlled dimension is "
              "not the one under test")
    baseline = _scalar(_field(policy, "baseline_value", "protocol policy"), "baseline_value")
    delegated = _scalar(_field(policy, "delegated_value", "protocol policy"), "delegated_value")
    if baseline == delegated:
        _fail("the baseline and delegated values must differ; otherwise the protocol changes nothing")

    # Evidence that the arms actually ran under those controls. It is an attestation - a reference
    # this document never opens - so a missing or withheld one leaves the controls gate UNKNOWN
    # rather than failed, and a present one never upgrades "declared" to "verified".
    out = {"changed_dimension": changed, "shared_controls": dict(controls),
           "baseline_value": baseline, "delegated_value": delegated,
           "controls_evidence": _reference(policy.get("controls_evidence_ref"),
                                           "policy controls_evidence_ref", required=False)}
    for key in ("monthly_llm_cap_usd", "pilot_incremental_cap_usd", "critical_reserve_usd"):
        raw = _field(policy, key, "protocol policy")
        _decimal_string(raw, f"protocol policy {key}")
        out[key] = raw
    out["required_checkpoints_per_day"] = _count(
        _field(policy, "required_checkpoints_per_day", "protocol policy"),
        "protocol policy required_checkpoints_per_day")
    out["required_minutes_per_day"] = _number(
        _field(policy, "required_minutes_per_day", "protocol policy"),
        "protocol policy required_minutes_per_day")
    return out


def _read_admission(admission, created):
    status = _field(admission, "status", "budget_admission")
    if status not in ADMISSION_STATUSES:
        _fail(f"budget_admission status must be one of {', '.join(ADMISSION_STATUSES)}")
    checked = admission.get("checked_utc")
    ref = admission.get("evidence_ref")
    if status in ATTESTED_STATUSES:
        if checked is None:
            _fail(f"budget_admission {status!r} must carry the checked_utc it was attested at")
        classified = _reference(ref, "budget_admission evidence_ref")
        if classified["ref"] is None:
            _fail("budget_admission evidence_ref was withheld as unsafe; an attestation must cite a "
                  "reference this report may carry")
    else:
        if checked is not None or ref is not None:
            _fail("budget_admission 'not-attested' carries no checked_utc and no evidence_ref")
        classified = {"ref": None, "ref_kind": "absent"}
    checked_at = _utc(checked, "budget_admission checked_utc", allow_none=True)
    # A budget check BEFORE the window opens is the right order to do it in, so this one is bounded
    # by the protocol's own creation instant rather than by the window. Earlier than that and the
    # protocol it attests did not exist yet.
    if checked_at is not None and checked_at < created:
        _fail("budget_admission checked_utc precedes the protocol's created_utc; an attestation "
              "cannot predate the protocol it attests")
    return {"status": status, "attested": status in ATTESTED_STATUSES,
            "checked": checked_at,
            "evidence": classified}


def _read_work_classes(raw):
    if not isinstance(raw, list) or not raw:
        _fail("the protocol must declare at least one work class")
    if len(raw) > MAX_WORK_CLASSES:
        _fail(f"the protocol declares {len(raw)} work classes; a frozen comparison takes at most two")
    classes = {}
    order = []
    for entry in raw:
        _obj(entry, "each work class")
        ident = _text(_field(entry, "id", "a work class"), "work class id")
        if ident in classes:
            _fail("a work class id appears twice")
        fields = _field(entry, "matching_fields", "a work class")
        if not isinstance(fields, list) or not fields:
            _fail("a work class must declare the matching_fields its pairs are matched on")
        for name in fields:
            _text(name, "each matching field name")
        if len(set(fields)) != len(fields):
            _fail("a work class matching_fields contains a duplicate name")
        oracle = _reference(_field(entry, "acceptance_oracle_ref", "a work class"),
                            "work class acceptance_oracle_ref")
        classes[ident] = {"id": ident, "matching_fields": list(fields), "oracle": oracle}
        order.append(ident)
    return {"by_id": classes, "order": order}


def _read_pairs(raw, work_classes, salt):
    if not isinstance(raw, list) or not raw:
        _fail("the protocol must declare at least one pair")
    if len(raw) > MAX_PAIRS:
        _fail(f"the protocol declares {len(raw)} pairs; a frozen comparison takes at most six")
    pairs, pair_ids, goal_ids, roots_seen = [], set(), set(), {}
    for entry in raw:
        _obj(entry, "each pair")
        pair_id = _text(_field(entry, "id", "a pair"), "pair id")
        if pair_id in pair_ids:
            _fail("a pair id appears twice; every pair is its own row")
        pair_ids.add(pair_id)
        class_id = _text(_field(entry, "work_class", "a pair"), "pair work_class")
        if class_id not in work_classes["by_id"]:
            _fail("a pair names a work class the protocol never declared")
        matched = _obj(_field(entry, "matched", "a pair"), "pair matched")
        expected = work_classes["by_id"][class_id]["matching_fields"]
        if sorted(matched) != sorted(expected):
            _fail("a pair's matched fields must be exactly its work class matching_fields; they are "
                  "never inferred and never extended")
        for name, value in matched.items():
            _scalar(value, f"pair matched field {name!r}")

        arms = {}
        for side in SIDES:
            arm = _obj(_field(entry, side, "a pair"), f"pair {side}")
            goal_id = _text(_field(arm, "goal_id", f"pair {side}"), f"pair {side} goal_id")
            if goal_id in goal_ids:
                _fail("a goal id appears on more than one arm; every arm is a distinct goal")
            goal_ids.add(goal_id)
            arm_roots = _field(arm, "roots", f"pair {side}")
            if not isinstance(arm_roots, list) or not arm_roots:
                _fail(f"pair {side} must name a non-empty list of source roots")
            for root in arm_roots:
                _text(root, "each arm source root")
            if len(set(arm_roots)) != len(arm_roots):
                _fail("an arm names the same source root twice")
            for root in arm_roots:
                if root in roots_seen:
                    _fail("a source root appears on more than one arm; baseline and delegated "
                          "scopes must not overlap, inside a pair or across pairs")
                roots_seen[root] = (pair_id, side)
            arms[side] = {"goal_id": goal_id, "roots": list(arm_roots),
                          "fingerprints": sorted(R.root_fingerprint(root, salt)
                                                 for root in arm_roots)}
        pairs.append({"id": pair_id, "work_class": class_id, "matched": dict(matched), **arms})
    # A class no pair uses renders a whole column of vacuous affirmatives - $0.00 priced complete,
    # coverage complete, four gates met - over no data at all. Refuse it at the protocol.
    unused = [ident for ident in work_classes["order"]
              if not any(entry["work_class"] == ident for entry in pairs)]
    if unused:
        _fail(f"{len(unused)} declared work class(es) are used by no pair; a class with no arms "
              "has no metrics, and drawing it would show an unknown as a zero")
    return pairs


# --- the observation ledger --------------------------------------------------------------------

def _read_ledger(payload, *, base, protocol_digest, protocol, now, destinations=()):
    ledger = _obj(payload, "the observation ledger")
    version = _field(ledger, "schema_version", "the observation ledger")
    if version != SCHEMA_VERSION:
        _fail(f"unsupported observation ledger schema_version {version!r}; this reader takes "
              f"{SCHEMA_VERSION}")
    if _field(ledger, "kind", "the observation ledger") != OBSERVATIONS_KIND:
        _fail(f"the observation ledger kind must be {OBSERVATIONS_KIND!r}")
    frozen = _sha256(_field(ledger, "protocol_sha256", "the observation ledger"),
                     "the observation ledger's protocol_sha256")
    if frozen != protocol_digest:
        _fail("the observation ledger's protocol_sha256 does not match the protocol file it was "
              "given; a frozen protocol is identified by its exact bytes")
    as_of = _utc(_field(ledger, "as_of_utc", "the observation ledger"), "as_of_utc")
    if as_of < protocol["created"]:
        _fail("the observation ledger's as_of_utc precedes the protocol it freezes")
    # The comparison reports what the ledger saw. It cannot be generated before the ledger looked.
    if as_of > now:
        _fail("the observation ledger's as_of_utc is after this comparison's generation instant; a "
              "document cannot be generated before the evidence it reports")

    raw = _field(ledger, "observations", "the observation ledger")
    if not isinstance(raw, list):
        _fail("the observation ledger's observations must be a list")
    observations = {}
    for entry in raw:
        _obj(entry, "each observation")
        goal_id = _text(_field(entry, "goal_id", "an observation"), "observation goal_id")
        if goal_id in observations:
            _fail("a goal has more than one observation; the ledger records each arm exactly once")
        path = _report_path(_field(entry, "report_path", "an observation"), base,
                            destinations)
        # The digest of the report FILE, frozen when the observation was collected. It is what
        # binds the ledger's attestations to the bytes they were taken from.
        frozen_report = _sha256(_field(entry, "report_sha256", "an observation"),
                                "an observation's report_sha256")
        defects = entry.get("escaped_defects")
        if defects is not None:
            _count(defects, "observation escaped_defects")
        defect_ref = entry.get("defect_evidence_ref")
        if defects is not None and defect_ref is None:
            _fail("an escaped_defects count must cite a defect evidence reference; a defect count "
                  "is an external attestation, never a derivation from an arm's status")
        classified = _reference(defect_ref, "observation defect_evidence_ref", required=False)
        observations[goal_id] = {
            "goal_id": goal_id, "path": path, "report_sha256": frozen_report,
            "escaped_defects": defects, "defect_evidence": classified,
            # The instant a non-acceptance was reached. A goal report has no such field, so this
            # is the ONLY place it can come from, and it is required before one counts.
            "terminal_at": _utc(entry.get("terminal_at_utc"), "observation terminal_at_utc",
                                allow_none=True),
            "terminal_evidence": _reference(entry.get("terminal_evidence_ref"),
                                            "observation terminal_evidence_ref", required=False),
        }

    interventions = R._read_interventions(_field(ledger, "interventions", "the observation ledger"))
    _check_attestation_windows(protocol, interventions, as_of)
    return {"as_of": as_of, "observations": observations, "interventions": interventions}


def _check_attestation_windows(protocol, interventions, as_of):
    """The ledger's OWN records must fall inside the window the ledger scopes, and inside the reach
    it declares. Two of the six evidence gates are built from them, so a check-in from before the
    window - or from after the cutoff the ledger itself names - would answer "did this pilot stay
    inside its caps" with a record nobody was looking at. These are refused rather than silently
    dropped: an out-of-window record is an input the author has to correct, not a rounding error.
    """
    start, horizon_end = protocol["start"], protocol["horizon_end"]
    for entry in interventions["entries"]:
        at = entry["at"]
        if at < start:
            _fail("an intervention at_utc falls before the protocol window opens; a scoped ledger "
                  "cites only what happened inside the window it declares")
        if at > horizon_end:
            _fail("an intervention at_utc falls after the protocol horizon closed")
        if at > as_of:
            _fail("an intervention at_utc falls after the observation ledger's own as_of_utc; the "
                  "ledger cannot cite a record it had not yet seen")
    checked = protocol["admission"]["checked"]
    if checked is not None and checked > as_of:
        _fail("budget_admission checked_utc falls after the observation ledger's as_of_utc; an "
              "attestation the ledger could not have seen is not present-tense evidence")


def _report_path(value, base, destinations=()):
    """The ledger's own paths are private. They are validated here and never reported.

    `destinations` are the resolved paths this run intends to WRITE. An arm report is a read-only
    input like the protocol and the ledger - and the one the ledger's whole integrity model rests
    on, since its frozen digest describes those exact bytes. The CLI guard cannot see these paths
    because they are only known once the ledger is parsed, so the check belongs here, before any
    of them is read and long before anything is written.
    """
    what = "an observation report_path"
    if not isinstance(value, str) or not value.strip():
        _fail(f"{what} must be a non-empty path relative to the ledger")
    if CONTROL_RE.search(value):
        _fail(f"{what} carries a control character")
    if "\\" in value:
        _fail(f"{what} carries a backslash; paths here are relative POSIX paths")
    if value.startswith(("/", "~")) or R.WINDOWS_DRIVE_RE.match(value):
        _fail(f"{what} must be relative to the ledger directory, never absolute")
    parts = value.split("/")
    if ".." in parts or "" in parts:
        _fail(f"{what} must not traverse out of the ledger directory")
    resolved = (base / value)
    try:
        inside = resolved.resolve().is_relative_to(base.resolve())
    except OSError:
        inside = False
    if not inside:
        _fail(f"{what} resolves outside the ledger directory")
    if not resolved.is_file():
        _fail("a goal report named by the observation ledger is not a readable regular file")
    if destinations:
        try:
            target = resolved.resolve()
        except OSError:
            target = resolved
        if target in destinations:
            _fail("an output path points at a goal report this ledger names; arm reports are "
                  "read-only inputs and overwriting one destroys the evidence the ledger froze")
    return resolved


# --- one arm's goal report ------------------------------------------------------------------------

def _read_frozen_report(path, frozen):
    """Read the report's bytes ONCE, check them against the digest the observation froze, and only
    then parse. A report edited after the ledger recorded it is refused, not read: every temporal
    claim below rests on the ledger having actually seen these bytes."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        _fail(f"cannot read the goal report: {error.strerror}")
    if hashlib.sha256(raw).hexdigest() != frozen:
        _fail("a goal report's bytes do not match the report_sha256 its observation froze; the "
              "report was edited after the ledger recorded it")
    try:
        return json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except UnicodeDecodeError:
        _fail("a goal report is not valid UTF-8")
    except json.JSONDecodeError as error:
        _fail(f"a goal report is not valid JSON (line {error.lineno}, column {error.colno})")


def _read_arm_report(observation, *, goal_id, expected_fingerprints, bounds, salt):
    payload = _read_frozen_report(observation["path"], observation["report_sha256"])
    rep = _obj(payload, "a goal report")
    if _field(rep, "schema_version", "a goal report") != R.SCHEMA_VERSION:
        _fail("a goal report carries an unsupported schema_version; this reader takes "
              f"{R.SCHEMA_VERSION}")
    if _field(rep, "kind", "a goal report") != "goal-report":
        _fail("a file named by the ledger is not a goal report")

    goal = _obj(_field(rep, "goal", "a goal report"), "goal report goal")
    if _text(_field(goal, "id", "goal report goal"), "goal report goal id") != goal_id:
        _fail("a goal report declares a different goal id than the observation that names it")
    synthetic = _flag(_field(goal, "synthetic", "goal report goal"), "goal report synthetic")

    # Nothing generated after the ledger's own cutoff may establish a result: the ledger could not
    # have seen it, so every attestation it makes about this arm is about older bytes.
    generated = _utc(_field(rep, "generated_utc", "a goal report"), "goal report generated_utc")
    if generated > bounds["as_of"]:
        _fail("a goal report was generated after the observation ledger's as_of_utc; a report the "
              "ledger could not have seen cannot establish a result")

    scope = _obj(_field(rep, "scope", "a goal report"), "goal report scope")
    # Every field below is re-validated with the REPORTER's own validators before it is compared or
    # hashed. Two of them used to reach `scope_fingerprint` untyped, and because a report is parsed
    # with parse_float=Decimal, a fractional JSON bound became a Decimal that `json.dumps` could not
    # serialize - a TypeError escaping as exit 1 with a traceback instead of a validation error.
    _text(_field(scope, "mode", "goal report scope"), "goal report scope mode")
    _text(_field(scope, "ownership", "goal report scope"), "goal report scope ownership")
    since = _day(scope.get("since"), "a goal report's scope since", allow_none=True)
    until = _day(scope.get("until"), "a goal report's scope until", allow_none=True)
    if since and until and since > until:
        _fail("a goal report's scope since falls after its until")
    if scope["mode"] != "whole-session-roots":
        _fail("every arm report must be scoped 'whole-session-roots'; a day-bounded scope answers a "
              "different question and cannot be compared arm to arm")
    if scope["ownership"] != "dedicated":
        _fail("every arm report must declare dedicated scope ownership; a shared or unknown scope "
              "cannot be qualified as this arm's own work")
    coverage_complete = _flag(_field(scope, "coverage_complete", "goal report scope"),
                              "goal report coverage_complete")
    # `coverage_complete` is one boolean over two independent conditions, so the cause has to be
    # read separately or the comparison can only say THAT coverage failed, never WHICH one did.
    roots_unresolved = _count(_field(scope, "roots_unknown", "goal report scope"),
                              "goal report roots_unknown") > 0
    allocation = _obj(_field(scope, "allocation_coverage", "goal report scope"),
                      "goal report allocation_coverage")
    allocation_known = _flag(_field(allocation, "known", "goal report allocation_coverage"),
                             "goal report allocation_coverage known")
    allocation_complete = allocation.get("complete")
    if allocation_known:
        allocation_complete = _flag(allocation_complete, "goal report allocation_coverage complete")
    elif allocation_complete is not None:
        _fail("a goal report declares its allocation coverage unknown but still carries a verdict")

    source = _obj(_field(rep, "source", "a goal report"), "goal report source")
    precision = _text(_field(source, "time_precision", "goal report source"),
                      "goal report source time_precision")
    if precision != R.SUPPORTED_TIME_PRECISION:
        _fail(f"a goal report declares engine time precision {precision!r}; this comparison reads "
              f"{R.SUPPORTED_TIME_PRECISION}")
    provenance = _read_provenance(source)
    fingerprints = _read_scope_fingerprints(source, scope, precision, expected_fingerprints, salt)

    outcome = _obj(_field(rep, "outcome", "a goal report"), "goal report outcome")
    status = _field(outcome, "status", "goal report outcome")
    if status not in R.STATUSES:
        _fail("a goal report carries an unrecognised outcome status")
    accepted_utc = _utc(outcome.get("accepted_utc"), "goal report accepted_utc", allow_none=True)

    time_section = _obj(_field(rep, "time", "a goal report"), "goal report time")
    # The START of the interval this comparison medians. It is read, validated and bounded like
    # the end of it; it is never exported, because the bound is what a reader needs, not the date.
    authorized_utc = _utc(_field(time_section, "authorized_utc", "goal report time"),
                          "goal report authorized_utc", allow_none=True)
    latency_known, latency = _read_latency(time_section, authorized_utc, accepted_utc)

    usage = _obj(_field(rep, "usage", "a goal report"), "goal report usage")
    tokens_known = _flag(_field(usage, "normalized_total_known", "goal report usage"),
                         "goal report normalized_total_known")

    money = _obj(_field(rep, "allocated_api_equivalent", "a goal report"),
                 "goal report allocated_api_equivalent")
    subtotal = _decimal_string(_field(money, "known_subtotal_usd", "goal report money"),
                               "a goal report's known_subtotal_usd")
    pricing_complete = _flag(_field(money, "pricing_complete", "goal report money"),
                             "goal report pricing_complete")
    whole_scope_covered = _flag(_field(money, "whole_source_scope_covered", "goal report money"),
                                "goal report whole_source_scope_covered")
    # A resolved scope that returned no priced row is an unknown, not a measured zero.
    rows_present = _count(_field(usage, "rows", "goal report usage"), "goal report usage rows") > 0

    # A goal report publishes the same coverage answer three times: the raw facts, the scope
    # verdict, and the money section's copy of it. Redundancy is checkable, and this one has to be
    # checked - an externally supplied report can be altered and THEN frozen into a ledger by its
    # exact bytes, and the gates read the raw fields while the PAGE reads the derived one. Left
    # unchecked, a forged boolean puts "whole source scope covered: yes" in a grid directly above a
    # rationale saying a root is unresolved.
    expected_coverage = (not roots_unresolved and allocation_known is True
                         and allocation_complete is True and bool(rows_present))
    if coverage_complete is not expected_coverage:
        _fail("a goal report's scope coverage_complete contradicts its own roots_unknown, "
              "allocation coverage and priced rows")
    if whole_scope_covered is not expected_coverage:
        _fail("a goal report's whole_source_scope_covered contradicts its own roots_unknown, "
              "allocation coverage and priced rows")

    if observation["terminal_at"] is not None and status not in TERMINAL_NON_ACCEPTANCE:
        _fail("an observation attests a terminal instant for an arm whose goal report is not a "
              f"terminal non-acceptance; it declares {status!r}")
    classification, latency_seconds, reason = _classify(
        status, accepted_utc, authorized_utc, latency_known, latency, observation, bounds)
    evidence_source = source.get("evidence_source")
    label = None
    if isinstance(evidence_source, dict):
        # Validated with the REPORTER's own rules, not a bare isinstance: this page RENDERS these
        # two strings, and G1 already holds them to `_text`. An empty or control-laden label
        # passing here and not there is a seam where the two schemas disagree.
        label = {"label": _text(_field(evidence_source, "label", "goal report evidence_source"),
                                "goal report evidence_source label"),
                 "revision": _text(evidence_source.get("revision"),
                                   "goal report evidence_source revision", allow_none=True)
                 if isinstance(evidence_source.get("revision"), str) else None}

    return {
        "goal_id": goal_id,
        "synthetic": synthetic,
        "status": status,
        "classification": classification,
        "latency_seconds": latency_seconds,
        "classification_reason": reason,
        # True only when an attested instant actually carried this arm to a terminal outcome.
        "terminal_at_attested": classification in TERMINAL_NON_ACCEPTANCE
        if status in TERMINAL_NON_ACCEPTANCE else None,
        "coverage_complete": coverage_complete,
        "roots_unresolved": roots_unresolved,
        "rows_present": rows_present,
        "allocation_known": allocation_known,
        "allocation_complete": allocation_complete,
        "pricing_complete": pricing_complete,
        "tokens_known": tokens_known,
        "subtotal": subtotal,
        "fingerprints": fingerprints,
        "provenance": provenance,
        "evidence_label": label,
    }


def _read_latency(time_section, authorized_utc, accepted_utc):
    """RE-DERIVE the duration rather than trust it.

    A goal report publishes three redundant things: the instant the work was authorized, the
    instant it was accepted, and the seconds between them. Redundancy is checkable, and this one
    has to be checked, because the number is the only input to the single improvement threshold in
    the whole document. An externally supplied report can be altered and THEN frozen into an
    observation ledger by its exact bytes - at which point every integrity check the ledger has
    will faithfully carry the edit. A five-day interval declared as one second would otherwise
    manufacture the 20% latency gate outright.

    The recomputation matches the reporter's own, truncation included: G1 writes
    `int(total_seconds())`, so a sub-second difference rounds toward zero and a stricter rule here
    would reject the reporter's own output. `human` is display and is never read - only the integer
    is arithmetic, so editing the string alone changes nothing and proves nothing.

    A mismatch is a malformed report, not a measurement outcome: it is refused, never absorbed
    into an unresolved arm where it would read as something the pilot observed.
    """
    to_acceptance = _obj(_field(time_section, "time_to_acceptance", "goal report time"),
                         "goal report time_to_acceptance")
    known = _flag(_field(to_acceptance, "known", "goal report time_to_acceptance"),
                  "goal report time_to_acceptance known")
    seconds = _field(to_acceptance, "seconds", "goal report time_to_acceptance")
    if not known:
        if seconds is not None:
            _fail("a goal report declares an unknown time_to_acceptance that still carries a "
                  "seconds value; an unknown duration has no number")
        return False, None
    seconds = _count(seconds, "goal report time_to_acceptance seconds")
    if authorized_utc is None or accepted_utc is None:
        _fail("a goal report declares a known time_to_acceptance without both of the instants it "
              "is measured between")
    if accepted_utc < authorized_utc:
        _fail("a goal report declares a known time_to_acceptance whose acceptance precedes its "
              "authorization")
    if seconds != int((accepted_utc - authorized_utc).total_seconds()):
        _fail("a goal report's time_to_acceptance seconds does not equal the interval between the "
              "two instants it publishes; the duration was altered after the report was written")
    return True, seconds


def _read_provenance(source):
    """A missing raw-byte digest is a missing receipt, never a wrong one.

    The goal reporter writes the snapshot digest ONLY when it read a file, and says so in its own
    note. A digest that contradicts that note is a hand-edited receipt and is refused; a report
    with neither is reported as a provenance limitation, which is not a mismatch.
    """
    digest = source.get("snapshot_sha256")
    note = source.get("snapshot_sha256_note")
    if digest is None:
        if note != R.NO_RAW_DIGEST_NOTE:
            _fail("a goal report's snapshot provenance note contradicts its absent raw digest")
        return "in-memory-object"
    _sha256(digest, "a goal report's source snapshot_sha256")
    if note != R.RAW_DIGEST_NOTE:
        _fail("a goal report's snapshot provenance note contradicts its raw digest")
    return "raw-bytes"


def _read_scope_fingerprints(source, scope, precision, expected, salt):
    fingerprints = source.get("scope")
    if not isinstance(fingerprints, dict):
        _fail("a goal report carries no source scope fingerprints; this comparison needs them to "
              "prove the arm was measured over the roots the protocol froze")
    # The algorithm is checked FIRST so a legacy unkeyed report gets its own cause instead of the
    # mismatch every other check would also report. An unsalted digest is a confirmation oracle
    # against a guessable root id, so it is refused rather than accepted as if it were opaque.
    if fingerprints.get("root_fingerprint_algorithm") != R.ROOT_FINGERPRINT_ALGORITHM:
        _fail("a goal report declares an unsupported root fingerprint algorithm; this comparison "
              f"reads only {R.ROOT_FINGERPRINT_ALGORITHM!r}, and an unkeyed handle is not opaque")
    declared = _field(fingerprints, "root_fingerprints", "goal report source scope")
    if not isinstance(declared, list) or not declared:
        _fail("a goal report's root_fingerprints must be a non-empty list")
    for digest in declared:
        _sha256(digest, "each goal report root fingerprint")
    if sorted(declared) != sorted(expected):
        _fail("a goal report's root fingerprints are not the ones its protocol arm declares under "
              "the protocol's own salt; the arm was measured over a different source selection, or "
              "under a different key")
    expected_scope = R.scope_fingerprint(sorted(declared), scope["mode"], scope.get("since"),
                                         scope.get("until"), precision, salt)
    if _sha256(_field(fingerprints, "scope_fingerprint", "goal report source scope"),
               "a goal report's scope_fingerprint") != expected_scope:
        _fail("a goal report's scope_fingerprint does not bind its own mode, bounds and precision")
    return sorted(declared)


def _classify(status, accepted_utc, authorized_utc, latency_known, latency, observation, bounds):
    """Place one arm against the protocol horizon, using only what the LEDGER observed.

    The generation clock decides one thing and one thing only, elsewhere: whether the window has
    opened. It never decides whether an arm was still open at the horizon - the ledger's own
    observation cutoff does, because that is how far anyone actually looked. And an instant the
    ledger could not have seen (after its cutoff) establishes nothing at all.

    BOTH ends of the measured interval are bounded. The acceptance was; the authorization was not,
    and an arm authorized months before the protocol opened could satisfy the one improvement
    threshold in the whole document on the strength of when its clock was started.
    """
    start, horizon_end, as_of = bounds["start"], bounds["horizon_end"], bounds["as_of"]
    if status == "accepted":
        if accepted_utc is None:
            return "unresolved", None, "acceptance-time-unknown"
        if accepted_utc < start:
            return "unresolved", None, "accepted-before-window"
        if accepted_utc > horizon_end:
            return "unresolved", None, "accepted-after-horizon"
        if accepted_utc > as_of:
            return "unresolved", None, "accepted-after-cutoff"
        # An accepted arm with no authorization instant has no interval at all; G1 already reports
        # its latency as unknown, and this says the same thing from the other side.
        if authorized_utc is None:
            return "unresolved", None, "acceptance-time-unknown"
        # The authorization bounds are checked BEFORE falling back to the generic
        # `acceptance-time-unknown`, so a violation names itself instead of hiding inside the same
        # cause G1 uses for a reversed pair. Within them, the window/horizon/cutoff comparisons come
        # before the acceptance one so each bound stays reachable: once `authorized <= accepted`
        # holds, the horizon and cutoff checks could never fire, because the acceptance cleared both.
        if authorized_utc < start:
            return "unresolved", None, "authorization-before-window"
        if authorized_utc > horizon_end:
            return "unresolved", None, "authorization-after-horizon"
        if authorized_utc > as_of:
            return "unresolved", None, "authorization-after-cutoff"
        if authorized_utc > accepted_utc:
            return "unresolved", None, "authorization-after-acceptance"
        if not latency_known:
            return "unresolved", None, "acceptance-time-unknown"
        return "accepted", latency, None
    if status in TERMINAL_NON_ACCEPTANCE:
        # A goal report states the status and never the instant. Without an external attestation
        # naming WHEN, a blocked arm three days into a fourteen-day window is indistinguishable
        # from one that failed for good - and G1 itself calls `blocked` open work.
        at, evidence = observation["terminal_at"], observation["terminal_evidence"]
        if at is None:
            return "unresolved", None, "terminal-attestation-missing"
        if evidence["ref"] is None:
            return ("unresolved", None, "terminal-evidence-withheld"
                    if evidence["ref_kind"] != "absent" else "terminal-attestation-missing")
        if at < start or at > horizon_end:
            return "unresolved", None, "terminal-instant-outside-window"
        if at > as_of:
            return "unresolved", None, "terminal-instant-after-cutoff"
        return status, None, None
    if status == "reopened":
        # The goal reporter calls `in_progress`, `blocked` and `reopened` open work. Two of those
        # are handled above; this one may have been accepted once already, and nothing here can
        # tell, so it is never a clean outcome in either direction.
        return "unresolved", None, "reopened-may-hide-acceptance"
    # `in_progress`: what the ledger last saw. If it looked at or after the horizon, that look is
    # the censoring observation; if it looked earlier, the arm is simply still pending.
    if as_of >= horizon_end:
        return "censored", None, "open-at-cutoff-after-horizon"
    return "pending", None, None


# --- attention, pilot-wide ------------------------------------------------------------------------

def _attention(protocol, interventions):
    """The pilot's own ledger, grouped by the protocol's timezone.

    This is NOT a goal report's attention section and is never merged into one: that counts a
    single goal's contribution to a day, this counts the whole pilot's required checkpoints.
    """
    policy = protocol["policy"]
    checkpoint_cap = policy["required_checkpoints_per_day"]
    minute_cap = policy["required_minutes_per_day"]
    days = {}
    for entry in interventions["entries"]:
        date = entry["at"].astimezone(protocol["tz"]).date().isoformat()
        day = days.setdefault(date, {"required_count": 0, "optional_count": 0,
                                     "required_minutes": Decimal(0), "optional_minutes": Decimal(0),
                                     "required_unknown": 0, "optional_unknown": 0})
        side = "required" if entry["required"] else "optional"
        day[f"{side}_count"] += 1
        if entry["minutes"] is None:
            day[f"{side}_unknown"] += 1
        else:
            day[f"{side}_minutes"] += entry["minutes"]

    rows, minutes_unknown, exceeded = [], False, False
    for date in sorted(days):
        day = days[date]
        if day["required_count"] and day["required_unknown"]:
            minutes_unknown = True
        if day["required_minutes"] > minute_cap:
            over_minutes = True
        elif day["required_unknown"]:
            over_minutes = None
        else:
            over_minutes = False
        over_checkpoints = day["required_count"] > checkpoint_cap
        exceeded = exceeded or over_checkpoints or over_minutes is True
        rows.append({
            "date": date,
            "required_count": day["required_count"],
            "optional_count": day["optional_count"],
            "required_minutes_known": _number_out(day["required_minutes"]),
            "optional_minutes_known": _number_out(day["optional_minutes"]),
            "required_minutes_unknown_count": day["required_unknown"],
            "optional_minutes_unknown_count": day["optional_unknown"],
            "required_checkpoints_exceeds_cap": over_checkpoints,
            "required_minutes_exceeds_cap": over_minutes,
        })

    # The caps are about REQUIRED attention. A ledger that recorded only optional check-ins has
    # rows, no exceedance and no unknown minutes - and still establishes nothing about the
    # required caps. That reads `met` only if "no required day was recorded" is read as zero.
    required_days = sum(1 for row in rows if row["required_count"])
    if not required_days:
        within = None
    elif exceeded:
        within = False
    elif minutes_unknown:
        within = None
    else:
        within = True

    return {
        "scope": "pilot-wide",
        "timezone": protocol["timezone"],
        "required_checkpoints_per_day": checkpoint_cap,
        "required_minutes_per_day": _number_out(minute_cap),
        "intervention_count": len(interventions["entries"]),
        "deduped_count": interventions["deduped"],
        "days": rows,
        "recorded_day_count": len(rows),
        "required_day_count": required_days,
        "unrecorded_days": None,
        "unrecorded_days_note": "a calendar day with no record is NOT RECORDED, never zero",
        "within_caps": within,
        "minutes_unknown": minutes_unknown,
        "exceeded": exceeded,
        "note": ("the whole pilot's required checkpoints, never one goal's own contribution: a "
                 "per-goal report answers a different question and is never merged into this."),
    }, minutes_unknown


# --- arithmetic over the arms ----------------------------------------------------------------------

def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / 2


def _side_summary(arms):
    counts = {name: 0 for name in CLASSIFICATIONS}
    for arm in arms:
        counts[arm["classification"]] += 1
    accepted = [arm for arm in arms if arm["classification"] == "accepted"]
    median = _median([arm["latency_seconds"] for arm in accepted])
    attested = [arm for arm in arms if arm["escaped_defects"] is not None]
    total_defects = sum(arm["escaped_defects"] for arm in attested)
    subtotal = sum((arm["subtotal"] for arm in arms), Decimal(0))

    # A rate divides by the population the experiment was run over. An arm nobody could place
    # against the horizon has an unknown numerator - it may yet have been accepted, which is
    # precisely why it is unresolved - so the rate is UNKNOWN, not computed. Dropping that arm to
    # make the number appear would quietly change the population instead, so the denominator
    # stays whole and the count of arms actually placed travels beside it.
    unresolved = counts["unresolved"]
    known_arms = len(arms) - unresolved
    acceptance_reason = "unresolved-arms" if unresolved else None
    # The same rule, plus one more: an arm that never attested a defect count is not an arm with
    # zero defects, and averaging over the ones that did attest credits it with the mean.
    if unresolved:
        defect_reason = "unresolved-arms"
    elif len(attested) != len(arms):
        defect_reason = "unattested-arms"
    else:
        defect_reason = None

    return {
        "arm_count": len(arms),
        "classification": counts,
        "acceptance": {
            "accepted": len(accepted),
            "arm_count": len(arms),
            "known_arm_count": known_arms,
            "unresolved_arm_count": unresolved,
            "rate_percent": None if acceptance_reason or not arms else
            str((Decimal(len(accepted)) * 100 / Decimal(len(arms)))
                .quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
            "reason": acceptance_reason,
        },
        # Every outcome count travels with the median, not just the two that flatter it. A
        # sentence naming only censored and pending hides the arms nobody could place at all.
        "latency": {
            "accepted_only": True,
            "median_seconds": _number_out(median),
            "median_human": _duration(median) if median is not None else None,
            "accepted_denominator": len(accepted),
            "arm_count": len(arms),
            "censored": counts["censored"],
            "pending": counts["pending"],
            "unresolved": counts["unresolved"],
            "counts_summary": " · ".join(
                [f"accepted {counts['accepted']}/{len(arms)}"]
                + [f"{name} {counts[name]}" for name in CLASSIFICATIONS if name != "accepted"]),
            "qualification": (
                f"accepted-only median over {len(accepted)} of {len(arms)} arms. Outcomes: "
                + ", ".join(f"{name} {counts[name]}" for name in CLASSIFICATIONS)
                + ". It describes the arms that reached an ATTESTED acceptance inside the horizon "
                  "and nothing else."),
        },
        "defects": {
            "known": len(attested),
            "unknown": len(arms) - len(attested),
            "attested_arms": len(attested),
            "arm_count": len(arms),
            "known_arm_count": known_arms,
            "unresolved_arm_count": unresolved,
            "total_escaped": total_defects,
            "rate_per_arm": None if defect_reason else
            str((Decimal(total_defects) / Decimal(len(attested)))
                .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "reason": defect_reason,
            "note": "an external attestation tied to an evidence reference, never inferred from a "
                    "goal's status. The total counts every attestation the ledger made; the rate "
                    "is published only when every arm in the side both resolved and attested.",
        },
        "allocated_api_equivalent": {
            "label": R.MONEY_LABEL,
            "known_subtotal_usd": str(subtotal),
            "display_usd": str(subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "is_cash": False,
            "pricing_complete": all(arm["pricing_complete"] for arm in arms),
            "coverage_complete": all(arm["coverage_complete"] for arm in arms),
            "normalized_tokens_known": all(arm["tokens_known"] for arm in arms),
            "qualification": "a descriptive known priced subtotal of allocated API-equivalent "
                             "value; not cash, and never a cash-admission calculation",
        },
        "_median": median,
        "_accepted": len(accepted),
        "_attested": len(attested),
        "_defects": total_defects,
    }


def _gates(entry, admission, controls, attention, arms):
    baseline, delegated = entry["arms"]["baseline"], entry["arms"]["delegated"]
    # An arm nobody could place against the horizon cannot be counted in a rate that divides by
    # arms. Every directional answer that would count it is therefore UNKNOWN, not computed over
    # the arms that happened to resolve.
    unresolved = any(arm["classification"] == "unresolved" for arm in arms)
    values = {}
    values["fully-observed-pairs"] = entry["fully_observed_pairs"] >= MIN_FULLY_OBSERVED_PAIRS
    # Tri-state, for the same reason `controls-attested` is: an unresolved root or an unpriced row
    # is a shortfall somebody can point at, but an allocation verdict that is incomplete or absent
    # means the selection does not hold all its own work - nobody claimed the arms cover less, so a
    # flat `false` would be a directional claim the evidence does not support.
    # A rowless arm is grouped with the allocation gaps, not the hard ones: nobody claimed it
    # covers less or priced it wrongly - there is simply nothing there to have priced.
    hard_gap = any(arm["roots_unresolved"] or arm["rows_present"] and (
                       not arm["pricing_complete"] or not arm["tokens_known"])
                   for arm in arms)
    allocation_gap = any(not arm["allocation_known"] or arm["allocation_complete"] is not True
                         or not arm["rows_present"] for arm in arms)
    values["source-coverage-complete"] = False if hard_gap else (None if allocation_gap else True)
    values["budget-admission-attested"] = admission["attested"]
    # Absent or withheld controls evidence is UNKNOWN, never a failure: nobody claimed the arms
    # ran differently, only that nothing attests they ran the same.
    values["controls-attested"] = True if controls["ref"] is not None else None
    values["attention-within-caps"] = attention["within_caps"]
    defects_known = all(arm["escaped_defects"] is not None for arm in arms)
    values["defect-attestations-known"] = defects_known

    # The gate reads the SAME verdict the published rate reached. Recomputing the condition here
    # is how a page ends up printing a percentage beside a gate that says the rate is unknown.
    if baseline["acceptance"]["reason"] or delegated["acceptance"]["reason"]:
        values["delegated-acceptance-not-lower"] = None
    else:
        values["delegated-acceptance-not-lower"] = (
            delegated["_accepted"] * baseline["arm_count"]
            >= baseline["_accepted"] * delegated["arm_count"])

    if baseline["defects"]["reason"] or delegated["defects"]["reason"]:
        values["delegated-defect-rate-not-higher"] = None
    else:
        values["delegated-defect-rate-not-higher"] = (
            delegated["_defects"] * baseline["_attested"] <= baseline["_defects"] * delegated["_attested"])

    if unresolved or baseline["_median"] is None or delegated["_median"] is None:
        values["delegated-latency-lower"] = None
    else:
        values["delegated-latency-lower"] = delegated["_median"] <= baseline["_median"] * LATENCY_IMPROVEMENT
    return [{"code": code, "met": values[code], "kind": GATE_KIND[code], "text": GATE_TEXT[code]}
            for code in GATE_ORDER]


def _class_rationale(gates, attention, arms, synthetic, started, pending):
    codes = []
    if not started:
        codes.append("window-not-open")
    if pending:
        codes.append("arms-pending")
    if any(arm["classification"] == "unresolved" for arm in arms):
        codes.append("arms-unresolved")
    answers = {gate["code"]: gate["met"] for gate in gates}
    if answers["fully-observed-pairs"] is False:
        codes.append("fully-observed-pairs-below-minimum")
    # Each cause names itself, and an arm can carry more than one. A single code covering all of
    # them described only the first one anyone had thought of.
    if answers["source-coverage-complete"] is not True:
        if any(arm["roots_unresolved"] for arm in arms):
            codes.append("requested-roots-unresolved")
        if any(arm["allocation_known"] and arm["allocation_complete"] is False for arm in arms):
            codes.append("source-allocation-outside-scope")
        if any(not arm["allocation_known"] for arm in arms):
            codes.append("source-allocation-coverage-unknown")
        if any(not arm["rows_present"] for arm in arms):
            codes.append("no-priced-rows")
        if any(arm["rows_present"] and not arm["pricing_complete"] for arm in arms):
            codes.append("pricing-incomplete")
        if not all(arm["tokens_known"] for arm in arms):
            codes.append("token-totals-unknown")
    if answers["budget-admission-attested"] is False:
        codes.append("budget-admission-not-attested")
    if answers["controls-attested"] is not True:
        codes.append("controls-not-attested")
    if answers["attention-within-caps"] is not True:
        if not attention["recorded_day_count"]:
            codes.append("attention-not-recorded")
        if not attention["required_day_count"]:
            codes.append("required-attention-not-recorded")
        if attention["minutes_unknown"]:
            codes.append("required-minutes-unknown")
        if any(day["required_checkpoints_exceeds_cap"] for day in attention["days"]):
            codes.append("checkpoint-cap-exceeded")
        if any(day["required_minutes_exceeds_cap"] is True for day in attention["days"]):
            codes.append("minute-cap-exceeded")
    if answers["defect-attestations-known"] is False:
        codes.append("defect-attestation-unknown")
    if answers["delegated-acceptance-not-lower"] is False:
        codes.append("delegated-acceptance-rate-lower")
    if answers["delegated-defect-rate-not-higher"] is False:
        codes.append("delegated-defect-rate-higher")
    elif answers["delegated-defect-rate-not-higher"] is None:
        codes.append("defect-rate-unknown")
    if answers["delegated-latency-lower"] is False:
        codes.append("latency-improvement-below-threshold")
    elif answers["delegated-latency-lower"] is None:
        codes.append("latency-median-unknown")
    if all(gate["met"] is True for gate in gates):
        codes.append("all-gates-met")
    if synthetic:
        codes.append("synthetic-inputs")
    return codes


def _class_status(gates, synthetic, started, pending):
    if not started:
        return "not-started"
    if pending:
        return "observing"
    # Sliced by KIND, never by index: inserting a gate used to move one silently across the line.
    evidence = [gate for gate in gates if gate["kind"] == "evidence"]
    directional = [gate for gate in gates if gate["kind"] == "directional"]
    # Synthetic input decides nothing in EITHER direction. A decisive negative on fabricated data
    # is the same illusion as a decisive positive, pointed the other way.
    if synthetic:
        return "inconclusive"
    if all(gate["met"] is True for gate in gates):
        return "candidate"
    # A clean observed failure: every piece of evidence is in hand and the comparison still went
    # the other way. Anything ambiguous is inconclusive - never a negative verdict, never a promotion.
    if all(gate["met"] is True for gate in evidence) and any(gate["met"] is False for gate in directional):
        return "not-candidate"
    return "inconclusive"


def _overall_status(classes):
    """A cohort headline is the weakest of its classes, never the most flattering one."""
    states = [entry["status"] for entry in classes]
    if all(state == "not-started" for state in states):
        return "not-started", None
    if "inconclusive" in states:
        return "inconclusive", None
    if "observing" in states or "not-started" in states:
        return "observing", None
    if all(state == "candidate" for state in states):
        return "candidate", None
    if all(state == "not-candidate" for state in states):
        return "not-candidate", None
    # One class candidate, another decisively not. Headlining either hands the reader the half of
    # the evidence that agrees with it.
    return "inconclusive", "mixed-class-results"


# --- the report -------------------------------------------------------------------------------------

def _build(protocol, ledger, *, now, protocol_digest, ledger_digest):
    horizon_end = protocol["horizon_end"]
    declared = {}
    for entry in protocol["pairs"]:
        for side in SIDES:
            declared[entry[side]["goal_id"]] = entry[side]
    missing = sorted(set(declared) - set(ledger["observations"]))
    if missing:
        _fail(f"{len(missing)} protocol goal(s) have no observation; the ledger records every arm "
              "exactly once")
    stranger = sorted(set(ledger["observations"]) - set(declared))
    if stranger:
        _fail(f"{len(stranger)} observation(s) name a goal the protocol never declared")

    bounds = {"start": protocol["start"], "horizon_end": horizon_end, "as_of": ledger["as_of"]}
    arms_by_goal = {}
    for goal_id, arm in declared.items():
        observation = ledger["observations"][goal_id]
        read = _read_arm_report(observation, goal_id=goal_id,
                                expected_fingerprints=arm["fingerprints"],
                                bounds=bounds, salt=protocol["salt"])
        read["escaped_defects"] = observation["escaped_defects"]
        read["defect_evidence"] = observation["defect_evidence"]
        read["terminal_evidence"] = observation["terminal_evidence"]
        arms_by_goal[goal_id] = read

    partition = _partition(declared, arms_by_goal)
    attention, minutes_unknown = _attention(protocol, ledger["interventions"])
    synthetic = protocol["synthetic"] or any(arm["synthetic"] for arm in arms_by_goal.values())
    # The generation clock decides ONE thing: whether the window has opened. Everything about what
    # was observed is decided by the ledger's own cutoff.
    started = now >= protocol["start"]
    controls = protocol["policy"]["controls_evidence"]

    classes = []
    for class_id in protocol["work_classes"]["order"]:
        pairs = [entry for entry in protocol["pairs"] if entry["work_class"] == class_id]
        classes.append(_build_class(protocol["work_classes"]["by_id"][class_id], pairs, arms_by_goal,
                                    protocol["admission"], controls, attention, synthetic, started))

    status, mixed = _overall_status(classes)
    rationale = sorted({item["code"] for entry in classes for item in entry["rationale"]})
    if mixed:
        rationale.append(mixed)
    if synthetic and "synthetic-inputs" not in rationale:
        rationale.append("synthetic-inputs")

    all_arms = list(arms_by_goal.values())
    codes = ["allocated-not-cash", "source-allocation-not-attribution", "acceptance-is-attestation",
             "matching-is-declared-not-verified", "controls-attested-not-verified",
             "small-sample-no-statistical-weight", "utc-day-precision", "report-bytes-frozen",
             "ledger-references-private", "no-promotion"]
    if synthetic:
        codes.append("synthetic-inputs")
    if any(arm["classification"] == "unresolved" for arm in all_arms):
        codes.append("unresolved-observations")
    if any(arm["classification"] == "censored" for arm in all_arms):
        codes += ["censored-observations", "censored-attested-at-cutoff"]
    if any(arm["classification"] == "pending" for arm in all_arms):
        codes.append("pending-observations")
    if any(arm["terminal_at_attested"] for arm in all_arms):
        codes.append("terminal-instant-attested-not-verified")
    if any(arm["escaped_defects"] is None for arm in all_arms):
        codes.append("defect-attestation-unknown")
    if minutes_unknown:
        codes.append("required-minutes-unknown")
    codes.append("intervention-days-not-recorded")
    if any(arm["provenance"] == "in-memory-object" for arm in all_arms):
        codes.append("source-provenance-unfingerprinted")
    if protocol["admission"]["attested"]:
        codes.append("budget-admission-is-attestation")
    else:
        codes.append("budget-admission-not-attested")
    withheld = [entry["oracle"] for entry in protocol["work_classes"]["by_id"].values()
                if entry["oracle"]["ref"] is None and entry["oracle"]["ref_kind"] != "absent"]
    withheld += [ref for arm in all_arms for ref in (arm["defect_evidence"], arm["terminal_evidence"])
                 if ref["ref"] is None and ref["ref_kind"] != "absent"]
    if controls["ref"] is None and controls["ref_kind"] != "absent":
        withheld.append(controls)
    if withheld:
        codes.append("evidence-ref-withheld")

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_utc": _utc_out(now),
        "status": status,
        "synthetic": synthetic,
        "promotion": {"promoted": False, "approved": False, "note": NO_PROMOTION_NOTE},
        "rationale": [{"code": code, "text": RATIONALE_TEXT[code]} for code in rationale
                      if code in RATIONALE_TEXT],
        "protocol": {
            "cohort_id": protocol["cohort_id"],
            "synthetic": protocol["synthetic"],
            "timezone": protocol["timezone"],
            "created_utc": _utc_out(protocol["created"]),
            "window": {"start_utc": _utc_out(protocol["start"]),
                       "horizon_hours": protocol["horizon_hours"],
                       "end_utc": _utc_out(horizon_end)},
            "changed_dimension": protocol["policy"]["changed_dimension"],
            "baseline_value": protocol["policy"]["baseline_value"],
            "delegated_value": protocol["policy"]["delegated_value"],
            "shared_controls": protocol["policy"]["shared_controls"],
            "work_class_count": len(protocol["work_classes"]["order"]),
            "pair_count": len(protocol["pairs"]),
            "arm_count": len(protocol["pairs"]) * 2,
            "policy": {key: protocol["policy"][key] for key in
                       ("monthly_llm_cap_usd", "pilot_incremental_cap_usd", "critical_reserve_usd")}
            | {"required_checkpoints_per_day": protocol["policy"]["required_checkpoints_per_day"],
               "required_minutes_per_day": _number_out(protocol["policy"]["required_minutes_per_day"])},
        },
        "budget_admission": {
            "status": protocol["admission"]["status"],
            "attested": protocol["admission"]["attested"],
            "checked_utc": _utc_out(protocol["admission"]["checked"]),
            "evidence": protocol["admission"]["evidence"],
            "provider_checked": False,
            "note": ("an attestation carried from the protocol. Nothing here contacted a provider, "
                     "read a subscription or checked a balance."),
        },
        "controls": {
            "changed_dimension": protocol["policy"]["changed_dimension"],
            "baseline_value": protocol["policy"]["baseline_value"],
            "delegated_value": protocol["policy"]["delegated_value"],
            "shared_controls": protocol["policy"]["shared_controls"],
            "attested": controls["ref"] is not None,
            "verified": False,
            "evidence": controls,
            "note": ("attested, NOT VERIFIED: the shared controls are declared in the protocol and "
                     "cited by a reference this document never opened. Nothing here observed the "
                     "model an arm used, the review it got, or the route it ran on."),
        },
        "cash": {"spend_usd": None, "headroom_usd": None, "status": CASH_STATUS,
                 "note": "no cash figure is derived here, with or without a budget attestation"},
        "elapsed": {
            "measure": "time to acceptance: each goal's own declared authorization to its own "
                       "declared acceptance, exactly as its goal report computed it",
            "accepted_only": True,
            "observation_cutoff_utc": _utc_out(ledger["as_of"]),
            "censoring": "an arm the ledger last saw open, at a cutoff falling at or after the "
                         "horizon, is censored and contributes no latency. An arm whose outcome "
                         "cannot be dated inside the window at all is UNRESOLVED, not censored "
                         "and never a clean outcome",
            "bounds": "an acceptance or an attested terminal instant counts only when it falls at "
                      "or after the window start, at or before the horizon end, and at or before "
                      "the ledger's observation cutoff",
            "horizon_end_utc": _utc_out(horizon_end),
            "engine_time_precision": R.SUPPORTED_TIME_PRECISION,
            "note": "every instant here is an attestation - from a goal manifest, or from the "
                    "observation ledger. None is derived from a clock, a file time or a session "
                    "timestamp, and the document's own generation clock decides nothing but "
                    "whether the window has opened",
        },
        "source_roots": partition,
        "classes": classes,
        "attention": attention,
        "sources": _sources(protocol, ledger, all_arms, protocol_digest, ledger_digest),
        "limitations": [{"code": code, "text": LIMITATION_TEXT[code]}
                        for code in dict.fromkeys(codes) if code in LIMITATION_TEXT],
    }


def _partition(declared, arms_by_goal):
    seen, overlaps = {}, 0
    for goal_id, arm in declared.items():
        for digest in arms_by_goal[goal_id]["fingerprints"]:
            if digest in seen:
                overlaps += 1
            seen[digest] = goal_id
    total = sum(len(arms_by_goal[goal_id]["fingerprints"]) for goal_id in declared)
    return {
        "arm_count": len(declared),
        "fingerprint_count": len(seen),
        "overlapping_arm_pairs": overlaps,
        "partition_exact": overlaps == 0 and len(seen) == total,
        "algorithm": R.ROOT_FINGERPRINT_ALGORITHM,
        "note": ("counts of keyed handles only - neither a raw root id nor a handle value enters "
                 "this document. This restates a validation that already refused the alternative: "
                 "overlapping roots never reach this point. It is a receipt that the arms did not "
                 "share a source, not a measurement of the data, and it does not prove a goal "
                 "produced the turns counted under its own roots."),
    }


def _build_class(work_class, pairs, arms_by_goal, admission, controls, attention, synthetic,
                 started):
    sides = {side: [arms_by_goal[entry[side]["goal_id"]] for entry in pairs] for side in SIDES}
    arms = sides["baseline"] + sides["delegated"]
    rows, fully_observed = [], 0
    for entry in pairs:
        cells = {}
        for side in SIDES:
            arm = arms_by_goal[entry[side]["goal_id"]]
            cells[side] = {
                "goal_id": arm["goal_id"],
                "classification": arm["classification"],
                "classification_reason": arm["classification_reason"],
                "classification_reason_text": UNRESOLVED_REASONS.get(arm["classification_reason"]),
                "terminal_at_attested": arm["terminal_at_attested"],
                "latency_seconds": arm["latency_seconds"],
                "latency_human": _duration(arm["latency_seconds"])
                if arm["latency_seconds"] is not None else None,
                "escaped_defects": arm["escaped_defects"],
                "defect_evidence_kind": arm["defect_evidence"]["ref_kind"],
            }
        # An UNRESOLVED arm settles nothing, so its pair is no more observed than a pending one.
        observed = all(cells[side]["classification"] not in ("pending", "unresolved")
                       for side in SIDES)
        fully_observed += observed
        rows.append({"id": entry["id"], "matched": entry["matched"], "fully_observed": observed,
                     **cells})

    summaries = {side: _side_summary(sides[side]) for side in SIDES}
    entry = {
        "id": work_class["id"],
        "matching_fields": work_class["matching_fields"],
        "acceptance_oracle": work_class["oracle"],
        "pair_count": len(pairs),
        "fully_observed_pairs": fully_observed,
        "arms": summaries,
        "pairs": rows,
    }
    gates = _gates(entry, admission, controls, attention, arms)
    answers = {gate["code"]: gate["met"] for gate in gates}
    pending = any(arm["classification"] == "pending" for arm in arms)
    entry["gates"] = gates
    entry["status"] = _class_status(gates, synthetic, started, pending)
    entry["rationale"] = [{"code": code, "text": RATIONALE_TEXT[code]}
                          for code in _class_rationale(gates, attention, arms, synthetic,
                                                       started, pending)]
    entry["unresolved_arms"] = sum(1 for arm in arms if arm["classification"] == "unresolved")
    entry["comparison"] = {
        "acceptance_rate_not_lower": answers["delegated-acceptance-not-lower"],
        "defect_rate_not_higher": answers["delegated-defect-rate-not-higher"],
        "latency_improvement_met": answers["delegated-latency-lower"],
        "latency_threshold": str(LATENCY_IMPROVEMENT),
        "required_delegated_median_seconds": _number_out(
            summaries["baseline"]["_median"] * LATENCY_IMPROVEMENT)
        if summaries["baseline"]["_median"] is not None else None,
    }
    for side in SIDES:
        for private in ("_median", "_accepted", "_attested", "_defects"):
            summaries[side].pop(private)
    return entry


def _sources(protocol, ledger, arms, protocol_digest, ledger_digest):
    labels, seen = [], set()
    for arm in arms:
        label = arm["evidence_label"]
        if label and (label["label"], label["revision"]) not in seen:
            seen.add((label["label"], label["revision"]))
            labels.append(label)
    defect_refs = [arm["defect_evidence"] for arm in arms]
    terminal_refs = [arm["terminal_evidence"] for arm in arms]
    return {
        "protocol_sha256": protocol_digest,
        "observations_sha256": ledger_digest,
        "note": ("these two digests are PRIVATE INPUT integrity fingerprints of the protocol and "
                 "ledger files, over the exact bytes that were parsed. They fingerprint the inputs; "
                 "they are not evidence of any result."),
        "cohort_id": protocol["cohort_id"],
        "as_of_utc": _utc_out(ledger["as_of"]),
        "acceptance_oracles": [{"work_class": entry["id"], **entry["oracle"]}
                               for entry in protocol["work_classes"]["by_id"].values()],
        "evidence_labels": sorted(labels, key=lambda item: (item["label"], item["revision"] or "")),
        "report_provenance": {
            "raw_bytes": sum(1 for arm in arms if arm["provenance"] == "raw-bytes"),
            "in_memory_object": sum(1 for arm in arms if arm["provenance"] == "in-memory-object"),
            "bytes_frozen": len(arms),
            "note": "every goal report was read once and checked against the digest its own "
                    "observation froze. This column is a different question: whether each report's "
                    "UPSTREAM cost snapshot carried a raw-byte digest of its own. Neither digest "
                    "value is reproduced here, and the two are never compared to each other.",
        },
        "terminal_evidence": {
            "cited": sum(1 for ref in terminal_refs if ref["ref_kind"] != "absent"),
            "absent": sum(1 for ref in terminal_refs if ref["ref_kind"] == "absent"),
            "withheld": sum(1 for ref in terminal_refs
                            if ref["ref"] is None and ref["ref_kind"] != "absent"),
            "note": "how many arms cite evidence for when their non-acceptance happened. The "
                    "references themselves stay in the ledger.",
        },
        "defect_evidence": {
            "cited": sum(1 for ref in defect_refs if ref["ref_kind"] != "absent"),
            "absent": sum(1 for ref in defect_refs if ref["ref_kind"] == "absent"),
            "withheld": sum(1 for ref in defect_refs
                            if ref["ref"] is None and ref["ref_kind"] != "absent"),
            "note": "the ledger's own references never leave it. Only whether an attestation cites "
                    "one, and its safety class, are reported.",
        },
    }


# --- public face ---------------------------------------------------------------------------------------

def load_comparison(protocol_path: Path, observations_path: Path, *, now_utc: str,
                    destinations=()) -> dict:
    """Read the frozen protocol and the observation ledger from disk and build ONE comparison.

    Each file is read once, so the digest this report carries and the content it describes can
    never be two different versions of the same file. Inputs are read-only, and neither path -
    nor any path inside the ledger - reaches the output.

    `destinations` are the resolved paths the caller will write. They are checked against every arm
    report the ledger names, because those are inputs too and the CLI cannot know them in advance.
    """
    try:
        now = _utc(now_utc, "now_utc")
        protocol_raw, protocol_payload = R._read_json(Path(protocol_path), "comparison protocol")
        ledger_raw, ledger_payload = R._read_json(Path(observations_path), "observation ledger")
        protocol = _read_protocol(protocol_payload)
        protocol["pairs"] = _read_pairs(protocol.pop("raw_pairs"), protocol["work_classes"],
                                        protocol["salt"])
        protocol_digest = hashlib.sha256(protocol_raw).hexdigest()
        ledger = _read_ledger(ledger_payload, base=Path(observations_path).parent,
                              destinations=destinations,
                              protocol_digest=protocol_digest, protocol=protocol, now=now)
        return _build(protocol, ledger, now=now, protocol_digest=protocol_digest,
                      ledger_digest=hashlib.sha256(ledger_raw).hexdigest())
    except ComparisonError:
        raise
    except R.ReportError as error:
        # Every primitive this module reuses raises the package's own error. Re-raising it as a
        # comparison error keeps one cause and one exit path, and never adds a payload to it.
        raise ComparisonError(str(error)) from None


def write_comparison(comparison: dict, out: Path) -> None:
    """Write the comparison atomically: private temp file in the same directory, fsync, replace."""
    try:
        payload = json.dumps(comparison, indent=2, ensure_ascii=False) + "\n"
    except TypeError:
        raise ComparisonError("the comparison contains a value that cannot be written as JSON") from None
    R.write_text_atomic(payload, out)

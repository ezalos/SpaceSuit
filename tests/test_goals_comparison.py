# ABOUTME: Tests for the frozen delegation-comparison protocol: schema limits, freeze hashing, root
# ABOUTME: fingerprint partition, horizon classification, candidacy gates, privacy and the CLI.

import copy
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from goals import comparison as C
from goals import comparison_view as V
from goals import report as R
from tests.test_goals_report import OBFUSCATED, cost, coverage, goal, row

ROOT = Path(__file__).resolve().parent.parent

# The protocol window opens 2026-09-17 and runs 336 hours, so the horizon ends 2026-10-01.
WINDOW_START = "2026-09-17T00:00:00Z"
HORIZON_END = "2026-10-01T00:00:00Z"
AS_OF = "2026-10-02T00:00:00Z"
NOW = "2026-10-03T00:00:00Z"
# The instant every arm's own G1 report was generated at. Never the comparison's `now`.
REPORT_NOW = "2026-10-01T12:00:00Z"
# Inside the window, before the horizon closes: where an open arm is still PENDING.
OPEN_NOW = "2026-09-25T00:00:00Z"
OPEN_AS_OF = "2026-09-24T00:00:00Z"

BASELINE_ACCEPTED = "2026-09-22T00:00:00Z"   # 5 days after authorization -> 432000s
DELEGATED_ACCEPTED = "2026-09-20T00:00:00Z"  # 3 days after authorization -> 259200s
AUTHORIZED = "2026-09-17T00:00:00Z"
# A terminal instant inside the window, inside the horizon and at or before either cutoff.
TERMINAL_AT = "2026-09-23T00:00:00Z"
TERMINAL_REF = "evidence/terminal.txt"
# The private key the root handles are derived under. Never leaves the manifest or the protocol.
SALT = "g2-synthetic-scope-salt-0123456789abcdef"
CONTROLS_REF = "evidence/controls-2026-09-17.txt"


# --- fixtures: real protocol, real ledger, real G1 reports -----------------------------

def arm_report(goal_id, roots, *, status="accepted", authorized=AUTHORIZED,
               accepted=BASELINE_ACCEPTED, synthetic=True, ownership="dedicated",
               partial=False, unknown_root=False, conflict=False, mode="whole-session-roots",
               since=None, until=None, evidence_label="suite", inputs_dir=None,
               generated=None, salt=SALT, allocation=True, allocated_elsewhere=1,
               rowless=False):
    """One arm's report, built by the REAL G1 reporter over a synthetic snapshot.

    With `inputs_dir` the manifest and the snapshot are written to real files and the reporter
    reads them, which is the only way a G1 report carries a raw-byte snapshot digest.
    A terminal instant is LEDGER material and is attached by `stage`, never by the reporter:
    G1 has no field for it, which is the whole reason the ledger must attest it.
    """
    roots = list(roots)
    resolved = [] if unknown_root else roots
    unknown = roots if unknown_root else []
    snap = cost()
    snap["scope"]["roots"] = roots
    snap["scope"]["resolvedRoots"] = resolved
    snap["scope"]["unknownRoots"] = unknown
    snap["scope"]["since"] = since
    snap["scope"]["until"] = until
    # `allocation=True` is the repaired engine's complete verdict; False is an incomplete one and
    # None is a LEGACY snapshot from before the field existed.
    if allocation is None:
        del snap["scope"]["allocationCoverage"]
    else:
        snap["scope"]["allocationCoverage"] = coverage(
            allocation, 0 if allocation else allocated_elsewhere)
    over = {}
    if partial:
        over = {"partial": True, "partialReasons": ["rate-missing"]}
    if conflict:
        over = {**over, "cwConflict": 3}
    # A scope the engine resolved and returned NO priced row for: an unknown, not a measured zero.
    snap["rows"] = [] if rowless else [row(name, **over) for name in resolved]

    outcome = {"status": status, "implementer": "worker", "verifier": "reviewer",
               "accepted_utc": accepted if status == "accepted" else None,
               "evidence": [{"label": evidence_label, "ref": "evidence/green.txt"}],
               "blocker": "a declared blocker" if status == "blocked" else None}
    if status != "accepted":
        outcome["verifier"] = None
    manifest = goal(goal_id=goal_id, authorized_utc=authorized, synthetic=synthetic,
                    scope={"roots": roots, "mode": mode, "since": since, "until": until,
                           "ownership": ownership},
                    outcome=outcome, scope_fingerprint_salt=salt)
    if salt is None:
        del manifest["scope_fingerprint_salt"]
    when = generated or REPORT_NOW
    if inputs_dir is None:
        return R.build_report(manifest, snap, now_utc=when)
    inputs_dir = Path(inputs_dir)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    goal_path, cost_path = inputs_dir / f"{goal_id}-goal.json", inputs_dir / f"{goal_id}-cost.json"
    goal_path.write_text(json.dumps(manifest), encoding="utf-8")
    cost_path.write_text(json.dumps(snap, default=str), encoding="utf-8")
    return R.load_report(goal_path, cost_path, now_utc=when)


def pair(index, work_class="bounded-code"):
    return {"id": f"p{index}", "work_class": work_class,
            "matched": {"change_kind": "parser", "scope_band": "small"},
            "baseline": {"goal_id": f"g-base-{index}", "roots": [f"root-base-{index}"]},
            "delegated": {"goal_id": f"g-del-{index}", "roots": [f"root-del-{index}"]}}


def work_class(ident="bounded-code", ref="oracles/bounded-code.md"):
    return {"id": ident, "acceptance_oracle_ref": ref,
            "matching_fields": ["change_kind", "scope_band"]}


def protocol(pairs=3, **over):
    base = {
        "schema_version": 1,
        "kind": "goal-comparison-protocol",
        "cohort_id": "synthetic-cohort",
        "synthetic": True,
        "created_utc": "2026-09-16T00:00:00Z",
        "timezone": "UTC",
        "scope_fingerprint_salt": SALT,
        "window": {"start_utc": WINDOW_START, "horizon_hours": 336},
        "policy": {
            "changed_dimension": "delegation_shape",
            "shared_controls": {"model_policy": "fixed", "review_policy": "independent"},
            "controls_evidence_ref": CONTROLS_REF,
            "baseline_value": "focused-solver",
            "delegated_value": "worker-plus-verifier",
            "monthly_llm_cap_usd": "1000.00",
            "pilot_incremental_cap_usd": "50.00",
            "critical_reserve_usd": "100.00",
            "required_checkpoints_per_day": 2,
            "required_minutes_per_day": 20,
        },
        "budget_admission": {"status": "not-attested", "checked_utc": None, "evidence_ref": None},
        "work_classes": [work_class()],
        "pairs": [pair(i) for i in range(1, pairs + 1)],
    }
    base.update(over)
    return base


DEFAULT_INTERVENTIONS = [
    {"id": "check-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": 12},
    {"id": "check-2", "at_utc": "2026-09-19T12:00:00Z", "required": True, "minutes": 8},
    {"id": "peek-1", "at_utc": "2026-09-19T18:00:00Z", "required": False, "minutes": 30},
]


def arm_specs(proto, over=None):
    over = over or {}
    specs = {}
    for entry in proto["pairs"]:
        for side, accepted in (("baseline", BASELINE_ACCEPTED), ("delegated", DELEGATED_ACCEPTED)):
            arm = entry[side]
            spec = {"accepted": accepted}
            spec.update(over.get(arm["goal_id"], {}))
            specs[arm["goal_id"]] = (arm["roots"], spec)
    return specs


def reports_for(proto, over=None, inputs_dir=None, generated=None):
    return {gid: arm_report(gid, roots, inputs_dir=inputs_dir, generated=generated,
                            **{k: v for k, v in spec.items()
                               if k not in ("terminal_at", "terminal_ref")})
            for gid, (roots, spec) in arm_specs(proto, over).items()}


TERMINAL_STATUSES = ("failed", "blocked", "abandoned")


def _terminal_for(goal_id, spec, attest, override):
    """What the LEDGER attests about an arm's terminal instant. Absent by default for a status
    G1 cannot date; `attest` fills in a valid attestation so a test can say which it means."""
    if override is not None and goal_id in override:
        return override[goal_id] or {"terminal_at_utc": None, "terminal_evidence_ref": None}
    if attest and spec.get("status") in TERMINAL_STATUSES:
        return {"terminal_at_utc": spec.get("terminal_at", TERMINAL_AT),
                "terminal_evidence_ref": spec.get("terminal_ref", TERMINAL_REF)}
    return {"terminal_at_utc": spec.get("terminal_at"),
            "terminal_evidence_ref": spec.get("terminal_ref")}


def stage(tmp_path, proto=None, *, arms=None, reports=None, interventions=None, as_of=AS_OF,
          escaped=None, defect_ref="evidence/defects.txt", protocol_sha=None,
          ledger_over=None, report_paths=None, protocol_bytes=None, omit=(), raw=False,
          generated=None, attest_terminal=True, terminal=None, report_sha=None,
          drop_report_sha=False, mutate_report=None):
    """Write a real protocol, real G1 report files and a real ledger; return their paths."""
    proto = protocol() if proto is None else proto
    specs = arm_specs(proto, arms)
    if reports is None:
        reports = reports_for(proto, arms, tmp_path / "_inputs" if raw else None,
                              generated or as_of)
    built = reports
    (tmp_path / "reports").mkdir(exist_ok=True)
    proto_path = tmp_path / "protocol.json"
    proto_path.write_bytes(protocol_bytes if protocol_bytes is not None
                           else (json.dumps(proto, indent=2) + "\n").encode("utf-8"))

    observations = []
    overridden = report_paths or {}
    for goal_id, built_report in built.items():
        if goal_id in omit:
            continue
        rel = overridden.get(goal_id, f"reports/{goal_id}.json")
        # An overridden path is the thing under test - it is validated, never opened, so the
        # fixture must not try to write through it.
        digest = None
        if built_report is not None and goal_id not in overridden:
            destination = tmp_path / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            R.write_report(built_report, destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        entry = {"goal_id": goal_id, "report_path": rel,
                 "escaped_defects": (escaped or {}).get(goal_id, 0),
                 "defect_evidence_ref": defect_ref,
                 **_terminal_for(goal_id, specs.get(goal_id, (None, {}))[1],
                                 attest_terminal, terminal)}
        if not drop_report_sha:
            entry["report_sha256"] = (report_sha or {}).get(goal_id, digest or "0" * 64)
        observations.append(entry)
    # The ledger is written from the bytes on disk; a mutation AFTER that is the freeze under test.
    for goal_id, rewrite in (mutate_report or {}).items():
        target = tmp_path / f"reports/{goal_id}.json"
        target.write_bytes(rewrite(target.read_bytes()))
    ledger = {
        "schema_version": 1,
        "kind": "goal-comparison-observations",
        "protocol_sha256": protocol_sha or hashlib.sha256(proto_path.read_bytes()).hexdigest(),
        "as_of_utc": as_of,
        "observations": observations,
        "interventions": list(DEFAULT_INTERVENTIONS if interventions is None else interventions),
    }
    ledger.update(ledger_over or {})
    obs_path = tmp_path / "observations.json"
    obs_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return proto_path, obs_path


def build(tmp_path, proto=None, *, now=NOW, **kwargs):
    return C.load_comparison(*stage(tmp_path, proto, **kwargs), now_utc=now)


def only_class(comparison):
    assert len(comparison["classes"]) == 1
    return comparison["classes"][0]


def gate(entry, code):
    """Gates are looked up by NAME. A positional slice silently relabels them when one is added."""
    for item in entry["gates"]:
        if item["code"] == code:
            return item
    raise AssertionError(f"no gate {code!r} in {[g['code'] for g in entry['gates']]}")


def codes(entries):
    return {entry["code"] for entry in entries}


# --- the G1 additive scope fingerprint (the extension G2 is built on) -------------------
# It lives here rather than beside the G1 suite because it exists only to make this
# comparison's root partition checkable without ever handling a raw root id again.

def fingerprints(rep):
    return rep["source"]["scope"]["root_fingerprints"]


def test_each_requested_root_gets_its_own_keyed_domain_separated_handle():
    rep = arm_report("g-1", ["root-a"])
    expected = hmac.new(SALT.encode("utf-8"), b"goal-root-v2\x00root-a", hashlib.sha256).hexdigest()
    assert fingerprints(rep) == [expected]
    assert rep["source"]["scope"]["root_fingerprint_algorithm"] == \
        "hmac-sha256(scope_fingerprint_salt, goal-root-v2\\0<root-id>)"


def test_an_unkeyed_digest_of_the_same_root_is_not_the_handle():
    # The plain sha256 is a confirmation oracle against any guessable root id. The keyed handle
    # is not reproducible without the salt, which never leaves the manifest.
    rep = arm_report("g-1", ["root-a"])
    assert hashlib.sha256(b"goal-root-v1\x00root-a").hexdigest() not in fingerprints(rep)
    assert hashlib.sha256(b"goal-root-v2\x00root-a").hexdigest() not in fingerprints(rep)


def test_a_different_salt_produces_different_handles_for_the_same_root():
    one = arm_report("g-1", ["root-a"])
    two = arm_report("g-1", ["root-a"], salt="a-different-private-salt-0123456789abcdef")
    assert fingerprints(one) != fingerprints(two)
    assert one["source"]["scope"]["scope_fingerprint"] != two["source"]["scope"]["scope_fingerprint"]


def test_the_salt_never_leaves_the_manifest():
    assert SALT not in json.dumps(arm_report("g-1", ["root-a"]))


@pytest.mark.parametrize("bad", ["", "short", "x" * 31, "a" * 64, 12345, None])
def test_a_missing_short_or_low_entropy_salt_is_refused(bad):
    with pytest.raises(R.ReportError, match="scope_fingerprint_salt"):
        arm_report("g-1", ["root-a"], salt=bad)


def test_the_same_roots_in_a_different_order_fingerprint_identically():
    one = arm_report("g-1", ["root-a", "root-b"])
    two = arm_report("g-1", ["root-b", "root-a"])
    assert fingerprints(one) == fingerprints(two)
    assert one["source"]["scope"]["scope_fingerprint"] == two["source"]["scope"]["scope_fingerprint"]


def test_one_changed_root_changes_the_fingerprints():
    one = arm_report("g-1", ["root-a", "root-b"])
    two = arm_report("g-1", ["root-a", "root-c"])
    assert fingerprints(one) != fingerprints(two)
    assert one["source"]["scope"]["scope_fingerprint"] != two["source"]["scope"]["scope_fingerprint"]


def test_a_raw_root_id_never_appears_beside_its_fingerprint():
    rep = arm_report("g-1", ["root-a"])
    assert "root-a" not in json.dumps(rep)
    assert all(len(digest) == 64 for digest in fingerprints(rep))


def test_the_scope_fingerprint_binds_the_selection_mode_and_bounds():
    whole = arm_report("g-1", ["root-a"])
    bounded = arm_report("g-1", ["root-a"], mode="utc-days", since="2026-09-01", until="2026-09-10")
    assert fingerprints(whole) == fingerprints(bounded)
    assert whole["source"]["scope"]["scope_fingerprint"] != bounded["source"]["scope"]["scope_fingerprint"]


def test_the_scope_fingerprint_binds_the_engine_time_precision():
    rep = arm_report("g-1", ["root-a"])
    scope = rep["source"]["scope"]
    payload = {"root_fingerprints": scope["root_fingerprints"], "mode": "whole-session-roots",
               "since": None, "until": None, "engine_time_precision": "utc-day"}

    def keyed(body):
        return hmac.new(SALT.encode("utf-8"),
                        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                        hashlib.sha256).hexdigest()

    assert scope["scope_fingerprint"] == keyed(payload)
    assert scope["scope_fingerprint"] != keyed(dict(payload, engine_time_precision="utc-second"))


def test_the_g1_report_schema_stays_at_version_one():
    assert arm_report("g-1", ["root-a"])["schema_version"] == 1


# --- protocol schema limits -------------------------------------------------------------

def test_a_well_formed_protocol_and_ledger_produce_one_report(tmp_path):
    comparison = build(tmp_path)
    assert comparison["kind"] == "goal-comparison-report"
    assert comparison["schema_version"] == 1
    assert comparison["generated_utc"] == NOW


def test_an_unsupported_protocol_schema_version_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="schema_version"):
        build(tmp_path, protocol(schema_version=2))


def test_a_protocol_of_the_wrong_kind_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="kind"):
        build(tmp_path, protocol(kind="goal-report"))


def test_a_seventh_pair_is_refused(tmp_path):
    proto = protocol(pairs=6)
    proto["pairs"].append(pair(7))
    with pytest.raises(C.ComparisonError, match="six"):
        build(tmp_path, proto)


def test_six_pairs_are_accepted(tmp_path):
    assert build(tmp_path, protocol(pairs=6))["protocol"]["pair_count"] == 6


def test_twelve_goal_arms_are_the_ceiling(tmp_path):
    assert build(tmp_path, protocol(pairs=6))["protocol"]["arm_count"] == 12


def test_a_third_work_class_is_refused(tmp_path):
    proto = protocol()
    proto["work_classes"] = [work_class("a"), work_class("b"), work_class("c")]
    with pytest.raises(C.ComparisonError, match="two"):
        build(tmp_path, proto)


def test_two_work_classes_are_accepted_and_reported_apart(tmp_path):
    proto = protocol()
    proto["work_classes"] = [work_class("bounded-code"), work_class("bounded-docs")]
    proto["pairs"] = [pair(1), pair(2), pair(3, "bounded-docs")]
    comparison = build(tmp_path, proto)
    assert [entry["id"] for entry in comparison["classes"]] == ["bounded-code", "bounded-docs"]
    assert [entry["pair_count"] for entry in comparison["classes"]] == [2, 1]


def test_a_pair_naming_an_undeclared_work_class_is_refused(tmp_path):
    proto = protocol()
    proto["pairs"][0]["work_class"] = "not-declared"
    with pytest.raises(C.ComparisonError, match="work class"):
        build(tmp_path, proto)


def test_an_empty_pair_list_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="pair"):
        build(tmp_path, protocol(pairs=0))


def test_a_duplicate_goal_id_across_arms_is_refused(tmp_path):
    proto = protocol()
    proto["pairs"][1]["baseline"]["goal_id"] = proto["pairs"][0]["baseline"]["goal_id"]
    with pytest.raises(C.ComparisonError, match="goal id"):
        build(tmp_path, proto)


def test_the_same_goal_id_on_both_arms_of_one_pair_is_refused(tmp_path):
    proto = protocol()
    proto["pairs"][0]["delegated"]["goal_id"] = proto["pairs"][0]["baseline"]["goal_id"]
    with pytest.raises(C.ComparisonError, match="goal id"):
        build(tmp_path, proto)


def test_a_duplicate_pair_id_is_refused(tmp_path):
    proto = protocol()
    proto["pairs"][1]["id"] = proto["pairs"][0]["id"]
    with pytest.raises(C.ComparisonError, match="pair id"):
        build(tmp_path, proto)


def test_a_root_shared_by_two_arms_is_refused_before_anything_is_measured(tmp_path):
    proto = protocol()
    proto["pairs"][0]["delegated"]["roots"] = list(proto["pairs"][0]["baseline"]["roots"])
    with pytest.raises(C.ComparisonError, match="root"):
        build(tmp_path, proto)


def test_a_root_shared_across_two_pairs_is_refused(tmp_path):
    proto = protocol()
    proto["pairs"][2]["baseline"]["roots"] = list(proto["pairs"][0]["baseline"]["roots"])
    with pytest.raises(C.ComparisonError, match="root"):
        build(tmp_path, proto)


def test_a_horizon_beyond_336_hours_is_refused(tmp_path):
    proto = protocol()
    proto["window"]["horizon_hours"] = 337
    with pytest.raises(C.ComparisonError, match="336"):
        build(tmp_path, proto)


def test_a_zero_or_negative_horizon_is_refused(tmp_path):
    proto = protocol()
    proto["window"]["horizon_hours"] = 0
    with pytest.raises(C.ComparisonError, match="horizon"):
        build(tmp_path, proto)


def test_a_fractional_horizon_is_refused(tmp_path):
    proto = protocol()
    proto["window"]["horizon_hours"] = 24.5
    with pytest.raises(C.ComparisonError, match="horizon"):
        build(tmp_path, proto)


def test_a_protocol_created_after_its_own_window_opens_is_refused(tmp_path):
    proto = protocol()
    proto["created_utc"] = "2026-09-18T00:00:00Z"
    with pytest.raises(C.ComparisonError, match="created_utc"):
        build(tmp_path, proto)


def test_a_local_offset_timestamp_is_refused(tmp_path):
    proto = protocol()
    proto["window"]["start_utc"] = "2026-09-17T00:00:00+02:00"
    with pytest.raises(C.ComparisonError, match="UTC"):
        build(tmp_path, proto)


def test_an_unknown_protocol_timezone_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="timezone"):
        build(tmp_path, protocol(timezone="Mars/Olympus"))


def test_the_changed_dimension_may_not_also_sit_in_the_shared_controls(tmp_path):
    proto = protocol()
    proto["policy"]["shared_controls"]["delegation_shape"] = "fixed"
    with pytest.raises(C.ComparisonError, match="changed_dimension"):
        build(tmp_path, proto)


def test_two_arms_with_the_same_value_change_nothing_and_are_refused(tmp_path):
    proto = protocol()
    proto["policy"]["delegated_value"] = proto["policy"]["baseline_value"]
    with pytest.raises(C.ComparisonError, match="differ"):
        build(tmp_path, proto)


def test_a_structured_arm_value_is_refused_because_it_can_hide_a_second_change(tmp_path):
    proto = protocol()
    proto["policy"]["delegated_value"] = {"shape": "worker-plus-verifier", "model": "other"}
    with pytest.raises(C.ComparisonError, match="scalar"):
        build(tmp_path, proto)


def test_a_structured_shared_control_is_refused(tmp_path):
    proto = protocol()
    proto["policy"]["shared_controls"]["review_policy"] = {"kind": "independent"}
    with pytest.raises(C.ComparisonError, match="scalar"):
        build(tmp_path, proto)


def test_matched_fields_must_be_exactly_the_work_class_matching_fields(tmp_path):
    proto = protocol()
    del proto["pairs"][0]["matched"]["scope_band"]
    with pytest.raises(C.ComparisonError, match="matching"):
        build(tmp_path, proto)


def test_an_extra_matched_field_is_refused_rather_than_inferred(tmp_path):
    proto = protocol()
    proto["pairs"][0]["matched"]["route"] = "paid"
    with pytest.raises(C.ComparisonError, match="matching"):
        build(tmp_path, proto)


def test_the_matched_values_are_reported_per_pair(tmp_path):
    entry = only_class(build(tmp_path))
    assert entry["matching_fields"] == ["change_kind", "scope_band"]
    assert entry["pairs"][0]["matched"] == {"change_kind": "parser", "scope_band": "small"}


# --- the freeze: protocol and ledger are hashed over their exact bytes ------------------

def test_the_ledger_must_name_the_exact_protocol_bytes(tmp_path):
    with pytest.raises(C.ComparisonError, match="protocol_sha256"):
        build(tmp_path, protocol_sha="0" * 64)


def test_a_reformatted_protocol_no_longer_matches_its_freeze(tmp_path):
    proto = protocol()
    frozen = hashlib.sha256((json.dumps(proto, indent=2) + "\n").encode("utf-8")).hexdigest()
    with pytest.raises(C.ComparisonError, match="protocol_sha256"):
        build(tmp_path, proto, protocol_sha=frozen,
              protocol_bytes=(json.dumps(proto, indent=4) + "\n").encode("utf-8"))


def test_the_protocol_digest_is_the_file_digest_a_reviewer_can_reproduce(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    comparison = C.load_comparison(proto_path, obs_path, now_utc=NOW)
    assert comparison["sources"]["protocol_sha256"] == hashlib.sha256(proto_path.read_bytes()).hexdigest()
    assert comparison["sources"]["observations_sha256"] == hashlib.sha256(obs_path.read_bytes()).hexdigest()


def test_the_input_digests_are_labelled_as_private_input_fingerprints(tmp_path):
    note = build(tmp_path)["sources"]["note"]
    assert "private input" in note.lower()


def test_a_malformed_protocol_digest_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="protocol_sha256"):
        build(tmp_path, protocol_sha="not-a-digest")


# --- ledger path safety -----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.json", "reports/../../outside.json",
                                 "reports\\arm.json", "reports/arm\n.json", "~/arm.json"])
def test_an_unsafe_report_path_is_refused(tmp_path, bad):
    with pytest.raises(C.ComparisonError, match="report_path"):
        build(tmp_path, report_paths={"g-base-1": bad})


def test_a_report_path_that_is_a_directory_is_refused(tmp_path):
    (tmp_path / "reports" / "adir").mkdir(parents=True)
    with pytest.raises(C.ComparisonError, match="report"):
        build(tmp_path, report_paths={"g-base-1": "reports/adir"})


def test_a_missing_report_file_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"] = None
    with pytest.raises(C.ComparisonError, match="report"):
        build(tmp_path, proto, reports=built)


# --- the observations must cover the protocol exactly -----------------------------------

def test_a_missing_observation_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="observation"):
        build(tmp_path, omit=("g-del-2",))


def test_a_duplicate_observation_for_one_goal_is_refused(tmp_path):
    proto = protocol()
    proto_path, obs_path = stage(tmp_path, proto)
    ledger = json.loads(obs_path.read_text())
    ledger["observations"].append(dict(ledger["observations"][0]))
    obs_path.write_text(json.dumps(ledger))
    with pytest.raises(C.ComparisonError, match="observation"):
        C.load_comparison(proto_path, obs_path, now_utc=NOW)


def test_an_observation_for_a_goal_the_protocol_never_declared_is_refused(tmp_path):
    proto = protocol()
    proto_path, obs_path = stage(tmp_path, proto)
    ledger = json.loads(obs_path.read_text())
    stranger = dict(ledger["observations"][0], goal_id="g-stranger")
    ledger["observations"].append(stranger)
    obs_path.write_text(json.dumps(ledger))
    with pytest.raises(C.ComparisonError, match="protocol"):
        C.load_comparison(proto_path, obs_path, now_utc=NOW)


def test_a_report_whose_goal_id_is_not_the_one_observed_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"] = arm_report("g-somebody-else", ["root-base-1"])
    with pytest.raises(C.ComparisonError, match="goal"):
        build(tmp_path, proto, reports=built)


def test_a_day_bounded_report_is_refused_because_the_arms_must_be_whole_roots(tmp_path):
    proto = protocol()
    with pytest.raises(C.ComparisonError, match="whole-session-roots"):
        build(tmp_path, proto, arms={"g-base-1": {"mode": "utc-days", "since": "2026-09-17"}})


def test_a_shared_scope_report_is_refused_because_the_arm_must_own_its_roots(tmp_path):
    with pytest.raises(C.ComparisonError, match="dedicated"):
        build(tmp_path, arms={"g-base-1": {"ownership": "shared"}})


def test_a_report_built_over_different_roots_fails_the_fingerprint_check(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"] = arm_report("g-base-1", ["root-somewhere-else"])
    with pytest.raises(C.ComparisonError, match="fingerprint"):
        build(tmp_path, proto, reports=built)


def test_a_report_with_a_hand_edited_scope_fingerprint_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["source"]["scope"]["scope_fingerprint"] = "f" * 64
    with pytest.raises(C.ComparisonError, match="fingerprint"):
        build(tmp_path, proto, reports=built)


def test_a_report_without_the_scope_fingerprint_extension_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    del built["g-base-1"]["source"]["scope"]
    with pytest.raises(C.ComparisonError, match="scope"):
        build(tmp_path, proto, reports=built)


def test_the_arms_root_fingerprints_are_reported_as_an_exact_disjoint_partition(tmp_path):
    partition = build(tmp_path)["source_roots"]
    assert partition["arm_count"] == 6
    assert partition["fingerprint_count"] == 6
    assert partition["overlapping_arm_pairs"] == 0
    assert partition["partition_exact"] is True


def test_two_arms_reporting_the_same_root_fingerprint_are_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    # Two DIFFERENT roots the protocol declared, but one report was built over the other's.
    built["g-del-1"] = arm_report("g-del-1", ["root-base-1"])
    with pytest.raises(C.ComparisonError, match="fingerprint"):
        build(tmp_path, proto, reports=built)


# --- source report provenance -----------------------------------------------------------

def test_a_report_built_from_a_file_carries_raw_byte_provenance(tmp_path):
    comparison = build(tmp_path, raw=True)
    assert comparison["sources"]["report_provenance"]["raw_bytes"] == 6
    assert comparison["sources"]["report_provenance"]["in_memory_object"] == 0
    assert "source-provenance-unfingerprinted" not in codes(comparison["limitations"])


def test_a_report_built_from_a_dict_is_a_provenance_limitation_never_a_mismatch(tmp_path):
    # G1 writes `snapshot_sha256: null` when it was handed an object rather than a file. That is
    # a missing receipt, not a wrong one, and it must never be reported as a hash mismatch.
    comparison = build(tmp_path)
    assert comparison["sources"]["report_provenance"]["in_memory_object"] == 6
    assert "source-provenance-unfingerprinted" in codes(comparison["limitations"])
    assert not any("mismatch" in item["text"].lower() for item in comparison["limitations"]
                   if item["code"] == "source-provenance-unfingerprinted")


def test_a_snapshot_digest_contradicting_its_own_note_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["source"]["snapshot_sha256"] = "a" * 64
    with pytest.raises(C.ComparisonError, match="provenance"):
        build(tmp_path, proto, reports=built)


def test_a_malformed_snapshot_digest_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["source"]["snapshot_sha256"] = "nope"
    built["g-base-1"]["source"]["snapshot_sha256_note"] = R.RAW_DIGEST_NOTE
    with pytest.raises(C.ComparisonError, match="sha256"):
        build(tmp_path, proto, reports=built)


def test_a_report_from_an_unsupported_time_precision_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["source"]["time_precision"] = "utc-second"
    with pytest.raises(C.ComparisonError, match="precision"):
        build(tmp_path, proto, reports=built)


def test_a_money_figure_arriving_as_a_number_is_refused(tmp_path):
    # G1 writes the subtotal as a decimal STRING. A JSON number has already lost that
    # representation, so it is refused rather than re-read as a float.
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["allocated_api_equivalent"]["known_subtotal_usd"] = 1.25
    with pytest.raises(C.ComparisonError, match="decimal string"):
        build(tmp_path, proto, reports=built)


# --- horizon classification ---------------------------------------------------------------

def classification(comparison, goal_id):
    for entry in comparison["classes"]:
        for pair_entry in entry["pairs"]:
            for side in ("baseline", "delegated"):
                if pair_entry[side]["goal_id"] == goal_id:
                    return pair_entry[side]
    raise AssertionError(goal_id)


def reason(comparison, goal_id):
    return classification(comparison, goal_id)["classification_reason"]


def test_an_arm_accepted_inside_the_horizon_is_accepted(tmp_path):
    assert classification(build(tmp_path), "g-base-1")["classification"] == "accepted"


def test_an_arm_accepted_after_the_horizon_is_unresolved_never_censored_evidence(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"accepted": "2026-10-01T00:00:01Z"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "accepted-after-horizon"


def test_an_arm_accepted_before_the_window_opens_is_unresolved(tmp_path):
    # A latency longer than the horizon it is measured against is the tell that the window is not
    # being enforced at both ends. It is bounded from below now.
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": "2026-09-01T00:00:00Z",
                                                    "accepted": "2026-09-16T23:59:59Z"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "accepted-before-window"


def test_an_arm_accepted_after_the_ledger_cutoff_is_unresolved(tmp_path):
    # The ledger observed to 2026-09-24; an acceptance it could not have seen establishes nothing.
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"accepted": "2026-09-26T00:00:00Z"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "accepted-after-cutoff"


def test_an_arm_accepted_exactly_at_the_horizon_end_counts_as_accepted(tmp_path):
    arm = classification(build(tmp_path, arms={"g-base-1": {"accepted": HORIZON_END}}), "g-base-1")
    assert arm["classification"] == "accepted"
    assert arm["classification_reason"] is None


def test_an_arm_accepted_exactly_at_the_window_start_counts_as_accepted(tmp_path):
    arm = classification(build(tmp_path, arms={"g-base-1": {"accepted": WINDOW_START}}), "g-base-1")
    assert arm["classification"] == "accepted"
    assert arm["latency_seconds"] == 0


def test_an_accepted_arm_with_an_unknown_acceptance_time_is_unresolved(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"accepted": None}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert classification(comparison, "g-base-1")["latency_seconds"] is None


def test_an_accepted_arm_with_an_unknown_authorization_is_unresolved(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": None}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "acceptance-time-unknown"


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_an_attested_terminal_outcome_inside_the_window_is_recorded_as_that_outcome(tmp_path, status):
    arm = classification(build(tmp_path, arms={"g-base-1": {"status": status}}), "g-base-1")
    assert arm["classification"] == status
    assert arm["terminal_at_attested"] is True


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_a_terminal_status_with_no_attestation_is_unresolved_after_the_horizon(tmp_path, status):
    # THE critical rule. G1 carries no terminal instant, so without an external attestation the
    # arm's history is unestablished - even once the window has closed.
    comparison = build(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": status}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "terminal-attestation-missing"


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_a_terminal_status_inside_a_still_open_window_is_unresolved(tmp_path, status):
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF, attest_terminal=False,
                       arms={"g-base-1": {"status": status}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"


def test_a_terminal_instant_before_the_window_opens_does_not_count(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "failed"}},
                       terminal={"g-base-1": {"terminal_at_utc": "2026-09-16T00:00:00Z",
                                              "terminal_evidence_ref": TERMINAL_REF}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "terminal-instant-outside-window"


def test_a_terminal_instant_after_the_horizon_does_not_count(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "failed"}},
                       terminal={"g-base-1": {"terminal_at_utc": "2026-10-01T00:00:01Z",
                                              "terminal_evidence_ref": TERMINAL_REF}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "terminal-instant-outside-window"


def test_a_terminal_instant_after_the_ledger_cutoff_does_not_count(tmp_path):
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"status": "failed"}},
                       terminal={"g-base-1": {"terminal_at_utc": "2026-09-26T00:00:00Z",
                                              "terminal_evidence_ref": TERMINAL_REF}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "terminal-instant-after-cutoff"


def test_a_terminal_instant_without_an_evidence_reference_does_not_count(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "failed"}},
                       terminal={"g-base-1": {"terminal_at_utc": TERMINAL_AT,
                                              "terminal_evidence_ref": None}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "terminal-attestation-missing"


def test_an_unsafe_terminal_evidence_reference_does_not_count(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "failed"}},
                       terminal={"g-base-1": {"terminal_at_utc": TERMINAL_AT,
                                              "terminal_evidence_ref": "/home/someone/private.txt"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "terminal-evidence-withheld"


def test_a_terminal_attestation_for_an_arm_g1_never_called_terminal_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="terminal"):
        build(tmp_path, terminal={"g-base-1": {"terminal_at_utc": TERMINAL_AT,
                                               "terminal_evidence_ref": TERMINAL_REF}})


def test_reopened_is_unresolved_because_it_may_hide_a_prior_acceptance(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "reopened"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "reopened-may-hide-acceptance"


def test_in_progress_at_a_cutoff_after_the_horizon_is_censored_at_the_cutoff(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "in_progress"}})
    arm = classification(comparison, "g-base-1")
    assert arm["classification"] == "censored"
    assert arm["classification_reason"] == "open-at-cutoff-after-horizon"
    assert "censored-attested-at-cutoff" in codes(comparison["limitations"])


def test_in_progress_at_a_cutoff_before_the_horizon_is_pending(tmp_path):
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"status": "in_progress"}})
    assert classification(comparison, "g-base-1")["classification"] == "pending"


def test_the_generation_clock_never_decides_whether_an_arm_was_open_at_the_horizon(tmp_path):
    # The stale-ledger inversion: a document generated long after the horizon, over a ledger that
    # only ever observed to a point INSIDE the window, must not call that arm censored.
    comparison = build(tmp_path, now="2026-10-20T00:00:00Z", as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"status": "in_progress"}})
    assert classification(comparison, "g-base-1")["classification"] == "pending"


def test_the_old_terminal_before_horizon_tri_state_is_gone(tmp_path):
    arm = classification(build(tmp_path, arms={"g-base-1": {"status": "failed"}}), "g-base-1")
    assert "terminal_before_horizon" not in arm
    assert "accepted_after_horizon" not in arm


def test_a_pair_with_a_pending_arm_is_not_fully_observed(tmp_path):
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"status": "in_progress"}})
    entry = only_class(comparison)
    assert entry["fully_observed_pairs"] == 2
    assert entry["pairs"][0]["fully_observed"] is False


def test_a_pair_with_an_unresolved_arm_is_not_fully_observed(tmp_path):
    comparison = build(tmp_path, attest_terminal=False, arms={"g-base-1": {"status": "blocked"}})
    entry = only_class(comparison)
    assert entry["fully_observed_pairs"] == 2
    assert entry["pairs"][0]["fully_observed"] is False
    assert "arms-unresolved" in codes(entry["rationale"])


def test_an_unresolved_arm_makes_every_directional_gate_unknown(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    arms["g-base-1"] |= {"status": "blocked"}
    comparison = build(tmp_path, proto, arms=arms, attest_terminal=False)
    entry = only_class(comparison)
    for code in ("delegated-acceptance-not-lower", "delegated-defect-rate-not-higher",
                 "delegated-latency-lower"):
        assert gate(entry, code)["met"] is None, code
    assert comparison["status"] == "inconclusive"


def test_a_censored_or_attested_terminal_arm_still_leaves_its_pair_fully_observed(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "failed"},
                                       "g-del-2": {"status": "in_progress"}})
    entry = only_class(comparison)
    assert entry["fully_observed_pairs"] == 3
    assert entry["pairs"][0]["fully_observed"] is True


def test_every_classification_is_counted_per_arm_side(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"status": "failed"},
                                       "g-base-2": {"status": "in_progress"},
                                       "g-base-3": {"status": "reopened"}})
    counts = only_class(comparison)["arms"]["baseline"]["classification"]
    assert counts == {"accepted": 0, "failed": 1, "blocked": 0, "abandoned": 0,
                      "censored": 1, "pending": 0, "unresolved": 1}


def test_the_whole_protocol_before_its_window_opens_has_not_started(tmp_path):
    # A ledger that has only observed to the day before the window cannot cite a check-in from
    # inside it, so this fixture carries none at all.
    comparison = build(tmp_path, now="2026-09-16T12:00:00Z", as_of="2026-09-16T00:00:00Z",
                       interventions=[])
    assert comparison["status"] == "not-started"
    assert "window-not-open" in codes(comparison["rationale"])


def test_a_pending_arm_inside_the_window_reads_as_observing(tmp_path):
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"status": "in_progress"}})
    assert comparison["status"] == "observing"
    assert "arms-pending" in codes(only_class(comparison)["rationale"])


# --- latency: accepted only, never without its denominators ------------------------------

def test_the_accepted_only_median_carries_its_denominator_rates_and_censored_count(tmp_path):
    latency = only_class(build(tmp_path))["arms"]["baseline"]["latency"]
    assert latency["median_seconds"] == 432000
    assert latency["accepted_denominator"] == 3
    assert latency["arm_count"] == 3
    assert latency["censored"] == 0 and latency["pending"] == 0
    assert latency["accepted_only"] is True


def test_the_median_qualification_names_every_outcome_count_not_just_the_flattering_ones(tmp_path):
    comparison = build(tmp_path, protocol(pairs=6), attest_terminal=False, arms={
        "g-base-1": {"status": "failed"}, "g-base-2": {"status": "blocked"},
        "g-base-3": {"status": "abandoned"}, "g-base-4": {"status": "in_progress"}})
    latency = only_class(comparison)["arms"]["baseline"]["latency"]
    for name in ("accepted", "failed", "blocked", "abandoned", "censored", "unresolved", "pending"):
        assert name in latency["qualification"], name
        assert name in latency["counts_summary"], name
    assert latency["unresolved"] == 3


def test_a_censored_arm_is_excluded_from_the_median_and_visible_beside_it(tmp_path):
    latency = only_class(build(tmp_path, arms={"g-base-1": {"status": "in_progress"}})
                         )["arms"]["baseline"]["latency"]
    assert latency["accepted_denominator"] == 2
    assert latency["censored"] == 1
    assert latency["median_seconds"] == 432000


def test_a_median_over_no_accepted_arm_is_unknown_and_never_zero(tmp_path):
    latency = only_class(build(tmp_path, arms={gid: {"status": "failed"} for gid in
                                               ("g-base-1", "g-base-2", "g-base-3")})
                         )["arms"]["baseline"]["latency"]
    assert latency["median_seconds"] is None
    assert latency["accepted_denominator"] == 0


def test_an_even_number_of_accepted_arms_averages_the_two_middles(tmp_path):
    comparison = build(tmp_path, protocol(pairs=4), arms={
        "g-base-1": {"accepted": "2026-09-18T00:00:00Z"},   # 86400
        "g-base-2": {"accepted": "2026-09-19T00:00:00Z"},   # 172800
        "g-base-3": {"accepted": "2026-09-20T00:00:00Z"},   # 259200
        "g-base-4": {"accepted": "2026-09-21T00:00:00Z"}})  # 345600
    assert only_class(comparison)["arms"]["baseline"]["latency"]["median_seconds"] == 216000


def test_the_acceptance_rate_is_reported_with_its_numerator_and_denominator(tmp_path):
    acceptance = only_class(build(tmp_path, arms={"g-base-1": {"status": "failed"}})
                            )["arms"]["baseline"]["acceptance"]
    assert acceptance["accepted"] == 2 and acceptance["arm_count"] == 3
    assert acceptance["rate_percent"] == "66.7"


# --- escaped defects are attested, never inferred ----------------------------------------

def test_a_null_defect_count_is_unknown_and_never_zero(tmp_path):
    comparison = build(tmp_path, escaped={"g-base-1": None})
    defects = only_class(comparison)["arms"]["baseline"]["defects"]
    assert defects["unknown"] == 1 and defects["known"] == 2
    assert "defect-attestation-unknown" in codes(comparison["limitations"])


def test_a_defect_count_without_an_evidence_reference_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="evidence"):
        build(tmp_path, defect_ref=None)


def test_a_negative_defect_count_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="escaped_defects"):
        build(tmp_path, escaped={"g-base-1": -1})


def test_a_fractional_defect_count_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="escaped_defects"):
        build(tmp_path, escaped={"g-base-1": 1.5})


def test_a_partial_defect_attestation_keeps_its_counts_and_publishes_no_rate(tmp_path):
    # Superseded r0 expectation: this used to report 1.00 over the two arms that attested, which
    # credits the third - the one that attested nothing - with the mean. D1 makes it unknown.
    defects = only_class(build(tmp_path, escaped={"g-base-1": 2, "g-base-2": None})
                         )["arms"]["baseline"]["defects"]
    assert defects["total_escaped"] == 2
    assert defects["attested_arms"] == 2
    assert defects["rate_per_arm"] is None
    assert defects["reason"] == "unattested-arms"


def test_a_defect_count_is_never_derived_from_the_arm_status(tmp_path):
    defects = only_class(build(tmp_path, arms={"g-base-1": {"status": "failed"}})
                         )["arms"]["baseline"]["defects"]
    assert defects["total_escaped"] == 0
    assert defects["known"] == 3


def test_the_defect_evidence_reference_value_never_leaves_the_ledger(tmp_path):
    comparison = build(tmp_path, defect_ref="evidence/private-defect-notes.txt")
    assert "private-defect-notes" not in json.dumps(comparison)


# --- interventions are pilot-wide -----------------------------------------------------------

def test_required_and_optional_interventions_are_counted_apart_per_local_day(tmp_path):
    days = build(tmp_path)["attention"]["days"]
    assert [day["date"] for day in days] == ["2026-09-18", "2026-09-19"]
    assert days[1]["required_count"] == 1 and days[1]["optional_count"] == 1
    assert days[1]["required_minutes_known"] == 8


def test_days_are_grouped_by_the_protocols_own_timezone(tmp_path):
    proto = protocol(timezone="America/Los_Angeles")
    days = build(tmp_path, proto, interventions=[
        {"id": "c-1", "at_utc": "2026-09-18T02:00:00Z", "required": True, "minutes": 5}
    ])["attention"]["days"]
    assert [day["date"] for day in days] == ["2026-09-17"]


def test_an_identical_duplicate_intervention_is_deduped(tmp_path):
    entry = {"id": "c-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": 5}
    attention = build(tmp_path, interventions=[entry, dict(entry)])["attention"]
    assert attention["intervention_count"] == 1
    assert attention["deduped_count"] == 1


def test_a_conflicting_duplicate_intervention_is_refused(tmp_path):
    entry = {"id": "c-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": 5}
    with pytest.raises(C.ComparisonError, match="c-1"):
        build(tmp_path, interventions=[entry, dict(entry, minutes=6)])


def test_a_day_with_no_record_is_not_recorded_rather_than_zero(tmp_path):
    attention = build(tmp_path)["attention"]
    assert attention["recorded_day_count"] == 2
    assert attention["unrecorded_days"] is None
    assert "not recorded" in attention["unrecorded_days_note"].lower()
    assert "intervention-days-not-recorded" in codes(build(tmp_path)["limitations"])


def test_a_missing_required_minute_count_is_unknown(tmp_path):
    comparison = build(tmp_path, interventions=[
        {"id": "c-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": None}])
    day = comparison["attention"]["days"][0]
    assert day["required_minutes_unknown_count"] == 1
    assert day["required_minutes_exceeds_cap"] is None
    assert "required-minutes-unknown" in codes(comparison["limitations"])


def test_a_day_over_the_checkpoint_cap_is_flagged(tmp_path):
    day = build(tmp_path, interventions=[
        {"id": f"c-{i}", "at_utc": f"2026-09-18T0{i}:00:00Z", "required": True, "minutes": 2}
        for i in range(1, 4)])["attention"]["days"][0]
    assert day["required_checkpoints_exceeds_cap"] is True


def test_a_day_over_the_minute_cap_is_flagged(tmp_path):
    day = build(tmp_path, interventions=[
        {"id": "c-1", "at_utc": "2026-09-18T01:00:00Z", "required": True, "minutes": 21}
    ])["attention"]["days"][0]
    assert day["required_minutes_exceeds_cap"] is True


def test_optional_interventions_never_count_against_the_required_caps(tmp_path):
    day = build(tmp_path, interventions=[
        {"id": f"o-{i}", "at_utc": f"2026-09-18T0{i}:00:00Z", "required": False, "minutes": 90}
        for i in range(1, 5)])["attention"]["days"][0]
    assert day["required_checkpoints_exceeds_cap"] is False
    assert day["required_minutes_exceeds_cap"] is False
    assert day["optional_count"] == 4


def test_an_empty_intervention_ledger_is_unknown_and_never_proof_that_the_caps_held(tmp_path):
    # No record is the LOUDEST unknown here: a pilot that logged nothing has not demonstrated it
    # stayed inside a daily cap, and reading its silence as compliance is the whole failure mode.
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto), interventions=[])
    assert comparison["attention"]["recorded_day_count"] == 0
    entry = only_class(comparison)
    gate = next(item for item in entry["gates"] if item["code"] == "attention-within-caps")
    assert gate["met"] is None
    assert "attention-not-recorded" in codes(entry["rationale"])
    assert comparison["status"] == "inconclusive"


def test_the_pilot_ledger_is_never_merged_into_a_goals_own_intervention_counts(tmp_path):
    attention = build(tmp_path)["attention"]
    assert attention["scope"] == "pilot-wide"
    assert "per-goal" in attention["note"] or "each goal" in attention["note"]


# --- budget admission ----------------------------------------------------------------------

def test_the_default_protocol_is_not_attested_and_blocks_a_directional_candidate(tmp_path):
    comparison = build(tmp_path)
    assert comparison["budget_admission"]["attested"] is False
    assert "budget-admission-not-attested" in codes(only_class(comparison)["rationale"])


def test_an_unknown_admission_status_is_refused(tmp_path):
    proto = protocol()
    proto["budget_admission"]["status"] = "approved"
    with pytest.raises(C.ComparisonError, match="budget_admission"):
        build(tmp_path, proto)


@pytest.mark.parametrize("status", ["included-only-overflow-disabled", "paid-route-bound-verified"])
def test_an_attested_status_needs_a_timestamp_and_a_reference(tmp_path, status):
    proto = protocol()
    proto["budget_admission"]["status"] = status
    with pytest.raises(C.ComparisonError, match="checked_utc"):
        build(tmp_path, proto)


def test_an_attested_status_with_an_unsafe_reference_is_refused(tmp_path):
    proto = protocol()
    proto["budget_admission"] = {"status": "paid-route-bound-verified",
                                 "checked_utc": "2026-09-17T00:00:00Z",
                                 "evidence_ref": "/home/someone/private/receipt.txt"}
    with pytest.raises(C.ComparisonError, match="evidence_ref"):
        build(tmp_path, proto)


def test_an_attestation_is_never_presented_as_a_provider_or_cash_check(tmp_path):
    proto = protocol()
    proto["budget_admission"] = {"status": "included-only-overflow-disabled",
                                 "checked_utc": "2026-09-17T00:00:00Z",
                                 "evidence_ref": "evidence/admission.txt"}
    admission = build(tmp_path, proto)["budget_admission"]
    assert admission["attested"] is True
    assert admission["provider_checked"] is False
    assert "attestation" in admission["note"].lower()


def test_an_attestation_never_turns_cash_into_a_number(tmp_path):
    proto = protocol()
    proto["budget_admission"] = {"status": "paid-route-bound-verified",
                                 "checked_utc": "2026-09-17T00:00:00Z",
                                 "evidence_ref": "evidence/admission.txt"}
    cash = build(tmp_path, proto)["cash"]
    assert cash["spend_usd"] is None and cash["headroom_usd"] is None
    assert cash["status"] == "UNKNOWN - not connected"


# --- allocated API-equivalent is descriptive only -------------------------------------------

def test_the_allocated_subtotal_is_summed_as_decimals_per_arm_side(tmp_path):
    money = only_class(build(tmp_path))["arms"]["baseline"]["allocated_api_equivalent"]
    assert money["known_subtotal_usd"] == "3.75"
    assert money["display_usd"] == "3.75"
    assert money["is_cash"] is False


def test_the_allocated_figure_is_never_used_as_a_cash_admission(tmp_path):
    money = only_class(build(tmp_path))["arms"]["baseline"]["allocated_api_equivalent"]
    assert "not cash" in money["qualification"].lower()
    assert "allocated-not-cash" in codes(build(tmp_path)["limitations"])


def test_incomplete_pricing_is_carried_through_to_the_class(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"partial": True}})
    money = only_class(comparison)["arms"]["baseline"]["allocated_api_equivalent"]
    assert money["pricing_complete"] is False
    assert "pricing-incomplete" in codes(only_class(comparison)["rationale"])


def test_incomplete_root_coverage_is_carried_through_to_the_class(tmp_path):
    # The code was renamed when the one coverage cause became three; the property is unchanged.
    comparison = build(tmp_path, arms={"g-base-1": {"unknown_root": True}})
    money = only_class(comparison)["arms"]["baseline"]["allocated_api_equivalent"]
    assert money["coverage_complete"] is False
    assert "requested-roots-unresolved" in codes(only_class(comparison)["rationale"])


def test_contradicted_token_counts_are_carried_through_to_the_class(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"conflict": True}})
    money = only_class(comparison)["arms"]["baseline"]["allocated_api_equivalent"]
    assert money["normalized_tokens_known"] is False
    assert "token-totals-unknown" in codes(only_class(comparison)["rationale"])


# --- candidacy ------------------------------------------------------------------------------

def attested(proto=None):
    proto = proto or protocol()
    proto["budget_admission"] = {"status": "included-only-overflow-disabled",
                                 "checked_utc": "2026-09-17T00:00:00Z",
                                 "evidence_ref": "evidence/admission.txt"}
    return proto


def candidate_shape(pairs=3, synthetic=False):
    proto = attested(protocol(pairs=pairs, synthetic=synthetic))
    return proto


def live_arms(proto):
    return {arm["goal_id"]: {"synthetic": False}
            for entry in proto["pairs"] for arm in (entry["baseline"], entry["delegated"])}


def test_a_synthetic_comparison_never_becomes_a_candidate(tmp_path):
    comparison = build(tmp_path, candidate_shape())
    assert comparison["status"] == "inconclusive"
    assert comparison["synthetic"] is True
    assert "synthetic-inputs" in codes(comparison["rationale"])


def test_a_synthetic_source_report_alone_keeps_the_comparison_synthetic(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    arms["g-del-1"] = {"synthetic": True}
    comparison = build(tmp_path, proto, arms=arms)
    assert comparison["synthetic"] is True
    assert comparison["status"] == "inconclusive"


def test_a_candidate_shaped_comparison_with_no_synthetic_input_reads_candidate(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert comparison["status"] == "candidate"
    assert only_class(comparison)["status"] == "candidate"


def test_a_candidate_is_a_measurement_posture_and_never_an_approval(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert comparison["promotion"] == {"promoted": False, "approved": False,
                                       "note": C.NO_PROMOTION_NOTE}
    assert comparison["status"] in C.STATUSES
    payload = json.dumps(comparison)
    assert not re.search(r'"(status|posture|verdict)":\s*"(promoted|approved)"', payload)
    assert "no-promotion" in codes(comparison["limitations"])


def test_fewer_than_three_fully_observed_pairs_cannot_be_a_candidate(tmp_path):
    proto = candidate_shape(pairs=2, synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert comparison["status"] == "inconclusive"
    assert "fully-observed-pairs-below-minimum" in codes(only_class(comparison)["rationale"])


def test_a_worse_delegated_acceptance_rate_is_a_clean_observed_failure(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    arms["g-del-1"] = {"synthetic": False, "status": "failed"}
    comparison = build(tmp_path, proto, arms=arms)
    assert comparison["status"] == "not-candidate"
    assert "delegated-acceptance-rate-lower" in codes(only_class(comparison)["rationale"])


def test_a_worse_delegated_defect_rate_is_a_clean_observed_failure(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto), escaped={"g-del-1": 2})
    assert comparison["status"] == "not-candidate"
    assert "delegated-defect-rate-higher" in codes(only_class(comparison)["rationale"])


def test_an_unknown_defect_attestation_yields_inconclusive_not_a_negative_verdict(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto), escaped={"g-del-1": None})
    assert comparison["status"] == "inconclusive"
    assert "defect-attestation-unknown" in codes(only_class(comparison)["rationale"])


def test_exactly_twenty_percent_faster_meets_the_latency_gate(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    for index in (1, 2, 3):
        arms[f"g-base-{index}"] |= {"accepted": "2026-09-22T00:00:00Z"}    # 432000s
        arms[f"g-del-{index}"] |= {"accepted": "2026-09-21T00:00:00Z"}     # 345600s == 0.8x
    comparison = build(tmp_path, proto, arms=arms)
    assert only_class(comparison)["comparison"]["latency_improvement_met"] is True
    assert comparison["status"] == "candidate"


def test_one_second_short_of_the_threshold_is_not_a_candidate(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    for index in (1, 2, 3):
        arms[f"g-base-{index}"] |= {"accepted": "2026-09-22T00:00:00Z"}
        arms[f"g-del-{index}"] |= {"accepted": "2026-09-21T00:00:01Z"}     # 345601s
    comparison = build(tmp_path, proto, arms=arms)
    assert only_class(comparison)["comparison"]["latency_improvement_met"] is False
    assert comparison["status"] == "not-candidate"
    assert "latency-improvement-below-threshold" in codes(only_class(comparison)["rationale"])


def test_an_unknown_median_blocks_the_latency_gate_without_deciding_against_it(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    for index in (1, 2, 3):
        arms[f"g-del-{index}"] |= {"status": "failed"}
    comparison = build(tmp_path, proto, arms=arms)
    entry = only_class(comparison)
    assert entry["comparison"]["latency_improvement_met"] is None
    assert comparison["status"] == "not-candidate"  # the acceptance rate already decided it


def test_incomplete_coverage_yields_inconclusive_rather_than_a_verdict(tmp_path):
    proto = candidate_shape(synthetic=False)
    arms = live_arms(proto)
    arms["g-base-1"] |= {"unknown_root": True}
    comparison = build(tmp_path, proto, arms=arms)
    assert comparison["status"] == "inconclusive"


def test_a_required_day_with_unknown_minutes_blocks_candidacy(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto), interventions=[
        {"id": "c-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": None}])
    assert comparison["status"] == "inconclusive"
    assert "required-minutes-unknown" in codes(only_class(comparison)["rationale"])


def test_an_exceeded_attention_cap_blocks_candidacy(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto), interventions=[
        {"id": "c-1", "at_utc": "2026-09-18T12:00:00Z", "required": True, "minutes": 25}])
    assert comparison["status"] != "candidate"
    assert "minute-cap-exceeded" in codes(only_class(comparison)["rationale"])


def test_every_gate_is_reported_as_a_named_record_with_its_own_answer(tmp_path):
    gates = only_class(build(tmp_path))["gates"]
    assert len(gates) == len(C.GATE_ORDER)
    assert [item["code"] for item in gates] == list(C.GATE_ORDER)
    assert all(item["met"] in (True, False, None) for item in gates)
    assert all(item["text"] for item in gates)
    # Evidence and direction are named, not implied by where a gate happens to sit in the list.
    assert {item["kind"] for item in gates} == {"evidence", "directional"}


def test_the_comparison_verdicts_name_the_gate_they_came_from(tmp_path):
    entry = only_class(build(tmp_path))
    assert entry["comparison"]["acceptance_rate_not_lower"] == \
        gate(entry, "delegated-acceptance-not-lower")["met"]
    assert entry["comparison"]["defect_rate_not_higher"] == \
        gate(entry, "delegated-defect-rate-not-higher")["met"]
    assert entry["comparison"]["latency_improvement_met"] == \
        gate(entry, "delegated-latency-lower")["met"]


def test_the_top_level_is_candidate_only_when_no_class_carries_a_blocking_unknown(tmp_path):
    proto = candidate_shape(synthetic=False)
    proto["work_classes"] = [work_class("bounded-code"), work_class("bounded-docs")]
    proto["pairs"] = [pair(i) for i in range(1, 4)] + [pair(i, "bounded-docs") for i in range(4, 7)]
    arms = live_arms(proto)
    arms["g-base-4"] |= {"unknown_root": True}
    comparison = build(tmp_path, proto, arms=arms)
    assert comparison["status"] == "inconclusive"
    assert {entry["status"] for entry in comparison["classes"]} == {"candidate", "inconclusive"}


def test_a_mixed_cohort_never_headlines_candidate(tmp_path):
    # One class candidate, one decisively negative. A cohort headline of CANDIDATE would hand the
    # reader the half of the evidence that agrees with it.
    proto = candidate_shape(synthetic=False)
    proto["work_classes"] = [work_class("bounded-code"), work_class("bounded-docs")]
    proto["pairs"] = [pair(i) for i in range(1, 4)] + [pair(i, "bounded-docs") for i in range(4, 7)]
    arms = live_arms(proto)
    for index in (4, 5, 6):
        arms[f"g-del-{index}"] |= {"accepted": "2026-09-22T00:00:00Z"}
    comparison = build(tmp_path, proto, arms=arms)
    assert {entry["status"] for entry in comparison["classes"]} == {"candidate", "not-candidate"}
    assert comparison["status"] == "inconclusive"
    assert "mixed-class-results" in codes(comparison["rationale"])


def test_a_cohort_is_a_candidate_only_when_every_declared_class_is(tmp_path):
    proto = candidate_shape(synthetic=False)
    proto["work_classes"] = [work_class("bounded-code"), work_class("bounded-docs")]
    proto["pairs"] = [pair(i) for i in range(1, 4)] + [pair(i, "bounded-docs") for i in range(4, 7)]
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert {entry["status"] for entry in comparison["classes"]} == {"candidate"}
    assert comparison["status"] == "candidate"


# --- controls are attested, never verified ---------------------------------------------------

def test_a_protocol_with_no_controls_evidence_can_never_be_a_candidate(tmp_path):
    proto = candidate_shape(synthetic=False)
    proto["policy"]["controls_evidence_ref"] = None
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert gate(only_class(comparison), "controls-attested")["met"] is None
    assert comparison["status"] == "inconclusive"
    assert "controls-not-attested" in codes(only_class(comparison)["rationale"])


def test_an_unsafe_controls_reference_leaves_the_controls_gate_unknown(tmp_path):
    proto = candidate_shape(synthetic=False)
    proto["policy"]["controls_evidence_ref"] = "/home/someone/private/controls.txt"
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert gate(only_class(comparison), "controls-attested")["met"] is None
    assert comparison["status"] == "inconclusive"
    assert "evidence-ref-withheld" in codes(comparison["limitations"])


def test_safe_controls_evidence_is_an_attestation_and_never_a_verification(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert gate(only_class(comparison), "controls-attested")["met"] is True
    assert comparison["controls"]["verified"] is False
    assert comparison["controls"]["evidence"]["ref"] == CONTROLS_REF
    assert "controls-attested-not-verified" in codes(comparison["limitations"])


def test_the_controls_limitation_is_present_even_with_no_attestation(tmp_path):
    assert "controls-attested-not-verified" in codes(build(tmp_path)["limitations"])


def test_the_matching_limitation_says_the_arms_comparability_is_declared(tmp_path):
    limitation = next(item for item in build(tmp_path)["limitations"]
                      if item["code"] == "matching-is-declared-not-verified")
    assert "matched" in limitation["text"].lower()


def test_the_small_sample_limitation_is_always_present(tmp_path):
    assert "small-sample-no-statistical-weight" in codes(build(tmp_path)["limitations"])


# --- no vacuous class ---------------------------------------------------------------------------

def test_a_declared_work_class_no_pair_uses_is_refused(tmp_path):
    proto = protocol()
    proto["work_classes"] = [work_class("bounded-code"), work_class("never-used")]
    with pytest.raises(C.ComparisonError, match="work class"):
        build(tmp_path, proto)


# --- synthetic input decides nothing, in either direction ----------------------------------------

def test_a_synthetic_directional_failure_is_inconclusive_not_a_negative_verdict(tmp_path):
    proto = attested(protocol())          # synthetic stays True
    arms = {}
    for index in (1, 2, 3):
        arms[f"g-del-{index}"] = {"accepted": "2026-09-30T00:00:00Z"}
    comparison = build(tmp_path, proto, arms=arms)
    assert gate(only_class(comparison), "delegated-latency-lower")["met"] is False
    assert comparison["status"] == "inconclusive"
    assert "synthetic-inputs" in codes(comparison["rationale"])


# --- limitations and privacy -----------------------------------------------------------------

def test_the_source_allocation_limitation_survives_a_unique_root_partition(tmp_path):
    comparison = build(tmp_path)
    assert comparison["source_roots"]["partition_exact"] is True
    assert "source-allocation-not-attribution" in codes(comparison["limitations"])


def test_the_sub_day_and_mixed_goal_limitation_is_explicit(tmp_path):
    comparison = build(tmp_path)
    assert "utc-day-precision" in codes(comparison["limitations"])
    text = next(item["text"] for item in comparison["limitations"]
                if item["code"] == "utc-day-precision")
    assert "utc-day" in text and "sub-day" in text.lower()


def test_no_promotion_limitation_is_always_present(tmp_path):
    assert "no-promotion" in codes(build(tmp_path)["limitations"])


def test_no_raw_root_id_survives_into_the_comparison(tmp_path):
    payload = json.dumps(build(tmp_path))
    assert "root-base-1" not in payload and "root-del-1" not in payload


def test_no_private_report_path_or_hash_survives_into_the_comparison(tmp_path):
    proto_path, obs_path = stage(tmp_path, raw=True)
    comparison = C.load_comparison(proto_path, obs_path, now_utc=NOW)
    payload = json.dumps(comparison)
    assert "reports/g-base-1.json" not in payload
    digest = hashlib.sha256((tmp_path / "reports" / "g-base-1.json").read_bytes()).hexdigest()
    assert digest not in payload
    assert str(tmp_path) not in payload
    snapshot = json.loads((tmp_path / "reports" / "g-base-1.json").read_text())
    assert snapshot["source"]["snapshot_sha256"] not in payload


def test_no_model_id_or_price_fingerprint_survives_into_the_comparison(tmp_path):
    payload = json.dumps(build(tmp_path))
    assert "model-a" not in payload
    assert "abc123def456abcd" not in payload


def test_the_acceptance_oracle_reference_passes_the_shared_reference_predicate(tmp_path):
    oracle = build(tmp_path)["sources"]["acceptance_oracles"][0]
    assert oracle["ref"] == "oracles/bounded-code.md"
    assert oracle["ref_kind"] == "relative-path"


@pytest.mark.parametrize("ref", OBFUSCATED)
def test_an_obfuscated_oracle_reference_is_withheld_consistently(tmp_path, ref):
    proto = protocol()
    proto["work_classes"] = [work_class(ref=ref)]
    comparison = build(tmp_path, proto)
    oracle = comparison["sources"]["acceptance_oracles"][0]
    assert oracle["ref"] is None
    assert oracle["ref_kind"] != "relative-path"
    assert ref.strip() not in json.dumps(comparison)
    assert "evidence-ref-withheld" in codes(comparison["limitations"])


def test_the_staleness_anchor_is_the_observations_as_of_time(tmp_path):
    assert build(tmp_path)["sources"]["as_of_utc"] == AS_OF


def test_a_ledger_that_predates_the_protocol_it_freezes_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="as_of_utc"):
        build(tmp_path, as_of="2026-09-15T00:00:00Z")


# --- output ------------------------------------------------------------------------------------

def test_the_comparison_is_written_atomically_and_privately(tmp_path):
    out = tmp_path / "comparison.json"
    C.write_comparison(build(tmp_path), out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert json.loads(out.read_text())["kind"] == "goal-comparison-report"


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path):
    out = tmp_path / "comparison.json"
    out.write_text('{"previous": true}')
    with pytest.raises(R.ReportError):
        C.write_comparison({"unserializable": {1, 2}}, out)
    assert json.loads(out.read_text()) == {"previous": True}


# --- the CLI as a real subprocess ----------------------------------------------------------------

def run(args):
    return subprocess.run([sys.executable, "-m", "goals", *args],
                          cwd=str(ROOT), capture_output=True, text=True)


def test_the_cli_writes_a_comparison_and_exits_zero(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    out = tmp_path / "comparison.json"
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(out), "--now", NOW])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["generated_utc"] == NOW


def test_the_cli_writes_html_when_asked(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    out, page = tmp_path / "comparison.json", tmp_path / "comparison.html"
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(out), "--html", str(page), "--now", NOW])
    assert proc.returncode == 0, proc.stderr
    assert page.read_text().lstrip().lower().startswith("<!doctype html")


def test_the_cli_refuses_the_same_path_for_json_and_html(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    out = tmp_path / "both.json"
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(out), "--html", str(out)])
    assert proc.returncode == 2
    assert "same file" in proc.stderr


def test_the_cli_refuses_to_write_over_one_of_its_inputs(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    before = proto_path.read_bytes()
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(proto_path)])
    assert proc.returncode == 2
    assert proto_path.read_bytes() == before


def test_an_invalid_protocol_exits_two_with_a_concise_cause(tmp_path):
    proto_path, obs_path = stage(tmp_path, protocol(schema_version=9))
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json")])
    assert proc.returncode == 2
    assert "schema_version" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) <= 2
    assert not (tmp_path / "comparison.json").exists()


def test_a_cli_error_never_echoes_a_raw_root_id(tmp_path):
    proto = protocol()
    proto["pairs"][0]["baseline"]["roots"] = ["root-s3cr3t-session-id"]
    proto["pairs"][0]["delegated"]["roots"] = ["root-s3cr3t-session-id"]
    proto_path, obs_path = stage(tmp_path, proto, reports=reports_for(protocol()))
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json")])
    assert proc.returncode == 2
    assert "s3cr3t" not in proc.stderr and "s3cr3t" not in proc.stdout


def test_a_failing_html_write_preserves_the_json_and_says_so(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    out = tmp_path / "comparison.json"
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(out), "--html", str(tmp_path / "absent" / "page.html"), "--now", NOW])
    assert proc.returncode == 2
    assert out.exists() and json.loads(out.read_text())["kind"] == "goal-comparison-report"
    assert "partial" in proc.stderr.lower()
    assert str(out) in proc.stderr


def test_the_compare_help_names_what_it_will_not_do():
    proc = run(["compare", "--help"])
    assert proc.returncode == 0
    assert "never" in proc.stdout.lower()
    assert "cash" in proc.stdout.lower()


def test_the_report_subcommand_still_exists_beside_compare():
    proc = run(["--help"])
    assert "report" in proc.stdout and "compare" in proc.stdout


def test_the_package_exports_the_comparison_public_face():
    import goals
    assert {"load_comparison", "write_comparison", "render_comparison",
            "ComparisonError"} <= set(goals.__all__)


# --- a rate is never divided by a population whose outcome is unknown ----------------------------

MIXED_OUTCOMES = {"g-base-2": {"status": "failed"}, "g-base-3": {"status": "blocked"},
                  "g-base-4": {"status": "in_progress"}}


def test_a_side_with_no_unresolved_arm_publishes_a_numerical_acceptance_rate(tmp_path):
    # accepted / failed / blocked / censored, every outcome placed. The denominator is every arm
    # in the side, and nothing is excluded to make the number look computable.
    entry = only_class(build(tmp_path, protocol(pairs=4), arms=MIXED_OUTCOMES))
    acceptance = entry["arms"]["baseline"]["acceptance"]
    assert entry["arms"]["baseline"]["classification"] == {
        "accepted": 1, "failed": 1, "blocked": 1, "abandoned": 0,
        "censored": 1, "pending": 0, "unresolved": 0}
    assert acceptance["rate_percent"] == "25.0"
    assert acceptance["arm_count"] == 4 and acceptance["known_arm_count"] == 4
    assert acceptance["unresolved_arm_count"] == 0
    assert acceptance["reason"] is None


def test_an_unattested_defect_nulls_the_defect_rate_while_acceptance_stays_numerical(tmp_path):
    comparison = build(tmp_path, protocol(pairs=4), arms=MIXED_OUTCOMES,
                       escaped={"g-base-2": None})
    side = only_class(comparison)["arms"]["baseline"]
    assert side["acceptance"]["rate_percent"] == "25.0"
    assert side["defects"]["rate_per_arm"] is None
    assert side["defects"]["reason"] == "unattested-arms"
    assert side["defects"]["known"] == 3 and side["defects"]["unknown"] == 1


def test_one_unresolved_arm_nulls_both_rates_and_leaves_every_count_standing(tmp_path):
    arms = dict(MIXED_OUTCOMES, **{"g-base-5": {"status": "reopened"}})
    comparison = build(tmp_path, protocol(pairs=5), arms=arms)
    side = only_class(comparison)["arms"]["baseline"]
    assert side["classification"] == {"accepted": 1, "failed": 1, "blocked": 1, "abandoned": 0,
                                      "censored": 1, "pending": 0, "unresolved": 1}
    assert side["acceptance"]["rate_percent"] is None
    assert side["acceptance"]["reason"] == "unresolved-arms"
    assert side["acceptance"]["accepted"] == 1 and side["acceptance"]["arm_count"] == 5
    assert side["acceptance"]["known_arm_count"] == 4
    assert side["acceptance"]["unresolved_arm_count"] == 1
    assert side["defects"]["rate_per_arm"] is None
    assert side["defects"]["reason"] == "unresolved-arms"
    assert comparison["status"] == "inconclusive"


def test_an_unresolved_arm_is_never_credited_with_zero_defects(tmp_path):
    side = only_class(build(tmp_path, attest_terminal=False,
                            arms={"g-base-1": {"status": "blocked"}}))["arms"]["baseline"]
    assert side["defects"]["rate_per_arm"] is None
    assert side["defects"]["unresolved_arm_count"] == 1


def test_a_rate_is_never_made_numerical_by_dropping_the_unresolved_arm(tmp_path):
    # The tempting repair - divide by the arms that did resolve - changes the population the
    # experiment was run over, so the denominator stays whole and the rate stays unknown.
    side = only_class(build(tmp_path, attest_terminal=False,
                            arms={"g-base-1": {"status": "blocked"}}))["arms"]["baseline"]
    assert side["acceptance"]["arm_count"] == 3
    assert side["acceptance"]["known_arm_count"] == 2
    assert side["acceptance"]["rate_percent"] is None


@pytest.mark.parametrize("code", ["delegated-acceptance-not-lower",
                                  "delegated-defect-rate-not-higher"])
def test_a_null_rate_is_never_paired_with_a_met_directional_gate(tmp_path, code):
    entry = only_class(build(tmp_path, attest_terminal=False,
                             arms={"g-base-1": {"status": "blocked"}}))
    assert entry["arms"]["baseline"]["acceptance"]["rate_percent"] is None
    assert gate(entry, code)["met"] is not True


# --- the ledger may only cite evidence from inside its own observation window --------------------

@pytest.mark.parametrize("at", ["2026-09-16T23:59:59Z",   # the window has not opened
                                "2026-10-01T00:00:01Z"])  # the horizon has closed
def test_an_intervention_outside_the_protocol_window_is_refused(tmp_path, at):
    with pytest.raises(C.ComparisonError, match="at_utc"):
        build(tmp_path, interventions=[
            {"id": "c-1", "at_utc": at, "required": True, "minutes": 5}])


def test_an_intervention_after_the_ledger_cutoff_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="at_utc"):
        build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF, interventions=[
            {"id": "c-1", "at_utc": "2026-09-25T00:00:00Z", "required": True, "minutes": 5}])


@pytest.mark.parametrize("at", [WINDOW_START, HORIZON_END])
def test_an_intervention_exactly_on_a_bound_is_kept(tmp_path, at):
    attention = build(tmp_path, interventions=[
        {"id": "c-1", "at_utc": at, "required": True, "minutes": 5}])["attention"]
    assert attention["required_day_count"] == 1


def test_an_out_of_window_intervention_can_never_satisfy_the_attention_cap(tmp_path):
    # The whole point: a ledger whose only required check-ins fall outside the window it declares
    # must not answer the attention gate `met`. It is refused, so the author fixes the input.
    proto = candidate_shape(synthetic=False)
    with pytest.raises(C.ComparisonError, match="at_utc"):
        build(tmp_path, proto, arms=live_arms(proto), interventions=[
            {"id": "c-1", "at_utc": "2026-08-01T00:00:00Z", "required": True, "minutes": 5}])


def test_a_budget_attestation_before_the_protocol_was_created_is_refused(tmp_path):
    proto = protocol()
    proto["budget_admission"] = {"status": "included-only-overflow-disabled",
                                 "checked_utc": "2026-09-15T00:00:00Z",
                                 "evidence_ref": "evidence/admission.txt"}
    with pytest.raises(C.ComparisonError, match="checked_utc"):
        build(tmp_path, proto)


def test_a_budget_attestation_after_the_ledger_cutoff_is_refused(tmp_path):
    proto = protocol()
    proto["budget_admission"] = {"status": "included-only-overflow-disabled",
                                 "checked_utc": "2027-06-01T00:00:00Z",
                                 "evidence_ref": "evidence/admission.txt"}
    with pytest.raises(C.ComparisonError, match="checked_utc"):
        build(tmp_path, proto)


def test_a_preflight_budget_attestation_before_the_window_opens_is_kept(tmp_path):
    # Checking the budget BEFORE opening the window is the right order to do it in, so this one is
    # bounded by the protocol's own creation instant rather than by the window.
    proto = candidate_shape(synthetic=False)
    proto["budget_admission"]["checked_utc"] = "2026-09-16T12:00:00Z"
    comparison = build(tmp_path, proto, arms=live_arms(proto))
    assert comparison["budget_admission"]["attested"] is True
    assert comparison["status"] == "candidate"


def test_in_window_records_still_reach_the_candidate_shape(tmp_path):
    proto = candidate_shape(synthetic=False)
    assert build(tmp_path, proto, arms=live_arms(proto))["status"] == "candidate"


def test_the_cli_exits_two_without_a_traceback_on_an_out_of_window_intervention(tmp_path):
    proto_path, obs_path = stage(tmp_path, interventions=[
        {"id": "c-1", "at_utc": "2026-08-01T00:00:00Z", "required": True, "minutes": 5}])
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "at_utc" in proc.stderr


# --- the ledger binds the report BYTES it was collected from ---------------------------------------

def test_every_observation_must_carry_the_report_digest(tmp_path):
    with pytest.raises(C.ComparisonError, match="report_sha256"):
        build(tmp_path, drop_report_sha=True)


def test_a_malformed_report_digest_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="report_sha256"):
        build(tmp_path, report_sha={"g-base-1": "not-a-digest"})


def test_a_report_edited_after_the_ledger_froze_it_is_refused(tmp_path):
    def rewrite(raw):
        return raw.replace(b'"2026-09-22T00:00:00Z"', b'"2026-09-18T00:00:00Z"')

    with pytest.raises(C.ComparisonError, match="report_sha256"):
        build(tmp_path, mutate_report={"g-base-1": rewrite})


def test_even_a_whitespace_only_change_to_a_report_breaks_its_freeze(tmp_path):
    with pytest.raises(C.ComparisonError, match="report_sha256"):
        build(tmp_path, mutate_report={"g-base-1": lambda raw: raw + b"\n"})


def test_the_report_digest_is_the_file_digest_a_reviewer_can_reproduce(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    ledger = json.loads(obs_path.read_text())
    for entry in ledger["observations"]:
        path = obs_path.parent / entry["report_path"]
        assert entry["report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert C.load_comparison(proto_path, obs_path, now_utc=NOW)["kind"] == "goal-comparison-report"


def test_the_cost_snapshot_digest_is_never_compared_to_the_report_digest(tmp_path):
    # `source.snapshot_sha256` fingerprints the upstream COST SNAPSHOT file. It is validated only
    # against its own documented meaning; a bundle whose report digest differs from it - which is
    # always - must load.
    proto_path, obs_path = stage(tmp_path, raw=True)
    comparison = C.load_comparison(proto_path, obs_path, now_utc=NOW)
    report = json.loads((tmp_path / "reports" / "g-base-1.json").read_text())
    ledger = json.loads(obs_path.read_text())
    frozen = next(e["report_sha256"] for e in ledger["observations"] if e["goal_id"] == "g-base-1")
    assert report["source"]["snapshot_sha256"] != frozen
    assert comparison["sources"]["report_provenance"]["raw_bytes"] == 6


def test_a_report_generated_after_the_ledger_cutoff_is_refused(tmp_path):
    with pytest.raises(C.ComparisonError, match="generated"):
        build(tmp_path, generated="2026-10-02T00:00:01Z")


def test_a_report_generated_exactly_at_the_ledger_cutoff_is_accepted(tmp_path):
    assert build(tmp_path, generated=AS_OF)["status"] in C.STATUSES


def test_the_comparison_may_not_be_generated_before_its_own_evidence(tmp_path):
    with pytest.raises(C.ComparisonError, match="as_of_utc"):
        build(tmp_path, now="2026-10-01T23:00:00Z", as_of=AS_OF)


# --- keyed handles: the protocol holds the same private key --------------------------------------

def test_the_protocol_needs_the_same_salt_the_manifests_used(tmp_path):
    proto = protocol(scope_fingerprint_salt="another-private-salt-0123456789abcdefgh")
    with pytest.raises(C.ComparisonError, match="fingerprint"):
        build(tmp_path, proto)


def test_a_protocol_without_a_salt_is_refused(tmp_path):
    proto = protocol()
    del proto["scope_fingerprint_salt"]
    with pytest.raises(C.ComparisonError, match="scope_fingerprint_salt"):
        build(tmp_path, proto)


@pytest.mark.parametrize("bad", ["", "short", "x" * 31, "a" * 64])
def test_a_short_or_low_entropy_protocol_salt_is_refused(tmp_path, bad):
    with pytest.raises(C.ComparisonError, match="scope_fingerprint_salt"):
        build(tmp_path, protocol(scope_fingerprint_salt=bad))


def test_a_legacy_unkeyed_fingerprint_report_is_refused_not_called_opaque(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["source"]["scope"]["root_fingerprint_algorithm"] = \
        "sha256(goal-root-v1\\0<root-id>)"
    with pytest.raises(C.ComparisonError, match="algorithm"):
        build(tmp_path, proto, reports=built)


def test_the_salt_never_reaches_the_comparison_output(tmp_path):
    comparison = build(tmp_path)
    payload = json.dumps(comparison) + V.render_comparison(comparison)
    assert SALT not in payload


def test_no_root_handle_value_reaches_the_comparison_output(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    comparison = build(tmp_path, proto, reports=built)
    payload = json.dumps(comparison) + V.render_comparison(comparison)
    for report in built.values():
        for handle in report["source"]["scope"]["root_fingerprints"]:
            assert handle not in payload


# --- attention: an optional-only ledger proves nothing about the required caps -------------------

def test_an_optional_only_intervention_ledger_leaves_the_required_caps_unknown(tmp_path):
    proto = candidate_shape(synthetic=False)
    comparison = build(tmp_path, proto, arms=live_arms(proto), interventions=[
        {"id": "peek-1", "at_utc": "2026-09-18T12:00:00Z", "required": False, "minutes": 30}])
    attention = comparison["attention"]
    assert attention["recorded_day_count"] == 1
    assert attention["required_day_count"] == 0
    assert attention["within_caps"] is None
    entry = only_class(comparison)
    assert gate(entry, "attention-within-caps")["met"] is None
    assert "required-attention-not-recorded" in codes(entry["rationale"])
    assert comparison["status"] == "inconclusive"


def test_an_optional_only_ledger_still_keeps_its_optional_record(tmp_path):
    day = build(tmp_path, interventions=[
        {"id": "peek-1", "at_utc": "2026-09-18T12:00:00Z", "required": False, "minutes": 30}
    ])["attention"]["days"][0]
    assert day["optional_count"] == 1 and day["required_count"] == 0


# --- malformed source reports exit 2, never a traceback -------------------------------------------

def test_a_time_to_acceptance_that_claims_known_without_seconds_is_a_clean_error(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    del built["g-base-1"]["time"]["time_to_acceptance"]["seconds"]
    with pytest.raises(C.ComparisonError, match="seconds"):
        build(tmp_path, proto, reports=built)


def test_the_cli_exits_two_without_a_traceback_on_a_malformed_arm_report(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    del built["g-base-1"]["time"]["time_to_acceptance"]["seconds"]
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "seconds" in proc.stderr


@pytest.mark.parametrize("section,key", [("time", "time_to_acceptance"), ("usage", None),
                                         ("allocated_api_equivalent", None), ("outcome", None),
                                         ("scope", None), ("source", None), ("goal", None)])
def test_a_report_missing_any_required_section_exits_two_without_a_traceback(tmp_path, section, key):
    proto = protocol()
    built = reports_for(proto)
    if key is None:
        del built["g-base-1"][section]
    else:
        del built["g-base-1"][section][key]
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr


# --- final I1: the latency interval must lie inside the comparison window -----------------
# The END of `accepted - authorized` was bounded at three edges; the START was bounded nowhere,
# so an arm authorized before the protocol existed could carry the 20% gate to `candidate`.

def test_an_arm_authorized_long_before_the_window_cannot_settle_its_pair(tmp_path):
    # The reviewer's case: baselines authorized 200 days early, accepted inside the window.
    comparison = build(tmp_path, arms={arm: {"authorized": "2026-03-01T00:00:00Z"}
                                       for arm in ("g-base-1", "g-base-2", "g-base-3")})
    for arm in ("g-base-1", "g-base-2", "g-base-3"):
        assert classification(comparison, arm)["classification"] == "unresolved"
        assert reason(comparison, arm) == "authorization-before-window"
        assert classification(comparison, arm)["latency_seconds"] is None
    entry = only_class(comparison)
    assert entry["fully_observed_pairs"] == 0
    assert entry["status"] != "candidate"


def test_an_arm_authorized_exactly_at_the_window_start_is_still_accepted(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": WINDOW_START}})
    assert classification(comparison, "g-base-1")["classification"] == "accepted"


def test_an_arm_authorized_at_its_own_acceptance_is_accepted_with_no_latency(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": BASELINE_ACCEPTED}})
    entry = classification(comparison, "g-base-1")
    assert entry["classification"] == "accepted"
    assert entry["latency_seconds"] == 0


def test_an_arm_authorized_after_its_own_acceptance_is_unresolved(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": "2026-09-23T00:00:00Z",
                                                    "accepted": "2026-09-22T00:00:00Z"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "authorization-after-acceptance"


def test_an_arm_authorized_after_the_horizon_is_unresolved(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": "2026-10-02T00:00:00Z"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "authorization-after-horizon"


def test_an_arm_authorized_after_the_ledger_cutoff_is_unresolved(tmp_path):
    # The ledger looked to 2026-09-24; an authorization it could not have seen establishes nothing.
    comparison = build(tmp_path, now=OPEN_NOW, as_of=OPEN_AS_OF,
                       arms={"g-base-1": {"authorized": "2026-09-26T00:00:00Z",
                                          "accepted": "2026-09-23T00:00:00Z"}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"
    assert reason(comparison, "g-base-1") == "authorization-after-cutoff"


def test_an_arm_with_no_authorization_instant_cannot_be_accepted(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": None}})
    assert classification(comparison, "g-base-1")["classification"] == "unresolved"


def test_every_authorization_reason_has_a_legend(tmp_path):
    for code in ("authorization-before-window", "authorization-after-acceptance",
                 "authorization-after-horizon", "authorization-after-cutoff"):
        assert code in C.UNRESOLVED_REASONS, code
        assert C.UNRESOLVED_REASONS[code]


def test_an_out_of_window_authorization_instant_never_reaches_the_output(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"authorized": "2026-03-01T00:00:00Z"}})
    assert "2026-03-01" not in json.dumps(comparison)


# --- final I2: malformed scope bounds are validation errors, never a traceback -------------

@pytest.mark.parametrize("field", ["since", "until"])
@pytest.mark.parametrize("bad", [1.5, 0, True, [], {}, {"a": 1.5}, "2026-9-1", "2026-09-01T00:00:00Z"])
def test_a_malformed_scope_bound_exits_two_without_a_traceback(tmp_path, field, bad):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["scope"][field] = bad
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "comparison.json").exists()


def test_inverted_scope_bounds_are_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["scope"]["since"] = "2026-09-10"
    built["g-base-1"]["scope"]["until"] = "2026-09-01"
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("field,bad", [("mode", 1), ("ownership", []), ("mode", None)])
def test_a_non_string_scope_field_exits_two_without_a_traceback(tmp_path, field, bad):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["scope"][field] = bad
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("bad", [1, None, [], {"a": 1}])
def test_a_non_string_engine_time_precision_exits_two_without_a_traceback(tmp_path, bad):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["source"]["time_precision"] = bad
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_valid_day_bounded_report_is_still_read_the_same_way(tmp_path):
    # The bounds validator must not change what a WELL-FORMED report means. A day-bounded scope is
    # refused for its mode, not for its bounds, and that cause must survive.
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["scope"]["since"] = "2026-09-01"
    built["g-base-1"]["scope"]["until"] = "2026-09-30"
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "comparison.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr


# --- G2 consumes the allocation-coverage verdict through scope.coverage_complete -------------
# The comparison already gates on `scope.coverage_complete`. These prove the new upstream verdict
# reaches that gate, and that it stops a candidate rather than being absorbed silently.

def test_an_arm_whose_sources_are_allocated_outside_its_scope_fails_the_coverage_gate(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": False}})
    entry = only_class(comparison)
    assert gate(entry, "source-coverage-complete")["met"] is not True
    assert entry["status"] != "candidate"


def test_a_legacy_arm_report_without_the_field_also_fails_the_coverage_gate(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": None}})
    entry = only_class(comparison)
    assert gate(entry, "source-coverage-complete")["met"] is not True
    assert entry["status"] != "candidate"


def test_incomplete_allocation_does_not_make_the_arm_price_partial(tmp_path):
    # Selection coverage is not price partialness: the money and its completeness are untouched.
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": False}})
    entry = only_class(comparison)
    assert "pricing-incomplete" not in codes(entry["rationale"])
    assert "source-allocation-outside-scope" in codes(entry["rationale"])


# --- the coverage gate must name the condition it actually failed on -------------------------

def test_allocation_outside_scope_is_not_reported_as_an_unresolved_root(tmp_path):
    # The reviewer's case: every root resolved, allocation incomplete. Sending a reader to hunt
    # for an unresolved root that does not exist is the defect.
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": False}})
    entry = only_class(comparison)
    assert "source-allocation-outside-scope" in codes(entry["rationale"])
    assert "coverage-incomplete" not in codes(entry["rationale"])


def test_an_unresolved_root_still_reports_the_unresolved_root_reason(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"unknown_root": True}})
    entry = only_class(comparison)
    assert "requested-roots-unresolved" in codes(entry["rationale"])
    assert "source-allocation-outside-scope" not in codes(entry["rationale"])


def test_a_legacy_arm_reports_the_coverage_unknown_reason(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": None}})
    entry = only_class(comparison)
    assert "source-allocation-coverage-unknown" in codes(entry["rationale"])
    assert "coverage-incomplete" not in codes(entry["rationale"])


def test_both_causes_are_reported_when_both_apply(tmp_path):
    comparison = build(tmp_path, arms={"g-base-1": {"unknown_root": True},
                                       "g-base-2": {"allocation": False}})
    reported = codes(only_class(comparison)["rationale"])
    assert {"requested-roots-unresolved", "source-allocation-outside-scope"} <= reported


def test_an_allocation_gap_leaves_the_coverage_gate_unknown_not_false(tmp_path):
    # Nobody claimed the arms cover less; only that the selection does not hold all of its work.
    for allocation in (False, None):
        entry = only_class(build(tmp_path, arms={"g-base-1": {"allocation": allocation}}))
        assert gate(entry, "source-coverage-complete")["met"] is None, allocation
        assert entry["status"] != "candidate"


def test_an_unresolved_root_still_makes_the_coverage_gate_false(tmp_path):
    entry = only_class(build(tmp_path, arms={"g-base-1": {"unknown_root": True}}))
    assert gate(entry, "source-coverage-complete")["met"] is False


def test_a_pricing_gap_still_makes_the_coverage_gate_false(tmp_path):
    entry = only_class(build(tmp_path, arms={"g-base-1": {"partial": True}}))
    assert gate(entry, "source-coverage-complete")["met"] is False
    assert "pricing-incomplete" in codes(entry["rationale"])


def test_the_coverage_gate_text_names_both_conditions_it_tests(tmp_path):
    entry = only_class(build(tmp_path))
    text = gate(entry, "source-coverage-complete")["text"]
    assert "root" in text
    assert "allocat" in text


def test_every_allocation_rationale_code_has_a_legend(tmp_path):
    for code in ("requested-roots-unresolved", "source-allocation-outside-scope",
                 "source-allocation-coverage-unknown"):
        assert C.RATIONALE_TEXT.get(code), code


def test_no_upstream_meaning_or_lineage_text_reaches_the_comparison(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": False}})
    blob = json.dumps(comparison)
    assert "deterministic global source allocation" not in blob
    assert "not metadata-verified" not in blob


def test_complete_allocation_coverage_leaves_the_gate_met(tmp_path):
    entry = only_class(build(tmp_path))
    assert gate(entry, "source-coverage-complete")["met"] is True


def test_no_upstream_identifier_reaches_the_comparison_output(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    comparison = build(tmp_path, arms={"g-base-1": {"allocation": False}})
    assert "allocatedElsewhere" not in json.dumps(comparison)


# --- the duration is re-derived, never trusted --------------------------------------------------

def with_time(report, **over):
    """Hand-edit a built report's time fields, the way an externally supplied report can be.

    This is the whole attack surface R34 names: a report is frozen into a ledger by its exact
    bytes, so a number altered BEFORE the freeze is carried faithfully by every integrity check
    the ledger has.
    """
    rep = copy.deepcopy(report)
    for key, value in over.items():
        if key == "accepted_utc":
            rep["outcome"]["accepted_utc"] = value
        elif key == "authorized_utc":
            rep["time"]["authorized_utc"] = value
        else:
            rep["time"]["time_to_acceptance"][key] = value
    return rep


def edited(tmp_path, goal_id="g-del-1", proto=None, **over):
    proto = proto or protocol()
    built = reports_for(proto)
    built[goal_id] = with_time(built[goal_id], **over)
    return proto, built


def test_a_five_day_interval_reported_as_one_second_is_refused(tmp_path):
    # The final reviewer's attack. Unchecked, this hands the delegated arm a one-second median
    # against a five-day baseline and manufactures the one improvement threshold in the document.
    proto, built = edited(tmp_path, seconds=1, human="0h 0m")
    with pytest.raises(C.ComparisonError, match="seconds"):
        build(tmp_path, proto, reports=built)


def test_the_manufactured_latency_gate_never_reaches_a_verdict(tmp_path):
    proto = candidate_shape(synthetic=False)
    built = reports_for(proto, live_arms(proto))
    for index in (1, 2, 3):
        built[f"g-del-{index}"] = with_time(built[f"g-del-{index}"], seconds=1)
    with pytest.raises(C.ComparisonError, match="seconds"):
        build(tmp_path, proto, reports=built)


def test_the_cli_exits_two_and_writes_nothing_on_an_altered_duration(tmp_path):
    proto, built = edited(tmp_path, seconds=1)
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    out = tmp_path / "comparison.json"
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(out), "--now", NOW])
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "seconds" in proc.stderr
    assert not out.exists()


def test_only_the_human_string_is_ignored_because_nothing_computes_from_it(tmp_path):
    proto, built = edited(tmp_path, human="0h 1m")
    comparison = build(tmp_path, proto, reports=built)
    assert classification(comparison, "g-del-1")["latency_seconds"] == 259200


def test_a_known_duration_without_an_authorization_instant_is_refused(tmp_path):
    proto, built = edited(tmp_path, authorized_utc=None)
    with pytest.raises(C.ComparisonError, match="instants"):
        build(tmp_path, proto, reports=built)


def test_a_known_duration_without_an_acceptance_instant_is_refused(tmp_path):
    proto, built = edited(tmp_path, accepted_utc=None)
    with pytest.raises(C.ComparisonError, match="instants"):
        build(tmp_path, proto, reports=built)


def test_a_known_duration_whose_acceptance_precedes_its_authorization_is_refused(tmp_path):
    proto, built = edited(tmp_path, accepted_utc="2026-09-16T00:00:00Z", seconds=86400)
    with pytest.raises(C.ComparisonError, match="precedes"):
        build(tmp_path, proto, reports=built)


@pytest.mark.parametrize("seconds", [-1, True, False, 259200.0, "259200", None])
def test_a_malformed_seconds_value_is_refused(tmp_path, seconds):
    proto, built = edited(tmp_path, seconds=seconds)
    with pytest.raises(C.ComparisonError, match="seconds"):
        build(tmp_path, proto, reports=built)


def test_an_unknown_duration_carrying_a_number_is_refused(tmp_path):
    proto, built = edited(tmp_path, known=False, seconds=259200)
    with pytest.raises(C.ComparisonError, match="seconds"):
        build(tmp_path, proto, reports=built)


def test_an_unknown_duration_with_no_number_is_read_as_unknown(tmp_path):
    # G1's own shape for an arm it could not time. It stays unresolved, as R26 already decided.
    comparison = build(tmp_path, arms={"g-del-1": {"authorized": None}})
    assert classification(comparison, "g-del-1")["classification"] == "unresolved"
    assert classification(comparison, "g-del-1")["latency_seconds"] is None


def test_an_exact_interval_is_accepted_and_its_median_is_unchanged(tmp_path):
    entry = only_class(build(tmp_path))
    assert entry["arms"]["delegated"]["latency"]["median_seconds"] == 259200
    assert entry["arms"]["baseline"]["latency"]["median_seconds"] == 432000


@pytest.mark.parametrize("fraction", ["400000", "600000", "999999"])
def test_a_subsecond_interval_is_truncated_exactly_as_the_reporter_truncates_it(tmp_path, fraction):
    # G1 computes int(total_seconds()), which truncates toward zero - 432000.6 is 432000, not
    # 432001. Re-deriving with the SAME rule is the point: rounding instead would reject the
    # reporter's own output on every interval whose fraction is half a second or more.
    comparison = build(tmp_path,
                       arms={"g-base-1": {"accepted": f"2026-09-22T00:00:00.{fraction}Z"}})
    assert classification(comparison, "g-base-1")["latency_seconds"] == 432000


def test_the_consistency_check_fires_even_where_the_arm_would_be_unresolved_anyway(tmp_path):
    # Validation happens before classification: an altered duration is a malformed report, not a
    # measurement outcome, so it is never absorbed into an unresolved arm.
    proto, built = edited(tmp_path, accepted_utc="2026-10-05T00:00:00Z", seconds=1)
    with pytest.raises(C.ComparisonError, match="seconds"):
        build(tmp_path, proto, reports=built)


# --- I-1: an arm report named by the ledger is an INPUT and must never be a destination -------

def test_out_targeting_a_ledger_named_arm_report_is_refused(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    victim = tmp_path / "reports" / "g-base-1.json"
    before = victim.read_bytes()
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(victim), "--now", NOW])
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert victim.read_bytes() == before


def test_html_targeting_a_ledger_named_arm_report_is_refused(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    victim = tmp_path / "reports" / "g-del-2.json"
    before = victim.read_bytes()
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "cmp.json"), "--html", str(victim), "--now", NOW])
    assert proc.returncode == 2, proc.stdout
    assert victim.read_bytes() == before
    assert not (tmp_path / "cmp.json").exists()


def test_a_relative_destination_pointing_at_an_arm_report_is_refused(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    victim = tmp_path / "reports" / "g-base-1.json"
    before = victim.read_bytes()
    proc = subprocess.run(
        [sys.executable, "-m", "goals", "compare", "--protocol", str(proto_path),
         "--observations", str(obs_path), "--out", "reports/g-base-1.json", "--now", NOW],
        cwd=str(tmp_path), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 2, proc.stdout
    assert victim.read_bytes() == before


def test_a_symlink_to_an_arm_report_is_refused_like_the_report_itself(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    victim = tmp_path / "reports" / "g-base-1.json"
    before = victim.read_bytes()
    link = tmp_path / "alias.json"
    link.symlink_to(victim)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(link), "--now", NOW])
    assert proc.returncode == 2, proc.stdout
    assert victim.read_bytes() == before


def test_the_existing_protocol_and_ledger_guards_still_hold(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    for target, name in ((proto_path, "protocol"), (obs_path, "observations")):
        before = target.read_bytes()
        proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                    "--out", str(target), "--now", NOW])
        assert proc.returncode == 2
        assert name in proc.stderr
        assert target.read_bytes() == before


def test_an_ordinary_destination_beside_the_reports_still_writes(tmp_path):
    proto_path, obs_path = stage(tmp_path)
    out = tmp_path / "reports" / "comparison.json"
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(out), "--now", NOW])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["kind"] == "goal-comparison-report"


# --- I-3: a report's derived coverage booleans must agree with the facts beside them ----------

def forge(built, goal_id, **over):
    """Alter an arm report the way an external author could, then let `stage` re-freeze it."""
    report = built[goal_id]
    for dotted, value in over.items():
        node = report
        *path, leaf = dotted.split(".")
        for step in path:
            node = node[step]
        node[leaf] = value
    return built


def test_a_forged_scope_coverage_boolean_is_refused(tmp_path):
    proto = protocol()
    built = forge(reports_for(proto), "g-base-1", **{"scope.coverage_complete": True})
    built["g-base-1"]["scope"]["roots_unknown"] = 1
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "cmp.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "cmp.json").exists()


def test_a_forged_whole_source_scope_boolean_is_refused(tmp_path):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["allocated_api_equivalent"]["whole_source_scope_covered"] = False
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "cmp.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stdout
    assert not (tmp_path / "cmp.json").exists()


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_a_coverage_boolean_that_is_not_a_real_bool_is_refused(tmp_path, value):
    proto = protocol()
    built = reports_for(proto)
    built["g-base-1"]["scope"]["coverage_complete"] = value
    proto_path, obs_path = stage(tmp_path, proto, reports=built)
    proc = run(["compare", "--protocol", str(proto_path), "--observations", str(obs_path),
                "--out", str(tmp_path / "cmp.json"), "--now", NOW])
    assert proc.returncode == 2, proc.stdout


@pytest.mark.parametrize("kind", ["clean", "allocation_false", "legacy", "unknown_root"])
def test_an_honest_report_of_every_shape_still_loads(tmp_path, kind):
    arms = {"clean": {},
            "allocation_false": {"g-base-1": {"allocation": False}},
            "legacy": {"g-base-1": {"allocation": None}},
            "unknown_root": {"g-base-1": {"unknown_root": True}}}[kind]
    comparison = build(tmp_path, arms=arms or None)
    assert comparison["kind"] == "goal-comparison-report"


# --- M6: a resolved root with no priced row is an unknown, not a confident zero ---------------

def test_a_rowless_arm_fails_the_coverage_and_pricing_gates(tmp_path):
    entry = only_class(build(tmp_path, arms={"g-base-1": {"rowless": True}}))
    assert gate(entry, "source-coverage-complete")["met"] is not True
    assert entry["status"] != "candidate"
    assert "no-priced-rows" in codes(entry["rationale"])

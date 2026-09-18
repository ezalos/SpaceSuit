# ABOUTME: Tests for the goal/evidence reporter: input validation, unknown-vs-zero, Decimal
# ABOUTME: aggregation of ONE upstream cost snapshot, privacy redaction and atomic writes.

import hashlib
import json
import os
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from goals import report as R

NOW = "2026-09-17T12:00:00Z"


# --- fixtures: real dicts shaped exactly like the two real inputs ----------------------

def row(key, **over):
    base = {
        "key": key, "turns": 10, "in": 1000, "out": 200, "cacheRead": 50,
        "cw5m": 10, "cw1h": 0, "cwUnknownTtl": 0, "cwConflict": 0,
        "withheldTurns": 0, "usd": Decimal("1.25"), "partial": False,
        "partialReasons": [], "tieredTurns": 0, "models": ["model-a"],
    }
    base.update(over)
    return base


MEANING = ("selected sources contain this many shared message ids whose deterministic global "
           "source allocation is outside the selected scope")


def coverage(complete=True, count=0, **over):
    base = {"complete": complete, "allocatedElsewhereIds": count, "meaning": MEANING}
    base.update(over)
    return base


def cost(**over):
    base = {
        "at": "2026-09-17T11:30:00Z",
        "group": "session",
        "unpriced": [],
        "lineageFrom": "path-nesting (not metadata-verified)",
        "engine": {"costCacheVersion": 6, "timePrecision": "utc-day", "bucketEdges": [200000]},
        "prices": {
            "models": 120,
            "sources": {"shipped": {"at": "2026-09-01T00:00:00Z", "from": "prices.default.json", "count": 120}},
            "sha256": "abc123def456abcd",
        },
        "scope": {
            "roots": ["root-a"], "resolvedRoots": ["root-a"], "unknownRoots": [],
            "repo": None, "repoSemantics": None, "since": None, "until": None,
            "allocationCoverage": coverage(),
        },
        "rows": [row("root-a")],
        "sessions": {"root-a": {"kind": "root", "parent": None, "cwd": "/w/x", "cwds": ["/w/x"],
                                "first": "2026-09-01T00:00:00Z", "last": "2026-09-02T00:00:00Z",
                                "file": "/w/x/root-a.jsonl", "files": ["/w/x/root-a.jsonl"]}},
    }
    base.update(over)
    return base


def goal(**over):
    base = {
        "schema_version": 1,
        "goal_id": "g-1",
        "title": "A bounded goal",
        "timezone": "UTC",
        "authorized_utc": "2026-09-01T00:00:00Z",
        "scope": {"roots": ["root-a"], "mode": "whole-session-roots",
                  "since": None, "until": None, "ownership": "dedicated"},
        "outcome": {"status": "in_progress", "implementer": "worker-1", "verifier": None,
                    "accepted_utc": None, "evidence": [], "blocker": None},
        "worker_intervals": [],
        "interventions": [],
        "policy": {"monthly_llm_cap_usd": "1000.00", "pilot_incremental_cap_usd": "50.00",
                   "critical_reserve_usd": "100.00", "required_checkpoints_per_day": 2,
                   "required_minutes_per_day": 20},
        # The private key the scope handles are derived under. It never leaves the manifest.
        "scope_fingerprint_salt": "g1-synthetic-scope-salt-0123456789abcdef",
        "synthetic": True,
    }
    base.update(over)
    return base


def codes(rep):
    return {lim["code"] for lim in rep["limitations"]}


# --- the repaired backend contract, and the one it replaced ---------------------------

def test_the_repaired_backend_snapshot_is_accepted():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert rep["source"]["engine_cache_version"] == 6
    assert rep["source"]["time_precision"] == "utc-day"


def test_an_old_cache_v4_snapshot_is_refused_not_silently_accepted():
    snap = cost()
    snap["engine"] = {"costCacheVersion": 4, "timePrecision": "utc-day"}
    with pytest.raises(R.ReportError, match="costCacheVersion"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_a_snapshot_grouped_by_model_is_refused():
    with pytest.raises(R.ReportError, match="group"):
        R.build_report(goal(), cost(group="model"), now_utc=NOW)


def test_a_missing_required_backend_field_is_a_structural_error():
    snap = cost()
    del snap["lineageFrom"]
    with pytest.raises(R.ReportError, match="lineageFrom"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_unknown_extra_backend_fields_are_ignored_once_required_fields_validate():
    snap = cost(effectiveRates={"model-a": {"input": 3}}, somethingNew=True)
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["allocated_api_equivalent"]["known_subtotal_usd"] == "1.25"


# --- the scope must be the one that was asked for -------------------------------------

def test_a_snapshot_for_a_different_root_set_is_refused():
    snap = cost()
    snap["scope"]["roots"] = ["root-b"]
    snap["scope"]["resolvedRoots"] = ["root-b"]
    snap["rows"] = [row("root-b")]
    with pytest.raises(R.ReportError, match="root"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_a_superset_root_snapshot_is_refused():
    snap = cost()
    snap["scope"]["roots"] = ["root-a", "root-b"]
    snap["scope"]["resolvedRoots"] = ["root-a", "root-b"]
    with pytest.raises(R.ReportError, match="root"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_root_order_alone_does_not_matter():
    g = goal(scope={"roots": ["root-b", "root-a"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    snap = cost()
    snap["scope"]["roots"] = ["root-a", "root-b"]
    snap["scope"]["resolvedRoots"] = ["root-a", "root-b"]
    snap["rows"] = [row("root-a"), row("root-b")]
    rep = R.build_report(g, snap, now_utc=NOW)
    assert rep["scope"]["roots_requested"] == 2


def test_a_snapshot_with_different_bounds_is_refused():
    g = goal(scope={"roots": ["root-a"], "mode": "utc-days",
                    "since": "2026-09-01", "until": "2026-09-10", "ownership": "dedicated"})
    snap = cost()
    snap["scope"]["since"] = "2026-09-01"
    snap["scope"]["until"] = "2026-09-11"
    with pytest.raises(R.ReportError, match="until"):
        R.build_report(g, snap, now_utc=NOW)


def test_a_repo_scoped_snapshot_is_refused_for_explicit_root_input():
    snap = cost()
    snap["scope"]["repo"] = "/w/x"
    with pytest.raises(R.ReportError, match="repo"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_sub_day_bounds_are_rejected_and_the_supported_precision_is_echoed():
    g = goal(scope={"roots": ["root-a"], "mode": "utc-days",
                    "since": "2026-09-01T06:00:00Z", "until": None, "ownership": "dedicated"})
    with pytest.raises(R.ReportError, match="utc-day"):
        R.build_report(g, cost(), now_utc=NOW)


def test_duplicate_roots_in_the_manifest_are_rejected():
    g = goal(scope={"roots": ["root-a", "root-a"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    with pytest.raises(R.ReportError, match="duplicate"):
        R.build_report(g, cost(), now_utc=NOW)


def test_an_empty_root_list_is_rejected():
    g = goal(scope={"roots": [], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    with pytest.raises(R.ReportError, match="roots"):
        R.build_report(g, cost(), now_utc=NOW)


# --- unknown is not zero ---------------------------------------------------------------

def test_an_unknown_root_stays_visible_and_makes_coverage_incomplete():
    g = goal(scope={"roots": ["root-a", "root-gone"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    snap = cost()
    snap["scope"]["roots"] = ["root-a", "root-gone"]
    snap["scope"]["resolvedRoots"] = ["root-a"]
    snap["scope"]["unknownRoots"] = ["root-gone"]
    rep = R.build_report(g, snap, now_utc=NOW)
    assert rep["scope"]["roots_unknown"] == 1
    assert rep["scope"]["coverage_complete"] is False
    assert "coverage-incomplete-unknown-roots" in codes(rep)


def test_an_unknown_root_is_never_described_as_costing_zero():
    g = goal(scope={"roots": ["root-a", "root-gone"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    snap = cost()
    snap["scope"]["roots"] = ["root-a", "root-gone"]
    snap["scope"]["resolvedRoots"] = ["root-a"]
    snap["scope"]["unknownRoots"] = ["root-gone"]
    rep = R.build_report(g, snap, now_utc=NOW)
    money = rep["allocated_api_equivalent"]
    assert money["whole_source_scope_covered"] is False
    assert money["known_subtotal_usd"] == "1.25"


def test_complete_coverage_is_reported_when_every_root_resolved():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert rep["scope"]["coverage_complete"] is True
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is True


# --- rows: one aggregation of one upstream result, never a second pricer ---------------

def test_duplicate_row_keys_are_rejected():
    snap = cost(rows=[row("root-a"), row("root-a")])
    with pytest.raises(R.ReportError, match="duplicate"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_rows_are_summed_once_with_decimal_precision():
    snap = cost(rows=[row("root-a", usd=Decimal("0.1")), row("child-1", usd=Decimal("0.2"))])
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["allocated_api_equivalent"]["known_subtotal_usd"] == "0.3"
    assert rep["allocated_api_equivalent"]["display_usd"] == "0.30"


def test_money_comes_only_from_the_upstream_usd_never_from_local_rates():
    huge = row("root-a", **{"in": 10_000_000, "out": 2_000_000, "usd": Decimal("0")})
    rep = R.build_report(goal(), cost(rows=[huge]), now_utc=NOW)
    assert rep["allocated_api_equivalent"]["known_subtotal_usd"] == "0"
    assert rep["usage"]["input_tokens"] == 10_000_000


def test_a_binary_float_usd_is_refused_so_precision_cannot_be_lost_silently():
    snap = cost(rows=[row("root-a", usd=1.25)])
    with pytest.raises(R.ReportError, match="Decimal"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_load_report_parses_json_money_as_decimal(tmp_path):
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text(json.dumps(goal()))
    snap = cost(rows=[{**row("root-a"), "usd": 0.1}, {**row("child-1"), "usd": 0.2}])
    cp.write_text(json.dumps(snap, default=str))
    rep = R.load_report(gp, cp, now_utc=NOW)
    assert rep["allocated_api_equivalent"]["known_subtotal_usd"] == "0.3"


# --- counters: finite, integer, non-negative -------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("turns", -1), ("in", -5), ("out", -1), ("cacheRead", -1),
    ("cw5m", -1), ("cwConflict", -1), ("withheldTurns", -1), ("tieredTurns", -1),
])
def test_negative_counters_are_rejected(field, value):
    with pytest.raises(R.ReportError, match=field):
        R.build_report(goal(), cost(rows=[row("root-a", **{field: value})]), now_utc=NOW)


def test_a_fractional_counter_is_rejected():
    with pytest.raises(R.ReportError, match="turns"):
        R.build_report(goal(), cost(rows=[row("root-a", turns=1.5)]), now_utc=NOW)


def test_a_boolean_is_not_accepted_as_a_count():
    with pytest.raises(R.ReportError, match="turns"):
        R.build_report(goal(), cost(rows=[row("root-a", turns=True)]), now_utc=NOW)


def test_a_nonfinite_money_value_is_rejected():
    with pytest.raises(R.ReportError, match="usd"):
        R.build_report(goal(), cost(rows=[row("root-a", usd=Decimal("NaN"))]), now_utc=NOW)


def test_malformed_money_text_is_rejected():
    with pytest.raises(R.ReportError, match="usd"):
        R.build_report(goal(), cost(rows=[row("root-a", usd="not-a-number")]), now_utc=NOW)


def test_a_negative_subtotal_is_rejected():
    with pytest.raises(R.ReportError, match="usd"):
        R.build_report(goal(), cost(rows=[row("root-a", usd=Decimal("-1"))]), now_utc=NOW)


def test_an_invalid_snapshot_timestamp_is_rejected():
    with pytest.raises(R.ReportError, match="at"):
        R.build_report(goal(), cost(at="yesterday"), now_utc=NOW)


# --- incompleteness: the three causes stay distinct ------------------------------------

def test_a_missing_rate_marks_the_subtotal_incomplete_with_its_own_cause():
    snap = cost(rows=[row("root-a", partial=True, partialReasons=["rate-missing"])],
                unpriced=["claude-unknown-gateway-id"])
    rep = R.build_report(goal(), snap, now_utc=NOW)
    money = rep["allocated_api_equivalent"]
    assert money["pricing_complete"] is False
    assert money["partial_reasons"] == ["rate-missing"]
    assert money["unpriced_model_count"] == 1
    assert "pricing-incomplete" in codes(rep)


def test_an_unknown_ttl_cause_is_retained_separately():
    snap = cost(rows=[row("root-a", cwUnknownTtl=1000, partial=True, partialReasons=["unknown-ttl"])])
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["allocated_api_equivalent"]["partial_reasons"] == ["unknown-ttl"]
    assert rep["usage"]["cache_write_unknown_ttl"] == 1000


def test_a_count_conflict_makes_normalized_token_totals_unknown():
    snap = cost(rows=[row("root-a", cwConflict=1_000_000, withheldTurns=1,
                          partial=True, partialReasons=["count-conflict"], usd=Decimal("0"))])
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["usage"]["normalized_total_known"] is False
    assert rep["usage"]["withheld_turns"] == 1
    assert rep["allocated_api_equivalent"]["partial_reasons"] == ["count-conflict"]
    assert "token-totals-unknown-conflict" in codes(rep)


def test_all_three_causes_survive_together_without_collapsing():
    snap = cost(rows=[
        row("root-a", partial=True, partialReasons=["rate-missing"]),
        row("child-1", partial=True, partialReasons=["unknown-ttl"], cwUnknownTtl=5),
        row("child-2", partial=True, partialReasons=["count-conflict"], cwConflict=7, withheldTurns=2),
    ])
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["allocated_api_equivalent"]["partial_reasons"] == [
        "count-conflict", "rate-missing", "unknown-ttl"]
    assert rep["allocated_api_equivalent"]["rows_partial"] == 3


def test_an_unrecognised_partial_reason_is_rejected():
    snap = cost(rows=[row("root-a", partial=True, partialReasons=["mystery"])])
    with pytest.raises(R.ReportError, match="partialReasons"):
        R.build_report(goal(), snap, now_utc=NOW)


# --- reasoning tokens are a subset of output, never added again ------------------------

def test_reasoning_tokens_are_reported_as_a_subset_and_not_added_to_output():
    snap = cost(rows=[row("root-a", out=200, think=150)])
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["usage"]["output_tokens"] == 200
    assert rep["usage"]["reasoning_tokens"]["value"] == 150
    assert rep["usage"]["reasoning_tokens"]["subset_of"] == "output_tokens"


def test_reasoning_tokens_are_unknown_when_the_backend_omits_them():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert rep["usage"]["reasoning_tokens"]["known"] is False
    assert rep["usage"]["reasoning_tokens"]["value"] is None


def test_reasoning_tokens_larger_than_output_are_rejected():
    snap = cost(rows=[row("root-a", out=10, think=11)])
    with pytest.raises(R.ReportError, match="think"):
        R.build_report(goal(), snap, now_utc=NOW)


# --- complete pricing is not proof of the producing goal, and never cash ---------------

def test_complete_pricing_is_still_neither_cash_nor_producer_proof():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    money = rep["allocated_api_equivalent"]
    assert money["pricing_complete"] is True
    assert money["label"] == "allocated API-equivalent"
    assert money["is_cash"] is False
    assert {"allocated-not-cash", "source-allocation-not-attribution", "lineage-path-derived"} <= codes(rep)


def test_a_shared_scope_is_explicitly_unqualified():
    g = goal(scope={"roots": ["root-a"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "shared"})
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert rep["scope"]["ownership"] == "shared"
    assert "shared-scope-unqualified" in codes(rep)
    assert rep["allocated_api_equivalent"]["attributable_to_this_goal"] is False


def test_unknown_ownership_also_warns_rather_than_allocating():
    g = goal(scope={"roots": ["root-a"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "unknown"})
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert "ownership-unknown" in codes(rep)


def test_budget_shows_the_caps_and_keeps_cash_unknown():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    b = rep["budget"]
    assert b["monthly_llm_cap_usd"] == "1000.00"
    assert b["pilot_incremental_cap_usd"] == "50.00"
    assert b["critical_reserve_usd"] == "100.00"
    assert b["cash_spend_usd"] is None and b["cash_headroom_usd"] is None
    assert b["cash_status"] == "UNKNOWN - not connected"


# --- accepted work vs claimed work -----------------------------------------------------

def test_acceptance_requires_a_verifier_distinct_from_the_implementer():
    g = goal(outcome={"status": "accepted", "implementer": "worker-1", "verifier": "worker-1",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    with pytest.raises(R.ReportError, match="verifier"):
        R.build_report(g, cost(), now_utc=NOW)


def test_acceptance_requires_at_least_one_evidence_reference():
    g = goal(outcome={"status": "accepted", "implementer": "worker-1", "verifier": "review-1",
                      "accepted_utc": "2026-09-05T00:00:00Z", "evidence": [], "blocker": None})
    with pytest.raises(R.ReportError, match="evidence"):
        R.build_report(g, cost(), now_utc=NOW)


def test_in_progress_work_is_not_reported_as_accepted_however_much_evidence_it_cites():
    g = goal(outcome={"status": "in_progress", "implementer": "worker-1", "verifier": None,
                      "accepted_utc": None,
                      "evidence": [{"label": "partial log", "ref": "runs/partial.txt"}], "blocker": None})
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert rep["outcome"]["status"] == "in_progress"
    assert rep["outcome"]["accepted"] is False
    assert rep["outcome"]["evidence_count"] == 1
    assert "acceptance-is-attestation" in codes(rep)


def test_accepted_work_cites_its_evidence_and_stays_an_attestation():
    g = goal(outcome={"status": "accepted", "implementer": "worker-1", "verifier": "review-1",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert rep["outcome"]["accepted"] is True
    assert rep["outcome"]["evidence"] == [{"label": "suite", "ref": "runs/green.txt", "ref_kind": "relative-path"}]
    assert rep["outcome"]["reverified_by_this_report"] is False


def test_a_blocker_is_carried_verbatim_for_a_blocked_goal():
    g = goal(outcome={"status": "blocked", "implementer": "worker-1", "verifier": None,
                      "accepted_utc": None, "evidence": [], "blocker": "waiting on a decision"})
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert rep["outcome"]["blocker"] == "waiting on a decision"


def test_an_unknown_status_is_rejected():
    g = goal(outcome={"status": "nearly", "implementer": None, "verifier": None,
                      "accepted_utc": None, "evidence": [], "blocker": None})
    with pytest.raises(R.ReportError, match="status"):
        R.build_report(g, cost(), now_utc=NOW)


# --- time: known, unknown, and never invented ------------------------------------------

def test_time_to_acceptance_is_the_difference_when_both_ends_are_known():
    g = goal(authorized_utc="2026-09-01T00:00:00Z",
             outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-03T06:00:00Z",
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    t = R.build_report(g, cost(), now_utc=NOW)["time"]["time_to_acceptance"]
    assert t["known"] is True
    assert t["seconds"] == 2 * 86400 + 6 * 3600


def test_time_to_acceptance_is_unknown_when_acceptance_time_is_missing():
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": None,
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    rep = R.build_report(g, cost(), now_utc=NOW)
    t = rep["time"]["time_to_acceptance"]
    assert t["known"] is False and t["seconds"] is None
    assert t["reason"] == "accepted_utc unknown"
    assert "acceptance-time-unknown" in codes(rep)


def test_time_to_acceptance_is_unknown_when_authorization_is_unknown():
    g = goal(authorized_utc=None,
             outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-03T00:00:00Z",
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert rep["time"]["time_to_acceptance"]["known"] is False
    assert "authorization-unknown" in codes(rep)


def test_an_acceptance_before_authorization_is_flagged_not_computed():
    g = goal(authorized_utc="2026-09-10T00:00:00Z",
             outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-03T00:00:00Z",
                      "evidence": [{"label": "suite", "ref": "runs/green.txt"}], "blocker": None})
    rep = R.build_report(g, cost(), now_utc=NOW)
    t = rep["time"]["time_to_acceptance"]
    assert t["known"] is False and t["seconds"] is None
    assert "time-to-acceptance-unordered" in codes(rep)


def test_a_naive_timestamp_is_rejected():
    with pytest.raises(R.ReportError, match="authorized_utc"):
        R.build_report(goal(authorized_utc="2026-09-01 00:00:00"), cost(), now_utc=NOW)


def test_a_non_utc_offset_is_rejected():
    with pytest.raises(R.ReportError, match="authorized_utc"):
        R.build_report(goal(authorized_utc="2026-09-01T00:00:00+02:00"), cost(), now_utc=NOW)


def test_elapsed_time_for_open_work_uses_the_supplied_now():
    rep = R.build_report(goal(authorized_utc="2026-09-16T12:00:00Z"), cost(), now_utc=NOW)
    e = rep["time"]["elapsed_open"]
    assert e["known"] is True and e["seconds"] == 86400


def test_elapsed_time_is_not_applicable_once_the_goal_is_terminal():
    g = goal(outcome={"status": "abandoned", "implementer": "w", "verifier": None,
                      "accepted_utc": None, "evidence": [], "blocker": None})
    assert R.build_report(g, cost(), now_utc=NOW)["time"]["elapsed_open"]["applicable"] is False


def test_elapsed_time_is_unknown_without_a_known_authorization():
    rep = R.build_report(goal(authorized_utc=None), cost(), now_utc=NOW)
    assert rep["time"]["elapsed_open"]["known"] is False


def test_a_now_before_authorization_is_flagged_rather_than_negative():
    rep = R.build_report(goal(authorized_utc="2026-09-20T00:00:00Z"), cost(), now_utc=NOW)
    e = rep["time"]["elapsed_open"]
    assert e["known"] is False and e["seconds"] is None
    assert e["reason"] == "now_utc precedes authorized_utc"


# --- worker time is aggregate, never wall time -----------------------------------------

def test_overlapping_worker_time_can_exceed_elapsed_and_is_never_called_wall_time():
    g = goal(authorized_utc="2026-09-17T00:00:00Z", worker_intervals=[
        {"actor": "w-1", "start_utc": "2026-09-17T00:00:00Z", "end_utc": "2026-09-17T10:00:00Z"},
        {"actor": "w-2", "start_utc": "2026-09-17T01:00:00Z", "end_utc": "2026-09-17T11:00:00Z"},
    ])
    rep = R.build_report(g, cost(), now_utc=NOW)
    w = rep["time"]["worker_time"]
    assert w["aggregate_known_seconds"] == 20 * 3600
    assert w["overlapping"] is True
    assert w["is_wall_time"] is False
    assert w["aggregate_known_seconds"] > rep["time"]["elapsed_open"]["seconds"]
    assert "worker-intervals-overlap" in codes(rep)


def test_non_overlapping_intervals_are_not_flagged_as_overlapping():
    g = goal(worker_intervals=[
        {"actor": "w-1", "start_utc": "2026-09-17T00:00:00Z", "end_utc": "2026-09-17T01:00:00Z"},
        {"actor": "w-1", "start_utc": "2026-09-17T02:00:00Z", "end_utc": "2026-09-17T03:00:00Z"},
    ])
    w = R.build_report(g, cost(), now_utc=NOW)["time"]["worker_time"]
    assert w["overlapping"] is False
    assert w["aggregate_known_seconds"] == 2 * 3600


def test_an_open_interval_is_counted_as_unknown_not_closed_at_now():
    g = goal(worker_intervals=[
        {"actor": "w-1", "start_utc": "2026-09-17T00:00:00Z", "end_utc": "2026-09-17T01:00:00Z"},
        {"actor": "w-2", "start_utc": "2026-09-17T02:00:00Z", "end_utc": None},
    ])
    rep = R.build_report(g, cost(), now_utc=NOW)
    w = rep["time"]["worker_time"]
    assert w["aggregate_known_seconds"] == 3600
    assert w["open_interval_count"] == 1
    assert "worker-intervals-open" in codes(rep)


def test_a_reversed_worker_interval_is_rejected():
    g = goal(worker_intervals=[{"actor": "w-1", "start_utc": "2026-09-17T05:00:00Z",
                                "end_utc": "2026-09-17T04:00:00Z"}])
    with pytest.raises(R.ReportError, match="end_utc"):
        R.build_report(g, cost(), now_utc=NOW)


# --- attention: local days, required vs optional, unknown minutes ----------------------

def test_interventions_bucket_by_the_manifest_timezone_not_by_utc():
    g = goal(timezone="America/Los_Angeles", interventions=[
        {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": 5},
        {"id": "i-2", "at_utc": "2026-09-17T18:00:00Z", "required": True, "minutes": 5},
    ])
    days = {d["date"]: d for d in R.build_report(g, cost(), now_utc=NOW)["attention"]["days"]}
    assert set(days) == {"2026-09-16", "2026-09-17"}
    assert days["2026-09-16"]["required_count"] == 1


def test_optional_participation_is_counted_separately_from_required():
    g = goal(interventions=[
        {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": 5},
        {"id": "i-2", "at_utc": "2026-09-17T03:00:00Z", "required": False, "minutes": 40},
    ])
    day = R.build_report(g, cost(), now_utc=NOW)["attention"]["days"][0]
    assert day["required_count"] == 1 and day["optional_count"] == 1
    assert day["required_minutes_known"] == 5
    assert day["optional_minutes_known"] == 40


def test_unknown_minutes_stay_unknown_and_are_never_inferred():
    g = goal(interventions=[
        {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": None},
    ])
    rep = R.build_report(g, cost(), now_utc=NOW)
    day = rep["attention"]["days"][0]
    assert day["required_minutes_known"] == 0
    assert day["required_minutes_unknown_count"] == 1
    assert day["required_minutes_exceeds_cap_alone"] is None
    assert "required-minutes-unknown" in codes(rep)


def test_a_known_exceedance_of_the_daily_minutes_is_visible():
    g = goal(interventions=[
        {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": 45},
    ])
    day = R.build_report(g, cost(), now_utc=NOW)["attention"]["days"][0]
    assert day["required_minutes_known"] == 45
    assert day["required_minutes_exceeds_cap_alone"] is True


def test_too_many_required_checkpoints_in_one_local_day_is_visible():
    g = goal(interventions=[
        {"id": f"i-{n}", "at_utc": f"2026-09-17T0{n}:00:00Z", "required": True, "minutes": 1}
        for n in range(1, 5)
    ])
    day = R.build_report(g, cost(), now_utc=NOW)["attention"]["days"][0]
    assert day["required_count"] == 4
    assert day["required_checkpoints_exceeds_cap_alone"] is True


def test_the_attention_section_says_it_is_one_goals_contribution():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert "single-goal-contribution" in codes(rep)
    assert rep["attention"]["whole_pilot_compliance"] is None


def test_identical_duplicate_interventions_dedupe():
    entry = {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": 5}
    g = goal(interventions=[entry, dict(entry)])
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert rep["attention"]["intervention_count"] == 1
    assert rep["attention"]["deduped_count"] == 1


def test_conflicting_duplicate_intervention_ids_are_rejected():
    g = goal(interventions=[
        {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": 5},
        {"id": "i-1", "at_utc": "2026-09-17T02:00:00Z", "required": True, "minutes": 9},
    ])
    with pytest.raises(R.ReportError, match="i-1"):
        R.build_report(g, cost(), now_utc=NOW)


def test_negative_intervention_minutes_are_rejected():
    g = goal(interventions=[{"id": "i-1", "at_utc": "2026-09-17T02:00:00Z",
                             "required": True, "minutes": -1}])
    with pytest.raises(R.ReportError, match="minutes"):
        R.build_report(g, cost(), now_utc=NOW)


def test_an_unknown_timezone_is_rejected():
    with pytest.raises(R.ReportError, match="timezone"):
        R.build_report(goal(timezone="Mars/Olympus"), cost(), now_utc=NOW)


# --- privacy: raw inputs stay private ---------------------------------------------------

PRIVATE = [
    "/home/someone/private/repo", "root-a.jsonl", "/home/someone/.claude/prices.override.json",
    "claude-secret-internal-gateway", "/home/someone/private/proof.txt",
]


def private_inputs():
    snap = cost(unpriced=["claude-secret-internal-gateway"])
    snap["sessions"] = {"root-a": {"kind": "root", "parent": None, "cwd": "/home/someone/private/repo",
                                   "cwds": ["/home/someone/private/repo"], "first": None, "last": None,
                                   "file": "/home/someone/private/repo/root-a.jsonl",
                                   "files": ["/home/someone/private/repo/root-a.jsonl"]}}
    snap["prices"]["sources"] = {
        "shipped": {"at": "2026-09-01T00:00:00Z", "from": "prices.default.json", "count": 120},
        "override": {"at": "2026-09-10T00:00:00Z",
                     "from": "/home/someone/.claude/prices.override.json", "count": 3},
    }
    snap["rows"] = [row("root-a", partial=True, partialReasons=["rate-missing"],
                        models=["claude-secret-internal-gateway"])]
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "local proof", "ref": "/home/someone/private/proof.txt"}],
                      "blocker": None})
    return g, snap


def test_no_private_string_from_either_input_reaches_the_report():
    rep = R.build_report(*private_inputs(), now_utc=NOW)
    blob = json.dumps(rep)
    for secret in PRIVATE:
        assert secret not in blob, secret


def test_the_sessions_map_is_never_exported():
    rep = R.build_report(*private_inputs(), now_utc=NOW)
    assert "sessions" not in json.dumps(rep)


def test_price_sources_keep_labels_dates_and_counts_but_drop_paths():
    rep = R.build_report(*private_inputs(), now_utc=NOW)
    sources = rep["source"]["prices_sources"]
    assert {s["label"] for s in sources} == {"shipped", "override"}
    assert [s for s in sources if s["label"] == "override"][0]["model_count"] == 3
    assert all("from" not in s and "path" not in s for s in sources)
    assert rep["source"]["prices_sha256"] == "abc123def456abcd"


def test_a_price_source_dated_with_a_plain_day_is_accepted():
    # The shipped price table dates itself `2026-09-17`, not a timestamp. Refusing that would
    # refuse every real snapshot the engine emits.
    snap = cost()
    snap["prices"]["sources"] = {"shipped": {"at": "2026-09-17", "from": "x", "count": 65}}
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["source"]["prices_sources"] == [{"label": "shipped", "at": "2026-09-17", "model_count": 65}]


def test_a_price_source_dated_with_a_timestamp_is_reduced_to_its_day():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert rep["source"]["prices_sources"][0]["at"] == "2026-09-01"


def test_a_price_source_with_no_date_stays_unknown_rather_than_guessed():
    snap = cost()
    snap["prices"]["sources"] = {"shipped": {"from": "x", "count": 65}}
    rep = R.build_report(goal(), snap, now_utc=NOW)
    assert rep["source"]["prices_sources"][0]["at"] is None


def test_an_unparseable_price_source_date_is_rejected():
    snap = cost()
    snap["prices"]["sources"] = {"shipped": {"at": "last tuesday", "count": 1}}
    with pytest.raises(R.ReportError, match="price source"):
        R.build_report(goal(), snap, now_utc=NOW)


def test_unknown_model_identifiers_are_counted_not_named():
    rep = R.build_report(*private_inputs(), now_utc=NOW)
    assert rep["allocated_api_equivalent"]["unpriced_model_count"] == 1
    assert rep["usage"]["distinct_model_count"] == 1
    assert "models" not in rep["usage"]


def test_an_absolute_evidence_path_is_withheld_but_its_label_survives():
    rep = R.build_report(*private_inputs(), now_utc=NOW)
    ev = rep["outcome"]["evidence"][0]
    assert ev["label"] == "local proof"
    assert ev["ref"] is None and ev["ref_kind"] == "absolute-path"
    assert "evidence-ref-withheld" in codes(rep)


def test_an_https_evidence_reference_is_kept():
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "run", "ref": "https://example.org/run/1"}],
                      "blocker": None})
    ev = R.build_report(g, cost(), now_utc=NOW)["outcome"]["evidence"][0]
    assert ev["ref"] == "https://example.org/run/1" and ev["ref_kind"] == "https"


@pytest.mark.parametrize("ref", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd",
                                 "http://example.org/x"])
def test_an_unapproved_evidence_scheme_is_withheld(ref):
    g = goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                      "accepted_utc": "2026-09-05T00:00:00Z",
                      "evidence": [{"label": "x", "ref": ref}], "blocker": None})
    ev = R.build_report(g, cost(), now_utc=NOW)["outcome"]["evidence"][0]
    assert ev["ref"] is None
    assert ref not in json.dumps(R.build_report(g, cost(), now_utc=NOW))


def test_the_report_fingerprints_its_input_snapshot():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    other = R.build_report(goal(), cost(rows=[row("root-a", usd=Decimal("9"))]), now_utc=NOW)
    h = rep["source"]["snapshot_canonical_sha256"]
    assert len(h) == 64 and h != other["source"]["snapshot_canonical_sha256"]


# --- I3: the raw fingerprint is of the FILE, or it is absent -----------------------------

def test_load_report_fingerprints_the_exact_bytes_of_the_cost_file(tmp_path):
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text(json.dumps(goal()))
    cp.write_text(json.dumps(cost(), default=str))
    rep = R.load_report(gp, cp, now_utc=NOW)
    assert rep["source"]["snapshot_sha256"] == hashlib.sha256(cp.read_bytes()).hexdigest()


def test_two_whitespace_different_files_share_a_canonical_digest_but_not_a_raw_one(tmp_path):
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps(goal()))
    compact, spaced = tmp_path / "c1.json", tmp_path / "c2.json"
    compact.write_text(json.dumps(cost(), default=str, separators=(",", ":")))
    spaced.write_text(json.dumps(cost(), default=str, indent=4) + "\n")
    one = R.load_report(gp, compact, now_utc=NOW)["source"]
    two = R.load_report(gp, spaced, now_utc=NOW)["source"]
    assert one["snapshot_sha256"] != two["snapshot_sha256"]
    assert one["snapshot_canonical_sha256"] == two["snapshot_canonical_sha256"]


def test_build_report_from_a_dict_has_no_raw_file_digest_and_says_so():
    source = R.build_report(goal(), cost(), now_utc=NOW)["source"]
    assert source["snapshot_sha256"] is None
    assert "not a file" in source["snapshot_sha256_note"]


def test_the_canonical_digest_states_the_recipe_that_produces_it():
    source = R.build_report(goal(), cost(), now_utc=NOW)["source"]
    assert "json.dumps" in source["snapshot_canonical_recipe"]
    assert "sort_keys=True" in source["snapshot_canonical_recipe"]


def test_the_raw_digest_is_labelled_as_the_file_digest(tmp_path):
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text(json.dumps(goal()))
    cp.write_text(json.dumps(cost(), default=str))
    source = R.load_report(gp, cp, now_utc=NOW)["source"]
    assert "sha256sum" in source["snapshot_sha256_note"]


# --- I2: resolved and unknown roots must partition the requested set ---------------------

def partitioned(requested, resolved, unknown):
    g = goal(scope={"roots": requested, "mode": "whole-session-roots", "since": None,
                    "until": None, "ownership": "dedicated"})
    snap = cost(rows=[row(name) for name in dict.fromkeys(resolved)])
    snap["scope"]["roots"] = list(requested)
    snap["scope"]["resolvedRoots"] = resolved
    snap["scope"]["unknownRoots"] = unknown
    return g, snap


def test_a_requested_root_in_neither_list_is_rejected():
    with pytest.raises(R.ReportError, match="resolvedRoots"):
        R.build_report(*partitioned(["root-a", "root-b"], ["root-a"], []), now_utc=NOW)


def test_a_resolved_root_that_was_never_requested_is_rejected():
    with pytest.raises(R.ReportError, match="resolvedRoots"):
        R.build_report(*partitioned(["root-a"], ["root-a", "root-b"], []), now_utc=NOW)


def test_an_unknown_root_that_was_never_requested_is_rejected():
    with pytest.raises(R.ReportError, match="unknownRoots"):
        R.build_report(*partitioned(["root-a"], ["root-a"], ["root-ghost"]), now_utc=NOW)


def test_a_root_in_both_lists_at_once_is_rejected():
    with pytest.raises(R.ReportError, match="both"):
        R.build_report(*partitioned(["root-a"], ["root-a"], ["root-a"]), now_utc=NOW)


def test_duplicate_resolved_roots_are_rejected():
    with pytest.raises(R.ReportError, match="resolvedRoots"):
        R.build_report(*partitioned(["root-a"], ["root-a", "root-a"], []), now_utc=NOW)


def test_duplicate_unknown_roots_are_rejected():
    g, snap = partitioned(["root-a", "root-b"], ["root-a"], ["root-b", "root-b"])
    with pytest.raises(R.ReportError, match="unknownRoots"):
        R.build_report(g, snap, now_utc=NOW)


def test_a_legitimate_partition_with_an_unknown_root_still_reports_incomplete_coverage():
    rep = R.build_report(*partitioned(["root-a", "root-b"], ["root-a"], ["root-b"]), now_utc=NOW)
    assert rep["scope"]["roots_requested"] == 2
    assert rep["scope"]["roots_resolved"] == 1
    assert rep["scope"]["roots_unknown"] == 1
    assert rep["scope"]["coverage_complete"] is False
    assert "coverage-incomplete-unknown-roots" in codes(rep)


def test_a_fully_unknown_root_set_partitions_and_prices_nothing():
    rep = R.build_report(*partitioned(["root-a"], [], ["root-a"]), now_utc=NOW)
    assert rep["scope"]["coverage_complete"] is False
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is False


# --- I4: overlap is a tri-state, and an open interval never silently means "no" -----------

def intervals(*spans):
    return goal(worker_intervals=[{"actor": f"w-{n}", "start_utc": start, "end_utc": end}
                                  for n, (start, end) in enumerate(spans)])


def overlap(g):
    return R.build_report(g, cost(), now_utc=NOW)["time"]["worker_time"]


def test_an_open_interval_starting_inside_a_closed_one_proves_overlap():
    w = overlap(intervals(("2026-09-17T00:00:00Z", "2026-09-17T10:00:00Z"),
                          ("2026-09-17T01:00:00Z", None)))
    assert w["overlapping"] is True
    assert w["aggregate_known_seconds"] == 10 * 3600  # the open one still contributes nothing


def test_an_open_interval_starting_before_a_closed_one_leaves_overlap_unknown():
    w = overlap(intervals(("2026-09-17T05:00:00Z", "2026-09-17T10:00:00Z"),
                          ("2026-09-17T01:00:00Z", None)))
    assert w["overlapping"] is None
    assert "no end" in w["overlap_reason"]


def test_an_open_interval_starting_after_a_closed_one_does_not_overlap_it():
    w = overlap(intervals(("2026-09-17T00:00:00Z", "2026-09-17T02:00:00Z"),
                          ("2026-09-17T05:00:00Z", None)))
    assert w["overlapping"] is False


def test_two_open_intervals_sharing_a_start_prove_overlap():
    w = overlap(intervals(("2026-09-17T03:00:00Z", None), ("2026-09-17T03:00:00Z", None)))
    assert w["overlapping"] is True


def test_two_open_intervals_with_different_starts_leave_overlap_unknown():
    w = overlap(intervals(("2026-09-17T03:00:00Z", None), ("2026-09-17T04:00:00Z", None)))
    assert w["overlapping"] is None


def test_adjacent_closed_intervals_do_not_overlap():
    w = overlap(intervals(("2026-09-17T00:00:00Z", "2026-09-17T01:00:00Z"),
                          ("2026-09-17T01:00:00Z", "2026-09-17T02:00:00Z")))
    assert w["overlapping"] is False


def test_a_zero_length_interval_does_not_prove_overlap():
    w = overlap(intervals(("2026-09-17T01:00:00Z", "2026-09-17T01:00:00Z"),
                          ("2026-09-17T01:00:00Z", "2026-09-17T02:00:00Z")))
    assert w["overlapping"] is False


def test_an_open_interval_starting_where_a_closed_one_ends_does_not_overlap_it():
    w = overlap(intervals(("2026-09-17T00:00:00Z", "2026-09-17T01:00:00Z"),
                          ("2026-09-17T01:00:00Z", None)))
    assert w["overlapping"] is False


def test_disjoint_closed_intervals_do_not_overlap():
    w = overlap(intervals(("2026-09-17T00:00:00Z", "2026-09-17T01:00:00Z"),
                          ("2026-09-17T05:00:00Z", "2026-09-17T06:00:00Z")))
    assert w["overlapping"] is False
    assert w["overlap_reason"] is None


def test_an_unknown_overlap_carries_its_own_limitation_code():
    g = intervals(("2026-09-17T05:00:00Z", "2026-09-17T10:00:00Z"),
                  ("2026-09-17T01:00:00Z", None))
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert "worker-intervals-overlap-unknown" in codes(rep)
    assert "worker-intervals-overlap" not in codes(rep)


def test_a_proven_overlap_carries_the_certain_code_and_not_the_unknown_one():
    g = intervals(("2026-09-17T00:00:00Z", "2026-09-17T10:00:00Z"),
                  ("2026-09-17T01:00:00Z", None))
    rep = R.build_report(g, cost(), now_utc=NOW)
    assert "worker-intervals-overlap" in codes(rep)
    assert "worker-intervals-overlap-unknown" not in codes(rep)


# --- M1: one hardened reference predicate, shared by the JSON and the page ----------------

OBFUSCATED = ["\tjavascript:alert(1)", "java\tscript:alert(1)", " javascript:alert(1)",
              "javascript:alert(1)\n", "java\nscript:alert(1)", "//evil.example/x",
              "../../etc/passwd", "runs/../../etc/passwd", "\x00https://ok.example/x",
              "JaVaScRiPt:alert(1)", "vbscript:x"]


def evidence_goal(ref):
    return goal(outcome={"status": "accepted", "implementer": "w", "verifier": "v",
                         "accepted_utc": "2026-09-05T00:00:00Z",
                         "evidence": [{"label": "x", "ref": ref}], "blocker": None})


@pytest.mark.parametrize("ref", OBFUSCATED)
def test_an_obfuscated_or_traversing_reference_is_never_exported(ref):
    rep = R.build_report(evidence_goal(ref), cost(), now_utc=NOW)
    item = rep["outcome"]["evidence"][0]
    assert item["ref"] is None, item
    assert item["ref_kind"] != "relative-path"
    assert ref.strip() not in json.dumps(rep)
    assert "evidence-ref-withheld" in codes(rep)


@pytest.mark.parametrize("ref", ["runs/green.txt", "evidence/2026-09-17/report.json",
                                 "https://example.org/run/1"])
def test_an_approved_reference_survives_intact(ref):
    item = R.build_report(evidence_goal(ref), cost(), now_utc=NOW)["outcome"]["evidence"][0]
    assert item["ref"] == ref
    assert item["ref_kind"] in ("relative-path", "https")


def test_a_sub_second_snapshot_time_keeps_the_engine_s_own_digits():
    # The engine writes `...:04.177Z`. Re-emitting it as `.177000Z` would pad precision the
    # source never claimed.
    rep = R.build_report(goal(), cost(at="2026-09-17T11:30:04.177Z"), now_utc=NOW)
    assert rep["source"]["snapshot_at"] == "2026-09-17T11:30:04.177Z"


def test_a_whole_second_timestamp_grows_no_fractional_part():
    rep = R.build_report(goal(), cost(at="2026-09-17T11:30:04.000Z"), now_utc=NOW)
    assert rep["source"]["snapshot_at"] == "2026-09-17T11:30:04Z"


def test_the_report_carries_its_provenance_and_the_synthetic_flag():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert rep["generated_utc"] == NOW
    assert rep["source"]["snapshot_at"] == "2026-09-17T11:30:00Z"
    assert rep["goal"]["synthetic"] is True
    assert rep["source"]["lineage"] == "path-nesting-unverified"


# --- writing: atomic, private, never over an input --------------------------------------

def test_write_report_creates_a_private_file_with_valid_json(tmp_path):
    out = tmp_path / "report.json"
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    R.write_report(rep, out)
    assert json.loads(out.read_text())["goal"]["id"] == "g-1"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_write_report_replaces_an_existing_report_atomically(tmp_path):
    out = tmp_path / "report.json"
    out.write_text('{"previous": true}')
    R.write_report(R.build_report(goal(), cost(), now_utc=NOW), out)
    # The whole previous document is gone, checked structurally: a substring search over a report
    # this size matches by accident (`hold-their-own` contains "old").
    written = json.loads(out.read_text())
    assert "previous" not in written
    assert written["kind"] == "goal-report"
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


def test_a_failed_write_leaves_the_previous_report_intact(tmp_path):
    out = tmp_path / "report.json"
    out.write_text('{"old": true}')
    with pytest.raises(R.ReportError):
        R.write_report({"unserializable": {1, 2}}, out)
    assert json.loads(out.read_text()) == {"old": True}
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


def test_a_write_into_an_unwritable_directory_leaves_the_previous_report_intact(tmp_path):
    d = tmp_path / "locked"
    d.mkdir()
    out = d / "report.json"
    out.write_text('{"old": true}')
    os.chmod(d, 0o500)
    try:
        with pytest.raises(OSError):
            R.write_report(R.build_report(goal(), cost(), now_utc=NOW), out)
        assert json.loads(out.read_text()) == {"old": True}
    finally:
        os.chmod(d, 0o700)


def test_load_report_reads_real_files(tmp_path):
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text(json.dumps(goal()))
    cp.write_text(json.dumps(cost(), default=str))
    rep = R.load_report(gp, cp, now_utc=NOW)
    assert rep["goal"]["title"] == "A bounded goal"


def test_load_report_rejects_a_non_object_manifest(tmp_path):
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text("[]")
    cp.write_text(json.dumps(cost(), default=str))
    with pytest.raises(R.ReportError, match="object"):
        R.load_report(gp, cp, now_utc=NOW)


def test_load_report_rejects_broken_json_without_echoing_the_payload(tmp_path):
    gp, cp = tmp_path / "g.json", tmp_path / "c.json"
    gp.write_text('{"secret-token": "s3cr3t", ')
    cp.write_text(json.dumps(cost(), default=str))
    with pytest.raises(R.ReportError) as excinfo:
        R.load_report(gp, cp, now_utc=NOW)
    assert "s3cr3t" not in str(excinfo.value)


def test_an_unsupported_manifest_schema_version_is_refused():
    with pytest.raises(R.ReportError, match="schema_version"):
        R.build_report(goal(schema_version=2), cost(), now_utc=NOW)


# --- upstream allocation coverage: SELECTION coverage, never price partialness ---------------

def with_coverage(value, drop=False):
    snap = cost()
    if drop:
        del snap["scope"]["allocationCoverage"]
    else:
        snap["scope"]["allocationCoverage"] = value
    return snap


def test_complete_allocation_coverage_is_reported_as_known_and_complete():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    allocation = rep["scope"]["allocation_coverage"]
    assert allocation["known"] is True
    assert allocation["complete"] is True
    assert allocation["allocated_elsewhere_ids"] == 0
    assert allocation["description_code"] == "selected-sources-hold-their-own-allocation"


def test_incomplete_allocation_coverage_makes_source_coverage_incomplete():
    rep = R.build_report(goal(), with_coverage(coverage(False, 1)), now_utc=NOW)
    assert rep["scope"]["allocation_coverage"]["complete"] is False
    assert rep["scope"]["allocation_coverage"]["allocated_elsewhere_ids"] == 1
    assert rep["scope"]["coverage_complete"] is False
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is False
    assert "source-allocation-outside-scope" in codes(rep)


def test_incomplete_allocation_coverage_never_touches_the_price_or_the_money():
    complete = R.build_report(goal(), cost(), now_utc=NOW)
    incomplete = R.build_report(goal(), with_coverage(coverage(False, 3)), now_utc=NOW)
    for report in (complete, incomplete):
        assert report["allocated_api_equivalent"]["pricing_complete"] is True
        assert report["allocated_api_equivalent"]["known_subtotal_usd"] == "1.25"
        assert report["allocated_api_equivalent"]["partial_reasons"] == []
    assert incomplete["usage"] == complete["usage"]


def test_a_report_can_be_price_complete_and_source_incomplete_at_once():
    rep = R.build_report(goal(), with_coverage(coverage(False, 2)), now_utc=NOW)
    money = rep["allocated_api_equivalent"]
    assert money["pricing_complete"] is True and money["whole_source_scope_covered"] is False
    assert rep["scope"]["allocation_coverage"]["complete"] is False


def test_an_unknown_root_and_incomplete_allocation_are_two_different_gaps():
    g = goal(scope={"roots": ["root-a", "root-gone"], "mode": "whole-session-roots",
                    "since": None, "until": None, "ownership": "dedicated"})
    snap = with_coverage(coverage(False, 1))
    snap["scope"]["roots"] = ["root-a", "root-gone"]
    snap["scope"]["resolvedRoots"] = ["root-a"]
    snap["scope"]["unknownRoots"] = ["root-gone"]
    rep = R.build_report(g, snap, now_utc=NOW)
    assert {"coverage-incomplete-unknown-roots", "source-allocation-outside-scope"} <= codes(rep)


# --- a legacy snapshot fails closed: unknown, never quietly complete --------------------------

def test_a_legacy_snapshot_without_the_field_reports_coverage_unknown():
    rep = R.build_report(goal(), with_coverage(None, drop=True), now_utc=NOW)
    allocation = rep["scope"]["allocation_coverage"]
    assert allocation["known"] is False
    assert allocation["complete"] is None
    assert allocation["allocated_elsewhere_ids"] is None
    assert allocation["description_code"] == "source-allocation-coverage-unknown"
    assert allocation["count_window"] is None


def test_a_legacy_snapshot_is_never_called_complete_coverage():
    rep = R.build_report(goal(), with_coverage(None, drop=True), now_utc=NOW)
    assert rep["scope"]["coverage_complete"] is False
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is False
    assert "source-allocation-coverage-unknown" in codes(rep)
    assert "source-allocation-outside-scope" not in codes(rep)


def test_a_legacy_snapshot_still_prices_and_renders_for_forensics():
    rep = R.build_report(goal(), with_coverage(None, drop=True), now_utc=NOW)
    assert rep["allocated_api_equivalent"]["pricing_complete"] is True
    assert rep["allocated_api_equivalent"]["known_subtotal_usd"] == "1.25"


# --- malformed coverage is a validation error, never a guess ---------------------------------

@pytest.mark.parametrize("value", [[], "complete", 1, True, None])
def test_a_non_object_allocation_coverage_is_rejected(value):
    with pytest.raises(R.ReportError, match="allocationCoverage"):
        R.build_report(goal(), with_coverage(value), now_utc=NOW)


@pytest.mark.parametrize("bad", [1, 0, "true", None, [], "yes"])
def test_a_non_boolean_complete_flag_is_rejected(bad):
    with pytest.raises(R.ReportError, match="complete"):
        R.build_report(goal(), with_coverage(coverage(complete=bad)), now_utc=NOW)


@pytest.mark.parametrize("bad", [-1, 1.5, "1", True, None, []])
def test_a_non_integer_allocated_elsewhere_count_is_rejected(bad):
    with pytest.raises(R.ReportError, match="allocatedElsewhereIds"):
        R.build_report(goal(), with_coverage(coverage(False, bad)), now_utc=NOW)


def test_complete_coverage_with_a_nonzero_count_is_a_contradiction():
    with pytest.raises(R.ReportError, match="contradict"):
        R.build_report(goal(), with_coverage(coverage(True, 2)), now_utc=NOW)


def test_incomplete_coverage_with_a_zero_count_is_a_contradiction():
    with pytest.raises(R.ReportError, match="contradict"):
        R.build_report(goal(), with_coverage(coverage(False, 0)), now_utc=NOW)


@pytest.mark.parametrize("bad", ["", "   ", None, 5, []])
def test_an_empty_or_non_text_meaning_is_rejected(bad):
    with pytest.raises(R.ReportError, match="meaning"):
        R.build_report(goal(), with_coverage(coverage(meaning=bad)), now_utc=NOW)


# Upstream prose is validated as a PRODUCER-CONTRACT check and then dropped: it never reaches the
# JSON or the page, so a path or an identifier inside it is not a leak - and a cosmetic rewording
# upstream must not throw away an otherwise correct report.

@pytest.mark.parametrize("text", ["ids under /home/someone/private/repo",
                                  "see ~/.claude/projects/x.jsonl",
                                  "ids at https://example.org/leak",
                                  "ids under ../../etc/passwd",
                                  "shared with 0f3ab2c1-11de-4d3e-9a77-aa11bb22cc33",
                                  "owner root-a",
                                  "owner " + "a1b2c3d4" * 8,
                                  "owner louis at develle dot fr",
                                  "an em-dash - a curly quote and 100% of an ellipsis..."])
def test_producer_prose_is_accepted_and_never_exported(text):
    rep = R.build_report(goal(), with_coverage(coverage(meaning=text)), now_utc=NOW)
    assert text not in json.dumps(rep)
    assert "meaning" not in rep["scope"]["allocation_coverage"]


@pytest.mark.parametrize("bad", ["ids\tin\ta tab", "shared ids\nwith a second line",
                                 "null\x00byte"])
def test_control_bearing_producer_prose_is_still_rejected(bad):
    with pytest.raises(R.ReportError, match="meaning"):
        R.build_report(goal(), with_coverage(coverage(meaning=bad)), now_utc=NOW)


def test_the_engines_own_sentence_does_not_survive_into_the_report():
    """G1's limitation text describes the same fact and shares some vocabulary with the engine's
    sentence. What must not appear is the PRODUCER's string itself."""
    rep = R.build_report(goal(), with_coverage(coverage(False, 1)), now_utc=NOW)
    assert MEANING not in json.dumps(rep)


def test_an_over_long_producer_sentence_is_still_rejected():
    with pytest.raises(R.ReportError, match="meaning"):
        R.build_report(goal(), with_coverage(coverage(meaning="x" * 401)), now_utc=NOW)


# --- the coverage object carries counts and prose, never identifiers -------------------------

def test_the_allocation_coverage_object_carries_only_g1_owned_fields():
    rep = R.build_report(goal(), with_coverage(coverage(False, 4)), now_utc=NOW)
    assert set(rep["scope"]["allocation_coverage"]) == {
        "known", "complete", "allocated_elsewhere_ids", "description_code", "count_window"}


def test_the_exported_description_and_window_are_fixed_g1_constants():
    rep = R.build_report(goal(), with_coverage(coverage(False, 1)), now_utc=NOW)
    allocation = rep["scope"]["allocation_coverage"]
    assert allocation["description_code"] == "selected-source-allocation-outside-scope"
    assert allocation["count_window"] == "selected-sources-all-dates"


def test_the_count_window_disclaims_the_day_bounds_it_does_not_follow():
    # The upstream counter ignores --since/--until, so a day-bounded report must not let a reader
    # infer the count was clipped to the window.
    g = goal(scope={"roots": ["root-a"], "mode": "utc-days", "since": "2026-09-01",
                    "until": "2026-09-10", "ownership": "dedicated"})
    snap = with_coverage(coverage(False, 1))
    snap["scope"]["since"], snap["scope"]["until"] = "2026-09-01", "2026-09-10"
    rep = R.build_report(g, snap, now_utc=NOW)
    assert rep["scope"]["allocation_coverage"]["count_window"] == "selected-sources-all-dates"
    text = [i["text"] for i in rep["limitations"]
            if i["code"] == "source-allocation-outside-scope"][0]
    assert "day bounds" in text or "not clipped" in text


# --- the source lineage descriptor is an enum, never producer text ---------------------------

def test_the_known_engine_lineage_value_maps_to_a_safe_enum():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert rep["source"]["lineage"] == "path-nesting-unverified"


@pytest.mark.parametrize("hostile", [
    "/home/ezalos/.claude/projects/0f3ab2c1-11de-4d3e-9a77-aa11bb22cc33.jsonl",
    "https://example.org/leak?token=abc123",
    "path-nesting via /home/someone/private/repo",
    "root-a owned by session 0f3ab2c1",
])
def test_an_unrecognised_lineage_descriptor_is_withheld_not_exported(hostile):
    rep = R.build_report(goal(), cost(lineageFrom=hostile), now_utc=NOW)
    assert rep["source"]["lineage"] == "unknown-lineage-descriptor"
    assert hostile not in json.dumps(rep)
    assert "source-lineage-descriptor-withheld" in codes(rep)


def test_an_unrecognised_lineage_descriptor_does_not_reject_the_report():
    rep = R.build_report(goal(), cost(lineageFrom="something new upstream"), now_utc=NOW)
    assert rep["allocated_api_equivalent"]["known_subtotal_usd"] == "1.25"
    assert rep["source"]["lineage"] == "unknown-lineage-descriptor"


def test_the_known_lineage_value_adds_no_withholding_limitation():
    assert "source-lineage-descriptor-withheld" not in codes(
        R.build_report(goal(), cost(), now_utc=NOW))


def test_the_permanent_lineage_limitation_survives_a_withheld_descriptor():
    rep = R.build_report(goal(), cost(lineageFrom="/private/path"), now_utc=NOW)
    assert {"lineage-path-derived", "source-allocation-not-attribution"} <= codes(rep)


def test_no_upstream_identifier_travels_with_the_coverage_verdict():
    snap = with_coverage(coverage(False, 1))
    # Whatever else the engine may add beside the verdict stays upstream.
    snap["scope"]["allocationCoverage"]["allocatedElsewhere"] = {
        "msg_01private": "/home/someone/private/repo/root-a.jsonl"}
    rep = R.build_report(goal(), snap, now_utc=NOW)
    blob = json.dumps(rep)
    assert "msg_01private" not in blob
    assert "/home/someone/private" not in blob


def test_the_count_is_never_described_as_a_goal_or_campaign_count():
    rep = R.build_report(goal(), with_coverage(coverage(False, 1)), now_utc=NOW)
    text = [item["text"] for item in rep["limitations"]
            if item["code"] == "source-allocation-outside-scope"][0]
    assert "message" in text
    assert "goal" not in text.split("producing-goal")[0].replace("this goal", "")
    assert "not a producing-goal claim" in text or "producing-goal" in text


def test_the_permanent_allocation_limitations_survive_beside_the_new_one():
    rep = R.build_report(goal(), with_coverage(coverage(False, 1)), now_utc=NOW)
    assert {"source-allocation-not-attribution", "lineage-path-derived",
            "source-allocation-outside-scope"} <= codes(rep)


# --- I-2: the combined boolean is named for what it combines ---------------------------------

def test_the_old_root_only_key_is_gone_from_the_output():
    rep = R.build_report(goal(), cost(), now_utc=NOW)
    assert "covers_every_requested_root" not in json.dumps(rep)
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is True


def test_the_combined_key_is_false_when_only_allocation_fell_short():
    rep = R.build_report(goal(), with_coverage(coverage(False, 1)), now_utc=NOW)
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is False
    assert rep["scope"]["roots_unknown"] == 0


def test_the_combined_key_matches_the_scope_verdict_exactly():
    for snap in (cost(), with_coverage(coverage(False, 1)), with_coverage(None, drop=True)):
        rep = R.build_report(goal(), snap, now_utc=NOW)
        assert (rep["allocated_api_equivalent"]["whole_source_scope_covered"]
                is rep["scope"]["coverage_complete"])


# --- M6: a resolved root with no priced row is an unknown wearing a zero's clothes ------------

def test_a_rowless_snapshot_is_not_complete_coverage():
    rep = R.build_report(goal(), cost(rows=[]), now_utc=NOW)
    assert rep["scope"]["coverage_complete"] is False
    assert rep["allocated_api_equivalent"]["whole_source_scope_covered"] is False
    assert "no-priced-rows" in codes(rep)


def test_a_rowless_snapshot_is_not_priced_complete():
    money = R.build_report(goal(), cost(rows=[]), now_utc=NOW)["allocated_api_equivalent"]
    assert money["pricing_complete"] is False
    assert money["known_subtotal_usd"] == "0"
    assert money["display_usd"] == "0.00"


def test_a_rowless_snapshot_does_no_token_or_price_arithmetic():
    usage = R.build_report(goal(), cost(rows=[]), now_utc=NOW)["usage"]
    assert usage["rows"] == 0 and usage["turns"] == 0 and usage["input_tokens"] == 0


def test_a_snapshot_with_rows_keeps_its_pricing_verdict():
    money = R.build_report(goal(), cost(), now_utc=NOW)["allocated_api_equivalent"]
    assert money["pricing_complete"] is True
    assert "no-priced-rows" not in codes(R.build_report(goal(), cost(), now_utc=NOW))


def test_the_rowless_limitation_says_unknown_not_zero():
    rep = R.build_report(goal(), cost(rows=[]), now_utc=NOW)
    text = [i["text"] for i in rep["limitations"] if i["code"] == "no-priced-rows"][0]
    assert "unknown" in text.lower()
    assert "zero" in text.lower()


# --- M3: an absent attention day is not recorded, not zero -----------------------------------

def test_days_absent_from_the_record_are_reported_as_unrecorded():
    g = goal(interventions=[{"id": "i-1", "at_utc": "2026-09-17T02:00:00Z",
                             "required": True, "minutes": 5}])
    attention = R.build_report(g, cost(), now_utc=NOW)["attention"]
    assert attention["unrecorded_days"] is None
    assert "not recorded" in attention["unrecorded_days_note"].lower()


# --- the README is the pre-live gate's only control, so its claims are asserted ----------------

README = Path(__file__).resolve().parent.parent / "goals" / "README.md"


def readme():
    return README.read_text(encoding="utf-8")


def test_the_freshness_caveat_covers_a_rewrite_that_grows_not_only_a_same_size_one():
    """Upstream D-5: a rewrite that GROWS is read as an append from the old offset, so only the
    tail is parsed and the stale aggregate survives - and it is far more plausible than a
    byte-identical rewrite. A caveat naming only the same-size case tells a reader whose
    transcripts grow that it does not apply to them."""
    text = readme()
    window = text[text.index("not proof its source bytes were fresh"):][:1200]
    assert "grow" in window
    assert "append" in window
    assert "same size" in window


def test_the_freshness_caveat_does_not_claim_upstream_is_fixed():
    text = readme()
    window = text[text.index("not proof its source bytes were fresh"):][:1200]
    for claim in ("has been fixed", "is fixed", "no longer"):
        assert claim not in window, claim


def test_the_salt_section_says_it_lives_in_two_files_and_never_leaves_them():
    text = readme()
    assert "never leaves those two files" in text
    assert "never leaves the manifest" not in text


def test_the_keyed_handle_paragraph_names_its_own_subject():
    """The salt subsection was inserted between a sentence about the handles and its continuation,
    leaving `They answer exactly one question...` 25 lines and a heading away from its antecedent."""
    text = readme()
    assert "They answer exactly one question" not in text
    assert "The handles answer exactly one question" in text

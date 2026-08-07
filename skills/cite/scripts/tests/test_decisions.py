# ABOUTME: Tests deterministic decision tables: recency, auto-approve status, auto-promote, corroboration.
from datetime import date
import decisions as d

TODAY = date(2026, 6, 20)

# --- recency ---
def test_recency_unknown_when_no_date():
    assert d.recency_verdict(None, TODAY) == "unknown"
def test_recency_historical_event():
    assert d.recency_verdict("2012-09-30", TODAY) == "historical-event"
def test_recency_fresh_recent_stale():
    assert d.recency_verdict("2026-05-01", TODAY) == "fresh"     # <180d
    assert d.recency_verdict("2025-09-01", TODAY) == "recent"    # 180-365d
    assert d.recency_verdict("2024-01-01", TODAY) == "stale"     # >365d
def test_recency_historical_event_type_exempts_old_date():
    # a settled past event/milestone is never "stale" regardless of source age
    assert d.recency_verdict("2023-11-06", TODAY, claim_type="historical-event") == "historical-event"
def test_recency_historical_event_type_with_no_date():
    assert d.recency_verdict(None, TODAY, claim_type="historical-event") == "historical-event"
def test_recency_type_does_not_affect_non_historical():
    assert d.recency_verdict("2023-11-06", TODAY, claim_type="company-fact") == "stale"
    assert d.recency_verdict("2026-05-01", TODAY, claim_type="number") == "fresh"

# --- status_for ---
def test_status_auto_approved():
    assert d.status_for(2, "fresh")[0] == "auto-approved"
    assert d.status_for(4, "historical-event")[0] == "auto-approved"
def test_status_flagged_low_tier():
    assert d.status_for(5, "fresh")[0] == "flagged-low-reputation"
    assert d.status_for(None, "fresh")[0] == "flagged-low-reputation"
def test_status_flagged_stale():
    assert d.status_for(1, "stale")[0] == "flagged-low-reputation"

# --- promote_verdict ---  args: orig_tier, orig_date, new_tier, new_date, value_match, value_determinable
def test_promote_auto_when_newer_and_as_authoritative_and_same_value():
    assert d.promote_verdict(3, "2024-01-01", 3, "2026-01-01", True, True) == "auto-promote"
    assert d.promote_verdict(3, "2024-01-01", 1, "2026-01-01", True, True) == "auto-promote"
def test_promote_conflict_on_different_value():
    assert d.promote_verdict(3, "2024-01-01", 1, "2026-01-01", False, True) == "flag-claim-conflict"
def test_promote_flag_when_value_unknown():
    assert d.promote_verdict(3, "2024-01-01", 1, "2026-01-01", False, False) == "flag-better-source"
def test_promote_flag_when_date_unknown():
    assert d.promote_verdict(3, None, 1, "2026-01-01", True, True) == "flag-better-source"
def test_promote_keep_when_not_newer():
    assert d.promote_verdict(3, "2026-02-01", 3, "2026-01-01", True, True) == "keep"
def test_promote_flag_when_worse_tier():
    assert d.promote_verdict(2, "2024-01-01", 5, "2026-01-01", True, True) == "flag-better-source"

# --- corroboration_status ---  secondaries: list of {validated, independent, value_match}
def test_corroboration_confirmed():
    secs = [{"validated": True, "independent": True, "value_match": True}]
    assert d.corroboration_status(secs) == "confirmed"
def test_corroboration_weak_when_not_independent():
    secs = [{"validated": True, "independent": False, "value_match": True}]
    assert d.corroboration_status(secs) == "weak"
def test_corroboration_conflicting_beats_confirmed():
    secs = [{"validated": True, "independent": True, "value_match": True},
            {"validated": True, "independent": True, "value_match": False}]
    assert d.corroboration_status(secs) == "conflicting"
def test_corroboration_uncorroborated_when_empty():
    assert d.corroboration_status([]) == "uncorroborated"

# --- apply_corroboration upgrade ---
def test_low_tier_upgraded_by_independent_lowtier_secondary():
    secs = [{"validated": True, "independent": True, "value_match": True, "tier": 3}]
    assert d.apply_corroboration("flagged-low-reputation", 5, secs) == "auto-approved"
def test_low_tier_not_upgraded_by_weak_corroboration():
    secs = [{"validated": True, "independent": False, "value_match": True, "tier": 3}]
    assert d.apply_corroboration("flagged-low-reputation", 5, secs) == "flagged-low-reputation"

import json as _json
import subprocess, sys
from pathlib import Path
_SCRIPT = Path(__file__).resolve().parents[1] / "decisions.py"

def test_corroborate_cli_confirmed_and_upgrades():
    secs = [{"validated": True, "independent": True, "value_match": True, "tier": 3}]
    r = subprocess.run([sys.executable, str(_SCRIPT), "corroborate",
                        "--base-status", "flagged-low-reputation", "--tier", "5",
                        "--secondaries-json", _json.dumps(secs)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = _json.loads(r.stdout)
    assert out["corroboration_status"] == "confirmed"
    assert out["final_status"] == "auto-approved"

def test_corroborate_cli_empty_is_uncorroborated_no_upgrade():
    r = subprocess.run([sys.executable, str(_SCRIPT), "corroborate",
                        "--base-status", "flagged-low-reputation", "--tier", "5",
                        "--secondaries-json", "[]"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = _json.loads(r.stdout)
    assert out["corroboration_status"] == "uncorroborated"
    assert out["final_status"] == "flagged-low-reputation"

def test_recency_cli_type_exempts_historical_event():
    r = subprocess.run([sys.executable, str(_SCRIPT), "recency", "2023-11-06",
                        "--type", "historical-event"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "historical-event"

# ABOUTME: Tests link-health verdict logic with an injected fetcher (no real network in unit tests).
import link_check as lc

def test_ok_same_host():
    res = lc.verdict("https://sec.gov/a", final_url="https://sec.gov/a", code=200)
    assert res["status"] == "ok"

def test_dead_on_404():
    res = lc.verdict("https://sec.gov/a", final_url="https://sec.gov/a", code=404)
    assert res["status"] == "dead"

def test_redirect_to_other_host_is_suspect():
    res = lc.verdict("https://old.com/a", final_url="https://spam.com/home", code=200)
    assert res["status"] == "redirect-suspect"

def test_redirect_same_host_is_ok():
    res = lc.verdict("https://sec.gov/a", final_url="https://sec.gov/a?ref=1", code=200)
    assert res["status"] == "ok"

def test_dead_on_connection_error():
    res = lc.verdict("https://sec.gov/a", final_url=None, code=None, error="timeout")
    assert res["status"] == "dead"

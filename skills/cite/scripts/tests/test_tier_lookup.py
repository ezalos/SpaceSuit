# ABOUTME: Tests layered authority-map tier lookup with subdomain walk-up and overlay precedence.
import tier_lookup as tl

def _load(name):
    from pathlib import Path
    import yaml
    p = Path(__file__).parent / "fixtures" / name
    return yaml.safe_load(p.read_text())

def test_single_map_exact():
    assert tl.lookup("sec.gov", _load("map_global.yaml")) == 1

def test_subdomain_walks_up():
    assert tl.lookup("news.sec.gov", _load("map_global.yaml")) == 1

def test_unknown_returns_none():
    assert tl.lookup("example.com", _load("map_global.yaml")) is None

def test_layered_overlay_wins():
    layers = [_load("map_global.yaml"), _load("map_overlay.yaml")]
    assert tl.lookup_layered("techcrunch.com", layers) == 3   # overlay re-tiers from 5
    assert tl.lookup_layered("statista.com", layers) == 3     # overlay-only domain
    assert tl.lookup_layered("sec.gov", layers) == 1          # untouched base entry

# --- ported from Markdowns2Teach scripts/cite/tests/test_tier_lookup.py ---

def test_case_insensitive_domain():
    assert tl.lookup("SEC.GOV", _load("map_global.yaml")) == 1

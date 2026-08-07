<!-- ABOUTME: Bite-sized TDD implementation plan for generalizing the /cite skill family into Setup. -->
<!-- ABOUTME: Derived from docs/superpowers/specs/2026-06-10-cite-generalization-design.md. -->

# /cite Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the M2T-only `/cite` skill family into a Setup-tracked, project-agnostic source-verification pipeline that diagnoses existing + missing citations, remediates with corroboration, and corrects documents — with deterministic decisions in tested Python scripts.

**Architecture:** Four skills (`/cite` orchestrator + `cite-diagnose` / `cite-remediate` / `cite-correct`) deployed from `dotfiles/claude_skill_cite*/` to `~/.claude/skills/`. All deterministic logic (tier lookup, recency, status, value-match, auto-promote, corroboration independence) lives in pure, pytest-covered Python under the orchestrator's `scripts/`. Authority map is layered (skill-global memory → project overlay → per-run overlay). Run state lives centrally in `~/.local/state/cite/<slug>/`. Subagents return raw data only; the orchestrator computes every verdict via scripts.

**Tech Stack:** Python 3 (stdlib + `pyyaml`; `urllib` for HTTP — no `requests`), pytest, Tavily REST API, pandoc/pdftotext for input conversion, the `src_dotfiles` registry CLI for deployment.

---

## File Structure

Development happens inside the Setup repo at `dotfiles/claude_skill_cite/` (the orchestrator dir, canonical home for shared scripts/memory/references) plus three sibling skill dirs that hold only a `SKILL.md`.

```
dotfiles/claude_skill_cite/                  → ~/.claude/skills/cite/
  SKILL.md                                   # orchestrator prose
  references/sourcing-standards.md           # English translation of M2T §6, generalized
  memory/authority-map.yaml                  # skill-global roster (seeded from M2T)
  memory/authority-map.md                    # human-readable mirror (lint-synced)
  scripts/
    validate_claim.py        # ported + values_match()  (verbatim quote + value check)
    tier_lookup.py           # ported + layered --map (repeatable)
    lint_authority_map.py    # ported (md/yaml sync lint)
    decisions.py             # NEW pure: recency_verdict / status_for / promote_verdict / corroboration_status
    value_match.py           # NEW thin CLI → validate_claim.values_match
    textsim.py               # NEW near-duplicate detection (independence check)
    link_check.py            # NEW HTTP health for existing citations
    tavily_cli.py            # NEW search + extract over Tavily REST
    bundle_path.py           # NEW slug + central/legacy bundle resolution
    convert_input.py         # NEW input-format routing (md/pdf/html/docx/url)
    requirements.txt         # pyyaml
    tests/
      conftest.py
      test_validate_claim.py   test_tier_lookup.py      test_lint_authority_map.py
      test_decisions.py        test_value_match.py      test_textsim.py
      test_link_check.py       test_tavily_cli.py       test_bundle_path.py
      test_convert_input.py
      fixtures/
dotfiles/claude_skill_cite_diagnose/         → ~/.claude/skills/cite-diagnose/SKILL.md
dotfiles/claude_skill_cite_remediate/        → ~/.claude/skills/cite-remediate/SKILL.md
dotfiles/claude_skill_cite_correct/          → ~/.claude/skills/cite-correct/SKILL.md
```

**Not ported:** M2T's `target_scope.py` stays in M2T — it routes to `make check`/`marp`, which is M2T's build. The generic correct-phase uses a citation-format profile instead (Task 13).

**Runtime dependency note:** scripts are invoked with `python3`. System `/usr/bin/python3` already has `pyyaml 5.4.1`. Setup's `.venv` does not — Task 1 installs it for the test runs. Scripts must use only `pyyaml` + stdlib so they run on any project without a venv.

## Phasing

- **Phase A (Tasks 1–11):** scripts + memory + translation. All TDD, no skill prose. This is the reliability core.
- **Phase B (Tasks 12–15):** the four SKILL.md files (prose; verified by dry-run/structural checks, not pytest).
- **Phase C (Tasks 16–17):** registration/deployment + end-to-end verification.

Commit after every task. Run `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/ -q` as the standing test command (abbreviated `PYTEST` below).

---

## Task 1: Scaffold skill dirs, deps, and test harness

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/requirements.txt`
- Create: `dotfiles/claude_skill_cite/scripts/tests/conftest.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/__init__.py` (empty)
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_smoke.py`

- [ ] **Step 1: Create directories**

```bash
cd ~/Setup
mkdir -p dotfiles/claude_skill_cite/scripts/tests/fixtures
mkdir -p dotfiles/claude_skill_cite/references
mkdir -p dotfiles/claude_skill_cite/memory
mkdir -p dotfiles/claude_skill_cite_diagnose dotfiles/claude_skill_cite_remediate dotfiles/claude_skill_cite_correct
```

- [ ] **Step 2: Install pyyaml into the Setup venv (for tests)**

```bash
cd ~/Setup && .venv/bin/pip install pyyaml
```
Expected: `Successfully installed PyYAML-...`

- [ ] **Step 3: Write `requirements.txt`**

```
# ABOUTME: Runtime deps for the cite skill scripts. System python3 already ships pyyaml.
pyyaml>=5.4
```

- [ ] **Step 4: Write `tests/conftest.py`** (puts `scripts/` on the import path)

```python
# ABOUTME: pytest config — adds the scripts/ dir to sys.path so tests import modules directly.
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
```

- [ ] **Step 5: Write `tests/test_smoke.py`**

```python
# ABOUTME: Confirms the test harness + import path work before real modules exist.
def test_harness_alive():
    assert True
```

- [ ] **Step 6: Run it**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_smoke.py -q`
Expected: `1 passed`

- [ ] **Step 7: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite dotfiles/claude_skill_cite_diagnose dotfiles/claude_skill_cite_remediate dotfiles/claude_skill_cite_correct
git commit -F /tmp/cite-msg.txt
```
(Write the message to `/tmp/cite-msg.txt` first: subject `feat(cite): scaffold generalized skill dirs and test harness`, blank line, `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.)

---

## Task 2: Port `tier_lookup.py` with layered maps

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/tier_lookup.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_tier_lookup.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/fixtures/map_global.yaml`
- Create: `dotfiles/claude_skill_cite/scripts/tests/fixtures/map_overlay.yaml`

- [ ] **Step 1: Write fixtures**

`map_global.yaml`:
```yaml
tiers:
  1:
    name: "Primary"
    publishers:
      - name: "SEC"
        domains: ["sec.gov"]
  5:
    name: "Tier-2 press"
    publishers:
      - name: "TechCrunch"
        domains: ["techcrunch.com"]
```
`map_overlay.yaml` (re-tiers techcrunch, adds a new domain):
```yaml
tiers:
  3:
    name: "Promoted"
    publishers:
      - name: "TechCrunch (promoted)"
        domains: ["techcrunch.com"]
      - name: "Statista"
        domains: ["statista.com"]
```

- [ ] **Step 2: Write the failing test**

```python
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
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_tier_lookup.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tier_lookup'`

- [ ] **Step 4: Write `tier_lookup.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Look up authority tier for a URL domain against one or more layered authority maps.
# ABOUTME: Later --map layers override earlier ones. Outputs tier integer (1-6) or "null". No LLM judgment.

import argparse
import sys
from pathlib import Path

import yaml

DEFAULT_MAP = Path(__file__).resolve().parents[1] / "memory/authority-map.yaml"


def load_map(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _domain_chain(domain):
    """Yield the domain and each parent (news.foo.com -> news.foo.com, foo.com)."""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def _domain_to_tier(authority_map):
    out = {}
    for tier_num, tier_data in (authority_map or {}).get("tiers", {}).items():
        for publisher in tier_data.get("publishers", []):
            for d in publisher.get("domains", []):
                out[d.lower()] = int(tier_num)
    return out


def lookup(domain, authority_map):
    return lookup_layered(domain, [authority_map])


def lookup_layered(domain, authority_maps):
    domain = (domain or "").strip().lower()
    if not domain:
        return None
    merged = {}
    for amap in authority_maps:          # later layers override earlier
        merged.update(_domain_to_tier(amap))
    for candidate in _domain_chain(domain):
        if candidate in merged:
            return merged[candidate]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", help="URL domain to look up (e.g., sec.gov)")
    parser.add_argument("--map", action="append", default=None,
                        help="Path to an authority-map.yaml; repeatable, later wins")
    args = parser.parse_args()
    paths = args.map or [str(DEFAULT_MAP)]
    layers = [load_map(p) for p in paths]
    tier = lookup_layered(args.domain, layers)
    print("null" if tier is None else str(tier))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_tier_lookup.py -q`
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/tier_lookup.py dotfiles/claude_skill_cite/scripts/tests/
git commit -m "feat(cite): layered tier_lookup with overlay precedence"
```

---

## Task 3: Seed the skill-global authority map + port the lint

**Files:**
- Create: `dotfiles/claude_skill_cite/memory/authority-map.yaml` (copied from M2T)
- Create: `dotfiles/claude_skill_cite/memory/authority-map.md` (copied from M2T)
- Create: `dotfiles/claude_skill_cite/scripts/lint_authority_map.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_lint_authority_map.py`

- [ ] **Step 1: Seed the map from M2T**

```bash
cp ~/42/Markdowns2Teach/docs/references/authority-map.yaml ~/Setup/dotfiles/claude_skill_cite/memory/authority-map.yaml
cp ~/42/Markdowns2Teach/docs/references/authority-map.md  ~/Setup/dotfiles/claude_skill_cite/memory/authority-map.md
```

- [ ] **Step 2: Port the lint script**

```bash
cp ~/42/Markdowns2Teach/scripts/cite/lint_authority_map.py ~/Setup/dotfiles/claude_skill_cite/scripts/lint_authority_map.py
```
Then edit its `DEFAULT_MD`/`DEFAULT_YAML` constants to point at the skill memory dir:

```python
REPO_ROOT = Path(__file__).resolve().parents[1]            # was parents[2]
DEFAULT_MD = REPO_ROOT / "memory/authority-map.md"          # was docs/references/...
DEFAULT_YAML = REPO_ROOT / "memory/authority-map.yaml"
```

- [ ] **Step 3: Write the failing test**

```python
# ABOUTME: Confirms the seeded skill-global authority map passes its own md/yaml sync lint.
import subprocess, sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lint_authority_map.py"

def test_seeded_map_in_sync():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 4: Run it**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_lint_authority_map.py -q`
Expected: `1 passed` (if it fails on drift, the M2T source map drifted — reconcile by re-copying both files together, do NOT hand-edit one side).

- [ ] **Step 5: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/memory dotfiles/claude_skill_cite/scripts/lint_authority_map.py dotfiles/claude_skill_cite/scripts/tests/test_lint_authority_map.py
git commit -m "feat(cite): seed skill-global authority map + port sync lint"
```

---

## Task 4: Port `validate_claim.py` and extend enums for diagnosis

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/validate_claim.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_validate_claim.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/fixtures/claim_ok.yaml`
- Create: `dotfiles/claude_skill_cite/scripts/tests/fixtures/page_ok.txt`

- [ ] **Step 1: Port the file**

```bash
cp ~/42/Markdowns2Teach/scripts/cite/validate_claim.py ~/Setup/dotfiles/claude_skill_cite/scripts/validate_claim.py
```

- [ ] **Step 2: Extend the enums** in `validate_claim.py`

Replace the `STATUS_ENUM` and `PROPOSED_ACTION_ENUM` definitions with:

```python
STATUS_ENUM = {
    "pending", "auto-approved", "approved", "rejected", "needs-rework",
    "flagged-low-reputation", "flagged-unsourceable", "flagged-stale-stat",
    "flagged-validation-failed",
    # diagnosis of existing citations
    "uncited", "cited-healthy", "cited-broken", "cited-stale", "cited-low-tier",
    # promote / corroboration outcomes
    "auto-promoted", "flagged-claim-conflict", "flagged-better-source",
}
PROPOSED_ACTION_ENUM = {
    None, "add-citation", "update-claim-value", "soften-language", "none",
    "swap-source", "promote-source",
}
```

- [ ] **Step 3: Write fixtures**

`page_ok.txt`:
```
The global AI market reached 2527 billion dollars in 2026 according to the firm.
Adoption rose from 55% to 88% over two years across surveyed enterprises.
```
`claim_ok.yaml`:
```yaml
id: claim-01
location: {file: "deck.md", slide: "01 — Market", line: 12}
claim: {text: "Le marché atteint 2527 milliards en 2026", type: number, has_existing_source: false}
proposed_source:
  url: "https://www.example.com/report"
  url_domain: "example.com"
  publisher_org: "Example Research"
  author: null
  publication_date: "2026-01-10"
  accessed_date: "2026-06-20"
  quote: "The global AI market reached 2527 billion dollars in 2026 according to the firm."
  surrounding_paragraph: "The global AI market reached 2527 billion dollars in 2026 according to the firm."
  section_heading: null
  alignment_justification: "States the 2527 figure for 2026."
  confidence: high
status: pending
proposed_action: null
proposed_claim_update: null
validation: null
page_text_file: "claim-01.page.txt"
```

- [ ] **Step 4: Write the failing test** (covers schema/enum/quote rules + the new enum values)

```python
# ABOUTME: Tests claim YAML validation: required fields, enums, quote-in-page, extended diagnosis statuses.
from pathlib import Path
import yaml, validate_claim as vc

FX = Path(__file__).parent / "fixtures"

def _claim(): return yaml.safe_load((FX / "claim_ok.yaml").read_text())
def _page(): return (FX / "page_ok.txt").read_text()

def test_valid_claim_has_no_errors():
    assert vc.validate(_claim(), _page()) == []

def test_quote_not_in_page_is_error():
    c = _claim(); c["proposed_source"]["quote"] = "This sentence is absent."
    errs = vc.validate(c, _page())
    assert any("quote" in e for e in errs)

def test_new_diagnosis_status_allowed():
    c = _claim(); c["status"] = "cited-stale"
    assert vc.validate(c, _page()) == []

def test_unknown_status_rejected():
    c = _claim(); c["status"] = "bogus"
    assert any("status" in e for e in vc.validate(c, _page()))
```

- [ ] **Step 5: Run to verify it fails, then passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_validate_claim.py -q`
Expected: after Step 2 edits, `4 passed`. (If `test_new_diagnosis_status_allowed` fails, the enum edit in Step 2 was missed.)

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/validate_claim.py dotfiles/claude_skill_cite/scripts/tests/test_validate_claim.py dotfiles/claude_skill_cite/scripts/tests/fixtures/claim_ok.yaml dotfiles/claude_skill_cite/scripts/tests/fixtures/page_ok.txt
git commit -m "feat(cite): port validate_claim with diagnosis status enums"
```

---

## Task 5: Add `values_match()` to `validate_claim.py` + `value_match.py` CLI

**Files:**
- Modify: `dotfiles/claude_skill_cite/scripts/validate_claim.py` (add function)
- Create: `dotfiles/claude_skill_cite/scripts/value_match.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_value_match.py`

- [ ] **Step 1: Write the failing test**

```python
# ABOUTME: Tests numeric value-match used by auto-promote (conservative: scale words never auto-match).
import validate_claim as vc

def test_same_number_with_separators_matches():
    assert vc.values_match("Le marché atteint 2 527 milliards", "reached 2,527 billion dollars") is True

def test_percent_matches():
    assert vc.values_match("adoption de 88%", "rose to 88 percent") is True

def test_missing_number_is_mismatch():
    assert vc.values_match("88% adoption", "rose to 91 percent") is False

def test_scale_mismatch_does_not_auto_match():
    # claim says 2.5, source says 2500 -> different digit tokens -> no match (flag for review)
    assert vc.values_match("2.5 trillion", "2500 billion") is False

def test_no_numbers_is_not_determinable():
    assert vc.values_match("the company is the market leader", "they lead the market") is False
    assert vc.value_determinable("the company is the market leader") is False
    assert vc.value_determinable("revenue of 12 billion") is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_value_match.py -q`
Expected: FAIL — `AttributeError: module 'validate_claim' has no attribute 'values_match'`

- [ ] **Step 3: Add functions to `validate_claim.py`** (after the existing helpers)

```python
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text):
    """Extract comparable numeric tokens after stripping thousands separators."""
    if not text:
        return set()
    # remove thousands separators: ',' and ASCII/again narrow/no-break spaces between digits
    cleaned = re.sub(r"(?<=\d)[,   ](?=\d)", "", text)
    return set(_NUM_RE.findall(cleaned))


def value_determinable(claim_text):
    """True if the claim contains numeric tokens we can verify against a source."""
    return len(_numbers(claim_text)) > 0


def values_match(claim_text, quote):
    """Conservative numeric match: every number in the claim must appear in the quote.

    Returns False when the claim has no numbers (not determinable) — callers should
    use value_determinable() to distinguish 'mismatch' from 'unknown'. Scale words
    (billion/trillion) are NOT reconciled: differing digit tokens never auto-match.
    """
    claim_nums = _numbers(claim_text)
    if not claim_nums:
        return False
    quote_nums = _numbers(quote)
    return claim_nums.issubset(quote_nums)
```

- [ ] **Step 4: Write the CLI `value_match.py`**

```python
#!/usr/bin/env python3
# ABOUTME: CLI wrapper over validate_claim.values_match for the remediate orchestrator.
# ABOUTME: Prints "match" / "mismatch" / "unknown" on stdout. Logic lives in validate_claim.py.

import argparse
import sys

from validate_claim import value_determinable, values_match


def verdict(claim_text, quote):
    if not value_determinable(claim_text):
        return "unknown"
    return "match" if values_match(claim_text, quote) else "mismatch"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("claim_text")
    p.add_argument("quote")
    args = p.parse_args()
    print(verdict(args.claim_text, args.quote))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_value_match.py -q`
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/validate_claim.py dotfiles/claude_skill_cite/scripts/value_match.py dotfiles/claude_skill_cite/scripts/tests/test_value_match.py
git commit -m "feat(cite): numeric value-match for auto-promote"
```

---

## Task 6: `textsim.py` — near-duplicate detection for independence

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/textsim.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_textsim.py`

- [ ] **Step 1: Write the failing test**

```python
# ABOUTME: Tests near-duplicate detection that flags syndicated/copy-pasted sources as non-independent.
import textsim

A = "The global AI market reached 2527 billion dollars in 2026, according to a new Gartner study released Monday."
SYNDICATED = "The global AI market reached 2527 billion dollars in 2026, according to a new Gartner study released Monday."
PARAPHRASE = "An independent McKinsey survey of 1800 firms found adoption climbing from 55 to 88 percent over two years."

def test_identical_text_is_near_duplicate():
    assert textsim.near_duplicate(A, SYNDICATED) is True

def test_distinct_text_is_not_near_duplicate():
    assert textsim.near_duplicate(A, PARAPHRASE) is False

def test_ratio_is_symmetric_and_bounded():
    r = textsim.similarity(A, PARAPHRASE)
    assert 0.0 <= r <= 1.0
    assert abs(textsim.similarity(A, PARAPHRASE) - textsim.similarity(PARAPHRASE, A)) < 1e-9
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_textsim.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'textsim'`

- [ ] **Step 3: Write `textsim.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Text-similarity for source-independence checks. High similarity => syndicated/copy => not independent.
# ABOUTME: similarity() in [0,1]; near_duplicate() applies a default 0.8 threshold. CLI prints JSON.

import argparse
import json
import re
import sys
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.8


def _normalize(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def similarity(a, b):
    na, nb = _normalize(a), _normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def near_duplicate(a, b, threshold=DEFAULT_THRESHOLD):
    return similarity(a, b) >= threshold


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file_a")
    p.add_argument("file_b")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = p.parse_args()
    from pathlib import Path
    a = Path(args.file_a).read_text()
    b = Path(args.file_b).read_text()
    ratio = similarity(a, b)
    print(json.dumps({"similarity": round(ratio, 4),
                      "near_duplicate": ratio >= args.threshold}))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_textsim.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/textsim.py dotfiles/claude_skill_cite/scripts/tests/test_textsim.py
git commit -m "feat(cite): near-duplicate detection for source independence"
```

---

## Task 7: `decisions.py` — recency, status, promote, corroboration

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/decisions.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_decisions.py`

- [ ] **Step 1: Write the failing test** (the decision tables from the spec)

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_decisions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'decisions'`

- [ ] **Step 3: Write `decisions.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Pure deterministic decision tables for the cite pipeline — no LLM judgment, no I/O in core fns.
# ABOUTME: recency_verdict / status_for / promote_verdict / corroboration_status / apply_corroboration (+CLI).

import argparse
import json
import sys
from datetime import date, datetime

HISTORICAL_CUTOFF = date(2020, 1, 1)
AUTO_APPROVE_TIERS = {1, 2, 3, 4}
AUTO_APPROVE_RECENCY = {"fresh", "recent", "historical-event"}


def _parse(d):
    if not d:
        return None
    return datetime.strptime(str(d), "%Y-%m-%d").date()


def recency_verdict(publication_date, today):
    pd = _parse(publication_date)
    if pd is None:
        return "unknown"
    if pd < HISTORICAL_CUTOFF:
        return "historical-event"
    age = (today - pd).days
    if age <= 180:
        return "fresh"
    if age <= 365:
        return "recent"
    return "stale"


def status_for(tier, recency):
    if tier in AUTO_APPROVE_TIERS and recency in AUTO_APPROVE_RECENCY:
        return ("auto-approved", None)
    if tier not in AUTO_APPROVE_TIERS:
        return ("flagged-low-reputation", f"tier {tier} below auto-approve threshold")
    return ("flagged-low-reputation", f"recency '{recency}' not auto-approvable")


def promote_verdict(orig_tier, orig_date, new_tier, new_date, value_match, value_determinable):
    """Auto-promote an existing citation to a newer source — SOURCE SWAP ONLY."""
    if not value_determinable:
        return "flag-better-source"          # can't confirm same value -> human reviews
    if not value_match:
        return "flag-claim-conflict"         # new source states a different value
    od, nd = _parse(orig_date), _parse(new_date)
    if od is None or nd is None:
        return "flag-better-source"          # date ambiguity -> review
    if nd <= od:
        return "keep"                        # not actually newer
    if new_tier is None or orig_tier is None:
        return "flag-better-source"
    if new_tier <= orig_tier:                # numerically lower tier == more authoritative
        return "auto-promote"
    return "flag-better-source"              # newer but less authoritative


def corroboration_status(secondaries):
    if not secondaries:
        return "uncorroborated"
    validated = [s for s in secondaries if s.get("validated")]
    if any(s.get("value_match") is False for s in validated):
        return "conflicting"
    if any(s.get("independent") and s.get("value_match") for s in validated):
        return "confirmed"
    return "weak"


def apply_corroboration(base_status, tier, secondaries):
    """A flagged low-tier claim becomes auto-approved if an independent, validated,
    value-matching secondary of tier <=4 corroborates it."""
    if base_status != "flagged-low-reputation":
        return base_status
    for s in secondaries:
        if (s.get("validated") and s.get("independent") and s.get("value_match")
                and isinstance(s.get("tier"), int) and s["tier"] <= 4):
            return "auto-approved"
    return base_status


def _today():
    return date.today()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recency"); r.add_argument("date")
    s = sub.add_parser("status"); s.add_argument("tier"); s.add_argument("recency")
    pr = sub.add_parser("promote")
    for a in ("orig_tier", "orig_date", "new_tier", "new_date"):
        pr.add_argument(a)
    pr.add_argument("value_match"); pr.add_argument("value_determinable")

    args = p.parse_args()

    def _t(v):
        return None if v in ("null", "None", "") else int(v)

    def _b(v):
        return v.lower() in ("true", "1", "yes", "match")

    if args.cmd == "recency":
        print(recency_verdict(args.date, _today()))
    elif args.cmd == "status":
        print(json.dumps(status_for(_t(args.tier), args.recency)))
    elif args.cmd == "promote":
        print(promote_verdict(_t(args.orig_tier), args.orig_date, _t(args.new_tier),
                              args.new_date, _b(args.value_match), _b(args.value_determinable)))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_decisions.py -q`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/decisions.py dotfiles/claude_skill_cite/scripts/tests/test_decisions.py
git commit -m "feat(cite): deterministic decision tables (recency/status/promote/corroboration)"
```

---

## Task 8: `bundle_path.py` — central state with legacy fallback

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/bundle_path.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_bundle_path.py`

- [ ] **Step 1: Write the failing test**

```python
# ABOUTME: Tests slug derivation and central/legacy bundle-dir resolution.
from pathlib import Path
import bundle_path as bp

def test_slug_strips_md_and_replaces_slashes():
    assert bp.slug_for("slides/session-05/A-reg.md") == "slides-session-05-A-reg"

def test_slug_handles_absolute_paths():
    s = bp.slug_for("/home/x/My Deck.md")
    assert s == "home-x-My Deck" or s == "home-x-My-Deck"  # accept space or hyphen, no leading dash
    assert not s.startswith("-")

def test_central_dir_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CITE_STATE_HOME", str(tmp_path))
    d = bp.bundle_dir("deck.md")
    assert str(d).startswith(str(tmp_path))
    assert d.name == "deck"

def test_legacy_dir_preferred_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CITE_STATE_HOME", str(tmp_path / "state"))
    legacy = tmp_path / "docs" / "citation-audit" / "deck"
    legacy.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert bp.bundle_dir("deck.md") == legacy
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_bundle_path.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bundle_path'`

- [ ] **Step 3: Write `bundle_path.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Resolve the run-state bundle directory for a target document.
# ABOUTME: Central default ~/.local/state/cite/<slug>/; prefers a pre-existing legacy docs/citation-audit/<slug>/.

import argparse
import os
import sys
from pathlib import Path


def slug_for(target_path):
    p = str(target_path).strip()
    if p.endswith(".md"):
        p = p[:-3]
    slug = p.replace("/", "-").strip("-")
    return slug


def _state_home():
    env = os.environ.get("CITE_STATE_HOME")
    if env:
        return Path(env)
    return Path.home() / ".local" / "state" / "cite"


def bundle_dir(target_path):
    slug = slug_for(target_path)
    legacy = Path.cwd() / "docs" / "citation-audit" / slug
    if legacy.exists():
        return legacy
    return _state_home() / slug


def main():
    p = argparse.ArgumentParser()
    p.add_argument("target")
    p.add_argument("--ensure", action="store_true", help="mkdir -p the bundle + claims/")
    args = p.parse_args()
    d = bundle_dir(args.target)
    if args.ensure:
        (d / "claims").mkdir(parents=True, exist_ok=True)
    print(d)
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_bundle_path.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/bundle_path.py dotfiles/claude_skill_cite/scripts/tests/test_bundle_path.py
git commit -m "feat(cite): central run-state with legacy bundle fallback"
```

---

## Task 9: `convert_input.py` — input-format routing

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/convert_input.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_convert_input.py`

- [ ] **Step 1: Write the failing test** (routing logic is pure; actual conversion is integration)

```python
# ABOUTME: Tests input-format routing (md native, pdf->pdftotext, html/docx->pandoc, url->fetch+pandoc).
import convert_input as ci

def test_markdown_is_native():
    assert ci.route("deck.md") == "native"

def test_pdf_routes_to_pdftotext():
    assert ci.route("report.pdf") == "pdftotext"

def test_html_and_docx_route_to_pandoc():
    assert ci.route("page.html") == "pandoc"
    assert ci.route("page.htm") == "pandoc"
    assert ci.route("doc.docx") == "pandoc"

def test_url_routes_to_url():
    assert ci.route("https://example.com/post") == "url"
    assert ci.route("http://example.com/post") == "url"

def test_unknown_extension_raises():
    import pytest
    with pytest.raises(ValueError):
        ci.route("archive.zip")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_convert_input.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'convert_input'`

- [ ] **Step 3: Write `convert_input.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Route a citation target to its converter and (with --run) emit converted.md into the bundle.
# ABOUTME: md=native, pdf=pdftotext, html/docx=pandoc, http(s)=fetch+pandoc. Pure route() is unit-tested.

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


def route(target):
    t = str(target).lower()
    if t.startswith(("http://", "https://")):
        return "url"
    if t.endswith(".md"):
        return "native"
    if t.endswith(".pdf"):
        return "pdftotext"
    if t.endswith((".html", ".htm", ".docx")):
        return "pandoc"
    raise ValueError(f"unsupported input format: {target}")


def convert(target, out_dir):
    """Return the path to a markdown file representing `target`. May be the original."""
    kind = route(target)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "native":
        return Path(target)
    out = out_dir / "converted.md"
    if kind == "pdftotext":
        subprocess.run(["pdftotext", "-layout", str(target), str(out.with_suffix(".txt"))], check=True)
        out.write_text(out.with_suffix(".txt").read_text())
    elif kind == "pandoc":
        subprocess.run(["pandoc", str(target), "-t", "gfm", "-o", str(out)], check=True)
    elif kind == "url":
        raw = out_dir / "fetched.html"
        with urlopen(target, timeout=30) as resp:       # nosec - user-supplied target
            raw.write_bytes(resp.read())
        subprocess.run(["pandoc", str(raw), "-f", "html", "-t", "gfm", "-o", str(out)], check=True)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("target")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run", action="store_true", help="perform conversion (else just print the route)")
    args = p.parse_args()
    if args.run:
        print(convert(args.target, args.out_dir))
    else:
        print(route(args.target))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_convert_input.py -q`
Expected: `5 passed`

- [ ] **Step 5: Integration smoke (manual, one-off)**

Run: `cd /tmp && printf '# T\n\nHello **world**.\n' > t.md && pandoc t.md -t gfm -o /dev/stdout`
Expected: GFM output prints (confirms pandoc works); no assertion needed.

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/convert_input.py dotfiles/claude_skill_cite/scripts/tests/test_convert_input.py
git commit -m "feat(cite): input-format conversion routing"
```

---

## Task 10: `link_check.py` — existing-citation health

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/link_check.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_link_check.py`

- [ ] **Step 1: Write the failing test** (verdict logic is pure; network call is injected)

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_link_check.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'link_check'`

- [ ] **Step 3: Write `link_check.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Check an existing citation URL's health: ok / dead / redirect-suspect. CLI prints JSON.
# ABOUTME: verdict() is pure (testable); check() does the urllib request and feeds verdict().

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _host(url):
    if not url:
        return None
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def verdict(url, final_url=None, code=None, error=None):
    if error or code is None:
        return {"status": "dead", "http_code": code, "final_url": final_url, "error": error}
    if code >= 400:
        return {"status": "dead", "http_code": code, "final_url": final_url, "error": None}
    if final_url and _host(final_url) != _host(url):
        return {"status": "redirect-suspect", "http_code": code, "final_url": final_url, "error": None}
    return {"status": "ok", "http_code": code, "final_url": final_url, "error": None}


def check(url, timeout=20):
    req = Request(url, method="GET", headers={"User-Agent": "cite-link-check/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:    # nosec - checking a user-cited URL
            return verdict(url, final_url=resp.geturl(), code=resp.status)
    except HTTPError as e:
        return verdict(url, final_url=url, code=e.code)
    except (URLError, TimeoutError, ValueError) as e:
        return verdict(url, error=str(e))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--timeout", type=int, default=20)
    args = p.parse_args()
    print(json.dumps(check(args.url, timeout=args.timeout)))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_link_check.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/link_check.py dotfiles/claude_skill_cite/scripts/tests/test_link_check.py
git commit -m "feat(cite): existing-citation link health check"
```

---

## Task 11: `tavily_cli.py` — search + extract over REST

**Files:**
- Create: `dotfiles/claude_skill_cite/scripts/tavily_cli.py`
- Create: `dotfiles/claude_skill_cite/scripts/tests/test_tavily_cli.py`

- [ ] **Step 1: Write the failing test** (key resolution + payload build; `--dry-run` avoids network)

```python
# ABOUTME: Tests Tavily key resolution and request-payload construction without hitting the network.
import json
import tavily_cli as tv

def test_key_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    assert tv.resolve_key() == "tvly-env"

def test_key_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    f = tmp_path / "tavily_api_key"
    f.write_text("tvly-file\n")
    assert tv.resolve_key(key_file=f) == "tvly-file"

def test_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError):
        tv.resolve_key(key_file=tmp_path / "nope")

def test_search_payload():
    body = tv.build_search_payload("ai market 2026", max_results=3,
                                   include_domains=["gartner.com"], days=180)
    assert body["query"] == "ai market 2026"
    assert body["max_results"] == 3
    assert body["include_domains"] == ["gartner.com"]
    assert body["days"] == 180

def test_extract_payload():
    body = tv.build_extract_payload("https://x.com/a")
    assert body["urls"] == ["https://x.com/a"]
    assert body["extract_depth"] == "advanced"
    assert body["format"] == "markdown"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_tavily_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tavily_cli'`

- [ ] **Step 3: Write `tavily_cli.py`**

```python
#!/usr/bin/env python3
# ABOUTME: Tavily REST CLI (search + extract) for the cite pipeline — usable in subagents and headless runs.
# ABOUTME: Key from $TAVILY_API_KEY or ~/.claude/secrets/tavily_api_key. --dry-run prints the payload only.

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
DEFAULT_KEY_FILE = Path.home() / ".claude" / "secrets" / "tavily_api_key"


def resolve_key(key_file=DEFAULT_KEY_FILE):
    import os
    env = os.environ.get("TAVILY_API_KEY")
    if env:
        return env.strip()
    kf = Path(key_file)
    if kf.exists():
        return kf.read_text().strip()
    raise RuntimeError(
        "No Tavily API key. Set $TAVILY_API_KEY or write it to "
        f"{DEFAULT_KEY_FILE} (chmod 600). Get one at https://app.tavily.com/."
    )


def build_search_payload(query, max_results=5, include_domains=None, days=None, search_depth="advanced"):
    body = {"query": query, "max_results": max_results, "search_depth": search_depth}
    if include_domains:
        body["include_domains"] = include_domains
    if days is not None:
        body["days"] = days
    return body


def build_extract_payload(url):
    return {"urls": [url], "extract_depth": "advanced", "format": "markdown"}


def _post(url, body, key):
    req = Request(url, data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type": "application/json",
                           "Authorization": f"Bearer {key}"})
    with urlopen(req, timeout=60) as resp:    # nosec - calling the Tavily API
        return json.loads(resp.read())


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--max-results", type=int, default=5)
    s.add_argument("--domain", action="append", dest="domains")
    s.add_argument("--days", type=int, default=None)

    e = sub.add_parser("extract")
    e.add_argument("url")

    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.cmd == "search":
        body, endpoint = build_search_payload(
            args.query, args.max_results, args.domains, args.days), SEARCH_URL
    else:
        body, endpoint = build_extract_payload(args.url), EXTRACT_URL

    if args.dry_run:
        print(json.dumps({"endpoint": endpoint, "payload": body}, indent=2))
        sys.exit(0)

    key = resolve_key()
    print(json.dumps(_post(endpoint, body, key)))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/test_tavily_cli.py -q`
Expected: `5 passed`

- [ ] **Step 5: Full suite green + dry-run sanity**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/ -q`
Expected: all tests pass (≈40+).
Run: `cd ~/Setup && .venv/bin/python dotfiles/claude_skill_cite/scripts/tavily_cli.py search "ai market 2026" --domain gartner.com --days 180 --dry-run`
Expected: JSON with endpoint + payload, no network call.

- [ ] **Step 6: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/scripts/tavily_cli.py dotfiles/claude_skill_cite/scripts/tests/test_tavily_cli.py
git commit -m "feat(cite): Tavily REST CLI (search + extract) with dry-run"
```

---

## Task 12: Translate §6 → `references/sourcing-standards.md`

**Files:**
- Create: `dotfiles/claude_skill_cite/references/sourcing-standards.md`

- [ ] **Step 1: Write the translated, generalized standards**

Translate M2T `slide-creation-standards.md` §6.1–§6.8 (read it at `~/42/Markdowns2Teach/docs/references/slide-creation-standards.md:235-341`) into English. Generalize away Marp/slide specifics into a "citation format profiles" subsection. The file MUST contain these sections with this content:

```markdown
<!-- ABOUTME: Source-verification standard for the /cite pipeline — claim classification, authority tiers, recency, research protocol, corroboration. -->
<!-- ABOUTME: English generalization of Markdowns2Teach slide-creation-standards.md §6. -->

# Sourcing standards

## 1. Claim classification

**Needs a source** (citation marker + a Sources entry):
- Any number: dollar amounts, percentages, growth rates, market sizes, headcounts
- Any named statistic ("X% of companies do Y")
- Any company-specific fact: revenue, valuation, funding, user counts
- Any benchmark result: accuracy scores, error rates, performance comparisons
- Any pricing data: API costs, subscription tiers, price ranges
- Any prediction/forecast: "the market will reach $X by 2030"

**Does NOT need a source:**
- Logical deductions / reasoning (no factual claim)
- Definitions / textbook-level explanations
- Pedagogical framing: metaphors, teaching analogies
- Tool descriptions without statistics
- Discussion questions

**Gray zone — resolve toward sourcing:**
- "Person X says Y" → find where X got it; cite the upstream source, not X
- "It is well known that…" → if there is a number, source it
- "Industry estimates" / "developer estimates" → NOT real sources; replace with a real report/survey or soften the language

## 2. Authority hierarchy (tiers)

| Rank | Source type | Examples |
|------|-------------|----------|
| 1 | Company IR / SEC / government filings | Annual reports, investor docs — audited figures |
| 2 | Peer-reviewed publications | arXiv, NeurIPS, ICML — technical claims/benchmarks |
| 3 | Tier-1 research | Gartner, McKinsey, Stanford HAI, OECD — market/adoption |
| 4 | Tier-1 press | Bloomberg, Reuters, CNBC, FT — news/funding/events |
| 5 | Tier-2 press | TechCrunch, The Verge, Ars Technica — when Tier-1 unavailable |
| 6 | Startup databases | Crunchbase, Sacra, PitchBook — valuations/funding without press |

Tiers 1–4 are auto-approvable; 5–6 (and unmapped) are flagged for review. The machine-readable roster lives in `memory/authority-map.yaml`.

## 3. Recency filter

| Rule | Detail |
|------|--------|
| Hard reject | Source > 2 years old for any AI market/adoption claim |
| Exception | Historical events (AlexNet 2012, Flash Crash 2010) and case law |
| Preference | Source < 6 months when available |
| Conflict | Most recent wins, unless the older source is clearly more authoritative |

(Implemented in `decisions.recency_verdict`: <180d fresh, 180–365d recent, >365d stale, pre-2020 historical-event.)

## 4. Research protocol by claim type

| Claim type | Primary sources | Search strategy |
|-----------|-----------------|-----------------|
| Market size / forecast | Gartner, IDC, Statista, McKinsey, CB Insights | `"[topic] market size 2026" site:gartner.com OR site:statista.com` |
| Company financials | IR pages, SEC filings, Bloomberg | `"[company] revenue 2025" site:investor.[company].com` |
| Adoption / survey stats | McKinsey, Deloitte, Stanford HAI AI Index | `"[stat]" survey 2025 2026` |
| Benchmark results | Original papers (arXiv), HuggingFace | `"[model] [benchmark]" site:arxiv.org` |
| API pricing | Vendor pricing pages directly | go to openai.com/pricing, anthropic.com/pricing |
| Historical events | Reuters, Bloomberg, NYT, court archives | `"[event]" [year] site:reuters.com` |
| EU regulation | EUR-Lex, European Parliament, CEPS | `"EU AI Act [specific provision]"` |

## 5. Source verification

Read the actual page (Tavily Extract / WebFetch / pdftotext) and confirm the figure matches. Never trust search snippets. Every quote and surrounding paragraph stored for a claim MUST appear verbatim in the saved page text (enforced by `validate_claim.py`).

## 6. Corroboration & independence

A claim is stronger when independent sources agree — but copies are not independent.
- **Independence requires two checks**: (a) the saved page texts are not near-duplicates (`textsim.near_duplicate`), and (b) the sources rest on *distinct, stated* underlying origins (different study/dataset/announcement). Origin unknown → independence unconfirmed → corroboration stays `weak`.
- Only **validated, independent** secondaries upgrade a low-tier claim to auto-approvable.
- A validated secondary stating a *different* value → `flag-claim-conflict` (conflict counts even without independence — copies should not disagree).

## 7. Auto-promotion of existing citations

When auditing a document that already has citations, a newer source that is as-or-more authoritative and supports the **same value** replaces the citation automatically (SOURCE SWAP ONLY). Any change to the claim's stated value is always flagged for human review. Value sameness is computed by `validate_claim.values_match`, never by judgment.

## 8. Unsourceable claims

| Action | When |
|--------|------|
| Soften | Replace the exact figure with "about", "on the order of", "several" |
| Remove | Drop the specific stat if the passage works without it |
| Flag | Mark with `<!-- TODO: source needed for [claim] -->` for a decision |
| Never | Invent a source, or cite a secondary that does not contain the actual data |

## 9. Conflict resolution

When sources disagree, prefer the most recent figure from the most reputable source:
**company IR > Bloomberg/CNBC > TechCrunch > Crunchbase**. If a source contradicts the document's figure, update the document to match the best source (flagged for review when the value changes).

## 10. Citation format profiles

The correct phase patches citations using a profile chosen from the document:
- **Marp** (front matter detected): inline `[N]` markers + a per-slide `<small>Sources : [1] [Name](url) · …</small>` footer.
- **Plain markdown**: inline `[N]` markers + a `## Sources` section with a numbered list `1. [Name](url)`.
```

- [ ] **Step 2: Verify it has every section**

Run: `grep -c '^## ' dotfiles/claude_skill_cite/references/sourcing-standards.md`
Expected: `10`

- [ ] **Step 3: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/references/sourcing-standards.md
git commit -m "docs(cite): English generalized sourcing standards (from M2T §6)"
```

---

## Task 13: `cite-diagnose` SKILL.md

**Files:**
- Create: `dotfiles/claude_skill_cite_diagnose/SKILL.md`

This skill = M2T's `cite-scan` generalized + existing-citation audit. Reuse the cite-scan workflow text (claim extraction, slug, stubs, outline, `.scan-hash`) but: resolve bundle dir via `bundle_path.py`, convert non-md input via `convert_input.py`, classify per `references/sourcing-standards.md`, and ADD an existing-citation audit pass. Do NOT skip already-sourced claims — audit them.

- [ ] **Step 1: Write the SKILL.md**

````markdown
---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
description: Phase 1 of /cite — diagnose a document's claims: extract uncited factual claims AND audit existing citations (link health, verbatim quote, tier, recency). Writes a run bundle and stops at a review gate.
---

# /cite-diagnose — Claim & Citation Diagnosis

## Trigger
`/cite-diagnose <path-or-url>`

## Scripts (deployed at ~/.claude/skills/cite/scripts/)
Set `S=~/.claude/skills/cite/scripts` for the commands below.

## Workflow

### Step 1: Resolve input, convert, locate bundle
- If no argument, AskUserQuestion for the target.
- `ROUTE=$(python3 $S/convert_input.py "<target>" --out-dir /tmp/cite-pre 2>/dev/null || echo native)` — informational.
- `BUNDLE=$(python3 $S/bundle_path.py "<target>" --ensure)`
- Convert if needed: `MD=$(python3 $S/convert_input.py "<target>" --out-dir "$BUNDLE" --run)`. For `.md` this returns the original path. Record `converted: true/false` and `converted_md` in the bundle's `meta.yaml`.
- If conversion fails, abort with the converter's stderr and a one-line install hint (`pandoc` / `poppler-utils`).
- Hash the markdown actually analyzed: `sha256sum "$MD" | cut -d' ' -f1 > "$BUNDLE/.scan-hash"`.

### Step 2: Read the rubric
Read `~/.claude/skills/cite/references/sourcing-standards.md` §1 (classification) and §2 (tiers) into context.

### Step 3: Extract claims
Read `$MD`. For each line, classify per §1. Write one stub per claim to `$BUNDLE/claims/claim-NN.yaml`:

```yaml
id: claim-NN
location: {file: "<target>", slide: "<slide num+title if Marp, else section heading>", line: <n>}
claim: {text: "<verbatim>", type: <number|named-stat|company-fact|benchmark|pricing|forecast|historical-event>, has_existing_source: <bool>}
existing_source: null      # filled in Step 4 if has_existing_source
proposed_source: {}        # filled by /cite-remediate
corroboration: {status: not-sought, sources: []}
promote_verdict: null
status: <uncited | pending-audit>   # uncited if no marker; pending-audit if it already has one
flag_reason: null
proposed_action: null
proposed_claim_update: null
validation: null
page_text_file: null
```

Edge cases (carried from cite-scan): ambiguous lines → log to `caveats.md` "Detection-level", do not extract; discussion/Q slides with numbers → skip.

### Step 4: Audit existing citations
For every claim with `has_existing_source: true` (parse the `[N]` → URL from the slide's Sources line/section):
- `python3 $S/link_check.py "<url>"` → set `existing_source.link_status`.
- Fetch the page (WebFetch, or `tavily_cli.py extract`) and save to `claims/claim-NN.existing.page.txt`. Verbatim-check the claim's number against it → `existing_source.quote_found`.
- `T=$(python3 $S/tier_lookup.py "<domain>" --map ~/.claude/skills/cite/memory/authority-map.yaml [--map <project-overlay> --map <run-overlay>])` → `existing_source.authority_tier`.
- `R=$(python3 $S/decisions.py recency "<publication_date or '' >")` → `existing_source.recency_verdict`.
- Set `status`:
  - `link_status==ok` AND `quote_found` AND tier∈1–4 AND recency∈{fresh,recent,historical-event} → `cited-healthy`
  - `link_status!=ok` OR not `quote_found` → `cited-broken`
  - recency==stale → `cited-stale`
  - tier∈{5,6,null} → `cited-low-tier`

### Step 5: Authority overlay (per-run)
As in the legacy scan: identify 2–4 domains, optionally one Tavily search each to surface authoritative orgs, write `$BUNDLE/authority-map.md` overlay proposals. If a project overlay exists (`docs/references/authority-map.yaml` relative to cwd), note its path in `meta.yaml` so later phases pass it to `tier_lookup.py`.

### Step 6: Outline + report
Write `$BUNDLE/outline.md` categorizing claims: Uncited / Cited-healthy / Cited-broken / Cited-stale / Cited-low-tier (counts + table). Initialize `caveats.md` with `## Tool-level`, `## Research-level`, `## Detection-level`.

Report:
```
/cite-diagnose complete for <target>
- <U> uncited · <H> healthy · <B> broken · <St> stale · <L> low-tier
- bundle: <BUNDLE>
Review outline.md, then run /cite-remediate.
```

## Common failure modes
- Target not found / unsupported format → abort, no bundle.
- Bundle already exists → AskUserQuestion overwrite/resume/abort.
- Tavily/key missing in Step 5 → fall back to WebSearch, log to caveats.

## Non-goals (v1)
- Does NOT hunt for new sources (that's /cite-remediate).
- Does NOT edit the document (that's /cite-correct).
````

- [ ] **Step 2: Structural check**

Run: `head -5 dotfiles/claude_skill_cite_diagnose/SKILL.md | grep -q 'user-invocable: true' && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite_diagnose/SKILL.md
git commit -m "feat(cite): cite-diagnose skill (claims + existing-citation audit)"
```

---

## Task 14: `cite-remediate` SKILL.md

**Files:**
- Create: `dotfiles/claude_skill_cite_remediate/SKILL.md`

This skill = M2T's `cite-research` generalized + corroboration + auto-promote. Keep the parallel-subagent / raw-data-only / validator-retry architecture. Orchestrator computes all verdicts via `decisions.py`, `tier_lookup.py`, `value_match.py`, `textsim.py`.

- [ ] **Step 1: Write the SKILL.md**

````markdown
---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, WebSearch, WebFetch, AskUserQuestion
description: Phase 2 of /cite — subagents find sources (raw data only); the orchestrator finalizes tier/recency/status, auto-promote, and corroboration deterministically via scripts. Stops at a review gate.
---

# /cite-remediate — Source Hunting, Auto-Promote, Corroboration

## Trigger
`/cite-remediate [<slug>]`  (no slug → AskUserQuestion lists `~/.local/state/cite/*/` and legacy `docs/citation-audit/*/`)

Set `S=~/.claude/skills/cite/scripts`. Resolve `BUNDLE` for the slug.

## Which claims get worked
- **Source-hunt** (uncited, cited-broken, cited-stale, cited-low-tier).
- **Leave alone** cited-healthy (cost control).
- **Active corroboration hunt** only where it changes a decision: best source tier∈{5,6,null}, forecasts, and auto-promote candidates.

## Step 1: Load state
Read `outline.md`, `meta.yaml` (for project-overlay path + converted_md), and all claim YAMLs needing work.

## Step 2: Dispatch parallel research subagents (batches of 5, `subagent_type: general-purpose`)
Each subagent gets ONE claim and returns **raw data only** — never tier/recency/status/promote. Reuse the anti-fabrication contract from the legacy cite-research prompt verbatim (quote + surrounding_paragraph MUST appear in the saved `claims/<id>.page.txt`; fallback chain Tavily extract → WebFetch → curl+pdftotext; on retry honor `--error-feedback`). Tooling note in the prompt: use `python3 ~/.claude/skills/cite/scripts/tavily_cli.py search "<q>" --domain <d> --days <n>` then `... extract <url>` (falls back to WebFetch).

ADD to the returned fields:
```yaml
proposed_source: { ... same as legacy ... , underlying_origin: <stated study/dataset/org or null> }
corroboration:
  sources:                       # EVERY extra candidate seen — never discard
    - {url, url_domain, publication_date, snippet, underlying_origin, page_text_file: <id>.corrob-K.page.txt or null}
```
Instruct the subagent: if a second source is cheaply available (already in search results), save its page text too as `<id>.corrob-K.page.txt` so the orchestrator can validate it.

## Step 3: Deterministic finalization (orchestrator, per returned claim)
1. **Validate primary**: `python3 $S/validate_claim.py "$BUNDLE/claims/<id>.yaml" "$BUNDLE/claims/<id>.page.txt"`. Exit 1 → retry once with stderr as `--error-feedback`; second failure → `status: flagged-validation-failed`, log to caveats, skip rest.
2. **Tier**: `python3 $S/tier_lookup.py <url_domain> --map ~/.claude/skills/cite/memory/authority-map.yaml [--map <project-overlay> --map "$BUNDLE/authority-map.yaml"]` → `proposed_source.authority_tier`.
3. **Recency**: `python3 $S/decisions.py recency "<publication_date or ''>"` → `proposed_source.recency_verdict`.
4. **Base status**: `python3 $S/decisions.py status <tier|null> <recency>` → status + flag_reason.
5. **Corroboration** (for each saved `corrob-K.page.txt`):
   - validate it the same way (quote findable) → `validated`.
   - independence: `python3 $S/textsim.py "$BUNDLE/claims/<id>.page.txt" "$BUNDLE/claims/<id>.corrob-K.page.txt"` → if `near_duplicate` true → not independent. Also require `underlying_origin` distinct & stated from the primary's. Set `independent: true/false/null`.
   - value: `python3 $S/value_match.py "<claim text>" "<corrob quote>"` → `value_match: true/false`.
   - Set `corroboration.status` per `decisions.corroboration_status` (compute in-orchestrator using the table, or call the module). Then `status = decisions.apply_corroboration(status, tier, secondaries)` — a low-tier claim with an independent validated tier≤4 secondary becomes auto-approved.
6. **Auto-promote** (only for claims that had `existing_source`):
   - `VM=$(python3 $S/value_match.py "<claim text>" "<new quote>")` (match/mismatch/unknown)
   - `python3 $S/decisions.py promote <orig_tier|null> "<orig_date or ''>" <new_tier|null> "<new_date or ''>" <VM> <determinable>` → `promote_verdict`.
   - `auto-promote` → `status: auto-promoted`, `proposed_action: swap-source`.
   - `flag-claim-conflict` → `status: flagged-claim-conflict` (record `proposed_claim_update` with the new value + source for human review).
   - `flag-better-source` → `status: flagged-better-source`.
7. Write the `validation:` block (validated_at, quote_found_in_page, attempts).

## Step 4: Update outline.md
Sections: Auto-approved (incl. auto-promoted, corroboration-upgraded) · Flagged-review (low-rep / claim-conflict / better-source / validation-failed) · Unsourceable. Show corroboration status per claim.

## Step 5: Report + gate
```
/cite-remediate complete for <slug>
- <a> auto-approved (<p> via auto-promote, <c> via corroboration)
- <f> flagged · <u> unsourceable · <conf> conflicts
Edit flagged claims' YAML status to approved|rejected|needs-rework, then run /cite-correct.
```

## Common failure modes
- No `.scan-hash` → "run /cite-diagnose first".
- Tavily rate limit → WebSearch fallback, log each to caveats.
- Subagent error → retry once, else flagged-unsourceable.

## Non-goals (v1)
- Does NOT edit the document. Does NOT re-research needs-rework in the same run. Never auto-changes a claim's value.
````

- [ ] **Step 2: Structural check**

Run: `grep -q 'decisions.py promote' dotfiles/claude_skill_cite_remediate/SKILL.md && grep -q 'textsim.py' dotfiles/claude_skill_cite_remediate/SKILL.md && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite_remediate/SKILL.md
git commit -m "feat(cite): cite-remediate skill (auto-promote + corroboration)"
```

---

## Task 15: `cite-correct` SKILL.md

**Files:**
- Create: `dotfiles/claude_skill_cite_correct/SKILL.md`

This skill = M2T's `cite-apply` generalized. Editable markdown → patch in place (diff gate kept); converted/read-only input → write `corrected.md` into the bundle. Citation format chosen by profile (Marp vs plain). No M2T `make`/`marp` dependency — verification is profile-aware and best-effort.

- [ ] **Step 1: Write the SKILL.md**

````markdown
---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
description: Phase 3 of /cite — patch the document with [N] markers + a Sources footer/section from approved claims. Handles auto-promote source swaps. Offers authority-map promotion gate.
---

# /cite-correct — Apply Approved Citations

## Trigger
`/cite-correct [<slug>]`. Set `S=~/.claude/skills/cite/scripts`; resolve `BUNDLE`, read `meta.yaml`.

## Step 1: Drift check
Re-hash the analyzed markdown (`meta.converted_md` or the original `.md`); compare to `.scan-hash`. Mismatch → abort: "source changed since /cite-diagnose; re-run /cite-diagnose."

## Step 2: Choose output target & profile
- **Editable original markdown** (input was `.md`, file writable): patch in place.
- **Converted/read-only input**: write `corrected.md` into `$BUNDLE` (the deliverable); never touch the non-md original.
- **Profile**: front matter (`marp:` or leading `---` block with `marp: true`) → `marp`; else `plain`.

## Step 3: Partition claims
- **apply**: status∈{approved, auto-approved, auto-promoted}
- **swap-only**: status==auto-promoted (replace existing URL, keep marker)
- **soften**: flagged-unsourceable with proposed_action==soften-language
- **skip**: everything else (rejected/needs-rework/flagged-*)

If apply ∪ soften ∪ swap-only is empty → "nothing to apply", exit.

## Step 4: Number & build patch
Group apply claims by slide/section. Assign `[N]` per group, shared URL shares N, ordered by line.
- Append ` [N]` after each claim sentence.
- `auto-promoted`: replace only the URL in the existing Sources entry (marker unchanged).
- `soften`: replace the claim sentence with `proposed_claim_update`.
- Sources rendering by profile:
  - **marp**: `<small>Sources : [1] [Name](url) · [2] [Name](url)</small>` as the slide's last line before `---`/EOF.
  - **plain**: a `## Sources` section per document section (or one at EOF) with `1. [Name](url)`.

## Step 5: Preview diff gate
Write `$BUNDLE/apply-preview.diff` (`diff -u` original vs patched). Show it; AskUserQuestion Apply / Show-detail / Abort.

## Step 6: Apply
On Apply: edit the target (surgical Edits, not full rewrite). Best-effort verify:
- If profile==marp AND the repo has a Makefile with a `check` target (`grep -q '^check:' Makefile`): run `make check check-citations html` and report pass/fail. (This covers the M2T case unchanged.)
- Else: run `python3 -c "import pathlib,sys; t=pathlib.Path('<target>').read_text(); sys.exit(0 if t.count('[')>=0 else 1)"` as a trivial integrity touch and report "no project build detected; skipped deep verification".
Never revert on failure — report and leave for the user.

## Step 7: Authority promotion gate
For each per-run `authority-map.md` proposal, AskUserQuestion promote yes/no/skip. On yes, append under the right tier in `~/.claude/skills/cite/memory/authority-map.yaml` AND its `.md` mirror — OR, if a project overlay exists and the user prefers, into `docs/references/authority-map.yaml`. After edits: `python3 $S/lint_authority_map.py` must pass; then commit the Setup change:
```bash
cd ~/Setup && git add dotfiles/claude_skill_cite/memory/authority-map.* && git commit -m "chore(cite): promote <N> publishers from <slug>"
```

## Step 8: Final report
Counts of cited / swapped / softened, verification result, promotions, bundle path.

## Common failure modes
- Hash drift → abort.
- Stale line number → abort that claim, apply the rest.
- `make check` overflow (M2T) → report, do not revert.

## Non-goals (v1)
- No auto-retry of build failures. No revert. No claim-value changes without an approved flagged decision.
````

- [ ] **Step 2: Structural check**

Run: `grep -q 'corrected.md' dotfiles/claude_skill_cite_correct/SKILL.md && grep -q 'lint_authority_map.py' dotfiles/claude_skill_cite_correct/SKILL.md && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite_correct/SKILL.md
git commit -m "feat(cite): cite-correct skill (profile-aware patching + promotion gate)"
```

---

## Task 16: `/cite` orchestrator SKILL.md (rewrite for new phases)

**Files:**
- Create: `dotfiles/claude_skill_cite/SKILL.md`

- [ ] **Step 1: Write the orchestrator**

````markdown
---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion
description: Orchestrates /cite-diagnose, /cite-remediate, /cite-correct on any document (md/pdf/html/docx/url) with review gates between phases. Detects existing state and resumes.
---

# /cite — Source Verification Orchestrator

## Trigger
`/cite <path-or-url>`

Set `S=~/.claude/skills/cite/scripts`.

## Step 1: Resolve state
- `BUNDLE=$(python3 $S/bundle_path.py "<target>")` (does not create it).
- Infer phase from bundle contents:

| State | Phase | Action |
|-------|-------|--------|
| BUNDLE missing | not started | Diagnose |
| exists, all claims `uncited`/`pending-audit` | diagnosed | Remediate |
| exists, some claims finalized & some flagged | remediated | prompt Correct |
| `apply-preview.diff` present and applied | done | report nothing to do |

Confirm the inferred phase with AskUserQuestion.

## Step 2: Diagnose
Invoke `/cite-diagnose <target>` via Skill. Then show outline.md summary and ask: proceed to remediate? [yes/no/abort].

## Step 3: Remediate
Invoke `/cite-remediate <slug>` via Skill. Relay the auto-approved/flagged/unsourceable summary and stop:
"Review flagged claims, edit their YAML status, reply 'go' to correct."
Wait. On `go` → Step 4. On `abort` → stop.

## Step 4: Correct
Invoke `/cite-correct <slug>` via Skill. Its diff-preview is the final gate. Relay its report.

## Common failure modes
- Hash drift mid-pipeline → surface the abort; suggest re-running /cite-diagnose.
- User declines after diagnose → leave bundle, exit cleanly.

## Non-goals (v1)
- One document per run (no batching). No parallel phases.
````

- [ ] **Step 2: Structural check**

Run: `grep -q 'cite-diagnose' dotfiles/claude_skill_cite/SKILL.md && grep -q 'cite-correct' dotfiles/claude_skill_cite/SKILL.md && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd ~/Setup
git add dotfiles/claude_skill_cite/SKILL.md
git commit -m "feat(cite): orchestrator for diagnose/remediate/correct"
```

---

## Task 17: Register, deploy, retire old skills, end-to-end verify

**Files:**
- Modify (via CLI only): `dotfiles/dotfiles.json`

- [ ] **Step 1: Set up the Tavily secret (one-time, this device)**

```bash
mkdir -p ~/.claude/secrets
# write the existing key (currently in ~/.claude.json under projects > M2T > mcpServers.tavily)
# into the file, then lock it down:
chmod 600 ~/.claude/secrets/tavily_api_key
```
Verify: `python3 dotfiles/claude_skill_cite/scripts/tavily_cli.py search "test" --dry-run` prints payload (dry-run needs no key); `... resolve` path works once the file exists.

- [ ] **Step 2: Register the four skills via the add-dotfile skill**

Invoke the `add-dotfile` skill for each of: `claude_skill_cite` (→ `~/.claude/skills/cite`), `claude_skill_cite_diagnose` (→ `~/.claude/skills/cite-diagnose`), `claude_skill_cite_remediate` (→ `~/.claude/skills/cite-remediate`), `claude_skill_cite_correct` (→ `~/.claude/skills/cite-correct`). Do NOT hand-edit `dotfiles.json`. If a needed subcommand is missing, build it in `src_dotfiles/__main__.py` first (per the add-dotfile skill).

- [ ] **Step 3: Deploy**

Use the add-dotfile/src_dotfiles deploy path to symlink the four dirs into `~/.claude/skills/`. Verify:
```bash
ls -la ~/.claude/skills/cite ~/.claude/skills/cite-diagnose ~/.claude/skills/cite-remediate ~/.claude/skills/cite-correct
```
Expected: four symlinks resolving into `~/Setup/dotfiles/claude_skill_cite*`.

- [ ] **Step 4: Confirm scripts run from the deployed path**

```bash
python3 ~/.claude/skills/cite/scripts/tier_lookup.py sec.gov
```
Expected: `1`

- [ ] **Step 5: Retire the old, untracked skills**

```bash
rip ~/.claude/skills/cite-scan ~/.claude/skills/cite-research ~/.claude/skills/cite-apply
```
(Use `rip`, never `rm` — recoverable. The new `cite` symlink replaced the old loose `cite` dir during deploy; if the old `~/.claude/skills/cite` was a real dir, `rip` it first, then re-deploy.)

- [ ] **Step 6: Full test suite green**

Run: `cd ~/Setup && .venv/bin/python -m pytest dotfiles/claude_skill_cite/scripts/tests/ -q`
Expected: all pass.

- [ ] **Step 7: End-to-end on a non-M2T markdown doc**

Create `/tmp/cite-e2e.md` with one sourced + one unsourced numeric claim. Run `/cite /tmp/cite-e2e.md` through all three phases. Verify: bundle lands in `~/.local/state/cite/tmp-cite-e2e/`, diagnosis categorizes both claims, remediation finds a source for the unsourced one, correction writes a `## Sources` section (plain profile). Capture the final diff.

- [ ] **Step 8: End-to-end parity on an M2T deck**

Pick one M2T slide deck with existing citations. Run `/cite-diagnose` on it from the M2T repo root. Verify: the project overlay (`docs/references/authority-map.yaml`) is detected and passed to `tier_lookup.py`, existing healthy citations are classified `cited-healthy` (not re-researched), and a legacy `docs/citation-audit/<slug>/` bundle (if present) is reused. Confirm `make check`-style verification still routes in cite-correct when a Makefile `check:` target exists. Do NOT apply edits to the real deck unless Louis approves the diff.

- [ ] **Step 9: Commit any registry/CLI changes**

```bash
cd ~/Setup
git add -A
git commit -m "feat(cite): register + deploy generalized cite skill family; retire legacy scan/research/apply"
```

---

## Self-Review notes (author)

- **Spec coverage:** layered authority map (T2/T3), central state + legacy fallback (T8), conversion (T9), existing-citation audit (T13 Step 4), auto-promote source-swap-only with deterministic value match (T5/T7/T14), corroboration with independence via near-duplicate + distinct origin (T6/T7/T14), Tavily CLI with secret file (T11/T17), translation (T12), Setup deployment + retiring old skills (T17), M2T compatibility (T3 seed, T13 overlay detection, T15 Makefile route, T17 Step 8 parity). All present.
- **Determinism invariant:** every verdict (tier, recency, status, value-match, promote, corroboration, independence) is a tested pure function; subagents never decide.
- **Type consistency:** function names used across tasks — `lookup_layered`, `values_match`, `value_determinable`, `recency_verdict`, `status_for`, `promote_verdict`, `corroboration_status`, `apply_corroboration`, `near_duplicate`, `similarity`, `route`, `convert`, `slug_for`, `bundle_dir`, `verdict`/`check`, `resolve_key`/`build_search_payload`/`build_extract_payload` — all defined in their creating task and referenced consistently.

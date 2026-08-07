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

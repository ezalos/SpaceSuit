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

**Typing historical events:** if a claim states a *settled past event or milestone* — something that happened on a date and will not change (a launch, an announcement, a record, a one-time figure like "ChatGPT reached 100M weekly users in Nov 2023") — set `type: historical-event` even when it's also a company-fact or number. This exempts it from the staleness filter downstream (an old-but-true fact shouldn't flag just for source age). Reserve the other types for *living* figures (current market size, latest revenue, ongoing adoption) that genuinely go stale.

Edge cases (carried from cite-scan): ambiguous lines → log to `caveats.md` "Detection-level", do not extract; discussion/Q slides with numbers → skip.

### Step 4: Audit existing citations
For every claim with `has_existing_source: true` (parse the `[N]` → URL from the slide's Sources line/section):
- `python3 $S/link_check.py "<url>"` → set `existing_source.link_status`.
- Fetch the page (WebFetch, or `tavily_cli.py extract`) and save to `claims/claim-NN.existing.page.txt`. Verbatim-check the claim's number against it → `existing_source.quote_found`.
- `T=$(python3 $S/tier_lookup.py "<domain>" --map ~/.claude/skills/cite/memory/authority-map.yaml [--map <project-overlay> --map <run-overlay>])` → `existing_source.authority_tier`.
- `R=$(python3 $S/decisions.py recency "<publication_date or '' >" --type <claim.type>)` → `existing_source.recency_verdict`. (Forward the claim's type so `historical-event` claims are exempt from staleness.)
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

<!-- ABOUTME: Approved design for generalizing the /cite skill family into a Setup-tracked, project-agnostic source-verification pipeline. -->
<!-- ABOUTME: Covers layout, layered authority map, diagnose/remediate/correct phases, auto-promote rule, tooling, and M2T compatibility. -->

# /cite generalization — source verification on any document

**Date**: 2026-06-10
**Status**: approved (brainstorming complete)
**Origin**: §6 "Citations et sourcing" of `~/42/Markdowns2Teach/docs/references/slide-creation-standards.md` (French), to be translated and generalized. Existing `/cite` + `/cite-scan` + `/cite-research` + `/cite-apply` skills evolve in place.

## Goal

A general-purpose citation pipeline, callable on any document, that:
1. **Diagnoses** — extracts uncited factual claims AND audits existing citations (link health, verbatim quote check, authority tier, recency).
2. **Remediates** — finds sources for uncited/broken/stale/low-tier claims, with optional human feedback at a review gate.
3. **Corrects** — patches the document (in place when editable, else a corrected copy) with citations and approved claim updates, always sourced.

Auto-promote rule: a newer source that is as-or-more authoritative than the original and supports the **same claim value** replaces the citation automatically (source swap only). Any change to claim text is always human-reviewed.

## Decisions made (with alternatives rejected)

| Decision | Chosen | Rejected |
|---|---|---|
| Relationship to /cite | Evolve in place | New parallel skill (duplicate logic); rebuild-then-deprecate (most work) |
| Authority map | Layered: skill-global + project overlay + per-run overlay | Single global (couples M2T to home dir); per-project only (no learning) |
| Auto-promote scope | Source swap only; claim-text changes always flagged | Auto claim updates (full or tier-gated) — changing what the document says is never automatic |
| Tavily access | Bundled CLI script hitting REST API | User-scope MCP (key in plaintext config, absent in headless runs); both (surface area) |
| Input formats | Anything convertible (md, pdf, html, docx, URL) via pandoc/pdftotext | Markdown-only (too narrow); read-only-diagnosis tier (half-measure) |
| Run state | Central `~/.local/state/cite/<slug>/` | Next-to-document (fails on read-only targets); repo opt-in hybrid |
| Structure | 4-skill family: orchestrator + 3 phase skills, renamed | Single mega-skill (huge SKILL.md degrades reliability); Workflow rebuild (loses review gates) |
| Home | Setup repo, dotfiles-tracked, deployed to `~/.claude/skills/` | Staying loose in `~/.claude/skills/` (untracked, single-device) |
| Corroboration | Risk-targeted active hunt + opportunistic capture of extra sources from normal searches; validated secondaries can upgrade status or surface conflicts; cite one primary | All-claims sweep (≈2× cost); opt-in flag (data missing from routine runs); metadata-only (wastes the signal); citing both sources (longer footers) |

## Layout & deployment

```
Setup/dotfiles/
  claude_skill_cite/            → ~/.claude/skills/cite/        (orchestrator)
    SKILL.md
    references/sourcing-standards.md   # English translation of §6, generalized
    scripts/
      tavily_cli.py             # search + extract subcommands, Tavily REST API
      validate_claim.py         # verbatim quote validator (ported from M2T)
      tier_lookup.py            # layered-map aware (ported + extended)
      link_check.py             # HTTP health for existing citations (new)
      lint_authority_map.py     # ported
    memory/authority-map.yaml   # skill-global map, seeded from M2T's, versioned in Setup git
    tests/                      # pytest: ported + new
  claude_skill_cite_diagnose/   → ~/.claude/skills/cite-diagnose/
  claude_skill_cite_remediate/  → ~/.claude/skills/cite-remediate/
  claude_skill_cite_correct/    → ~/.claude/skills/cite-correct/
```

- Registration via the `src_dotfiles` CLI only (never hand-edit `dotfiles/dotfiles.json`); use the add-dotfile skill flow.
- Phase skills renamed: scan→diagnose, research→remediate, apply→correct. Old dirs removed from `~/.claude/skills/` with `rip` after deployment.
- Shared scripts live in the orchestrator dir; phase skills reference them via deployed path `~/.claude/skills/cite/scripts/`.
- Tavily API key: `~/.claude/secrets/tavily_api_key` (per-device, chmod 600, NOT in git — same philosophy as per-device Telegram bots). CLI reads `TAVILY_API_KEY` env var first, then the file. Missing key → clear stderr message with one-line setup instruction.

## Data model

### Layered authority map (later layer wins on domain conflict)
1. Skill-global `memory/authority-map.yaml`
2. Project overlay: `docs/references/authority-map.yaml` in the target document's repo, if present (M2T case)
3. Per-run overlay in the bundle

`tier_lookup.py` accepts repeated `--map` flags; stdout stays `1-6` or `null`.

### Run bundle — `~/.local/state/cite/<slug>/`
Same shape as today: `outline.md` (diagnosis report), `claims/*.yaml`, `claims/*.page.txt`, `.scan-hash`, `caveats.md`. Plus `converted.md` when input needed conversion and `corrected.md` when the original is not editable. `/cite` also globs legacy `docs/citation-audit/` locations so in-flight M2T bundles remain resumable.

### Claim YAML — new fields for existing citations
```yaml
existing_source:
  url: ...
  last_verified: <ISO date>
  authority_tier: <1-6 | null>
  recency_verdict: <fresh | recent | stale | historical-event | unknown>
  link_status: <ok | dead | redirect-suspect>
  quote_found: <true | false>
promote_verdict: <auto-promote | flag-better-source | flag-claim-conflict | keep | null>
corroboration:
  status: <confirmed | weak | uncorroborated | conflicting | not-sought>
  sources:                      # secondary sources beyond the primary
    - url: ...
      url_domain: ...
      publication_date: <date | null>
      authority_tier: <1-6 | null>
      validated: <true | false> # true = full extract + verbatim quote check passed
      quote: <verbatim quote | null>   # only when validated
      snippet: <search snippet>        # always kept, even unvalidated
      underlying_origin: <org/study the article reports on, or null if unstated>
      independent: <true | false | null>  # null until checked; requires dedup pass + distinct stated origin
```

## Phase behavior

### /cite-diagnose `<path>`
- Accepts `.md`, `.pdf`, `.html`, `.docx`, URLs. Non-markdown converted into the bundle (pandoc / pdftotext); claim locations track the conversion. Conversion failure → abort with instruction.
- Extracts **uncited claims** (current scan logic; classification per `sourcing-standards.md`).
- Audits **existing citations**: `link_check.py`, fetch page, verbatim quote check via `validate_claim.py`, tier lookup, recency verdict.
- Output: diagnosis report categorizing every claim — `uncited / cited-healthy / cited-broken / cited-stale / cited-low-tier`. Human review gate before remediation.

### /cite-remediate `<slug>`
- Source-hunts for `uncited`, `cited-broken`, `cited-stale`, `cited-low-tier`. **Healthy fresh tier-1–4 citations are left alone** (cost control).
- Architecture unchanged: parallel subagents return raw data only; orchestrator computes tier/recency/status deterministically; validator failure → one retry with error feedback, then flag.
- **Auto-promote rule** (deterministic, orchestrator-side):
  - new source more recent (both dates known) AND tier as-good-or-better (numerically ≤ original) AND value match confirmed → `auto-promote` (source swap only)
  - *Value match is computed by `validate_claim.py`, not subagent judgment*: the numeric value(s) in the claim text must appear in the new source's verbatim quote after conservative normalization (thousands separators, %/pts, $/€ symbol stripping). Unit-scale conversions (billion↔trillion) and anything ambiguous → no match → flag.
  - supports a different value → `flag-claim-conflict` with proposed claim update + source — never automatic
  - any date unknown, or worse tier → flagged for review
- Human review gate on flagged items (edit claim YAML statuses), as today.
- **Corroboration** (risk-targeted + opportunistic):
  - *Opportunistic capture*: research subagents never discard extra candidate sources surfaced by normal searches — every plausible secondary is recorded in `corroboration.sources` with url/domain/date/tier/snippet, unvalidated. Free signal, no extra search cost.
  - *Active hunt* (a deliberate 2nd full extract + validation) only where it changes decisions: best source is tier 5–6/unmapped, predictions/forecasts, and auto-promote candidates.
  - *Status semantics*: `confirmed` = ≥1 secondary passed full verbatim validation AND is independent (below); `weak` = unvalidated secondaries, or validated but independence unconfirmed; `conflicting` = a validated secondary supports a different value. Only **validated, independent** secondaries can upgrade.
  - *Independence ≠ different publisher.* Syndicated wire stories and press-release rewrites are the same source in different clothes. Two checks, both must pass:
    1. **Near-duplicate detection (deterministic)**: compare the saved page texts (primary vs secondary) with a text-similarity check (e.g. difflib/shingle ratio on the supporting paragraphs); near-identical wording → same origin → not independent.
    2. **Underlying origin (subagent-reported, conservative default)**: the subagent records `underlying_origin` — the study/dataset/report/announcement the article is based on (usually stated: "according to Gartner…", "a McKinsey survey…"). Upgrade requires the two origins to be *distinct, stated* orgs or distinct original work (e.g. two separate surveys). Origin unknown or unstated → independence unconfirmed → `weak`, no upgrade.
  - *Effect on decisions* (deterministic, orchestrator-side): tier-5/6 primary + validated **independent** tier ≤4 secondary → auto-approvable instead of flagged. Validated conflicting value → `flag-claim-conflict` with both quotes shown (a conflict is meaningful even without independence — same-origin sources shouldn't disagree). Auto-promote candidates whose validated secondary disagrees → flagged, never promoted. Unvalidated snippets never change status — at most a "possible conflict" note in the report.
  - Citation output lists one primary source; corroboration lives in the YAML and diagnosis report.

### /cite-correct `<slug>`
- Editable markdown original → patch in place, diff-preview gate kept. Converted/read-only input → write `corrected.md` in the bundle as the deliverable.
- Citation format by **profile**:
  - Marp front matter detected → `[N]` inline + `<small>Sources</small>` footer per slide (current M2T behavior)
  - Plain markdown → `[N]` inline + `## Sources` numbered list section
- Approved claim-text updates applied here, each with its new source.
- Ends with the authority-map **promotion gate**: new publishers promote into the skill-global map, or the project overlay when one exists (ask which).

### /cite `<path>` (orchestrator)
State detection + resume as today, phase names updated; surfaces hash-drift aborts; checks both central and legacy bundle locations.

## Tooling principles

- All deterministic decisions (tier, recency, status, auto-promote) live in scripts/orchestrator math — **never in subagent judgment**. This separation is the pipeline's core reliability property; the auto-promote rule follows it.
- `tavily_cli.py`: `search` (query, site filters, date window → JSON) and `extract` (URL → markdown, advanced depth). `--dry-run` flag for tests. Fallback chain in subagents stays: tavily extract → WebFetch → curl+pdftotext.
- Anti-fabrication contract unchanged: quote + surrounding paragraph must appear verbatim in saved page text or the claim is rejected.

## Translation

§6 translated to English and generalized into `references/sourcing-standards.md`: claim classification (needs source / doesn't / gray zone), 6-tier authority hierarchy, recency filter (2-year hard reject for market/adoption claims, historical exceptions, <6-month preference, conflict resolution), research protocol per claim type, source verification (read the real page, never trust snippets), unsourceable ladder (soften → remove → flag → never invent). Marp-specific formatting moves into the correct-phase profile description. The M2T French doc is untouched (later slimming to point at the skill is out of scope).

## M2T compatibility

- M2T `scripts/cite/` and Makefile targets unmodified and keep working.
- M2T `authority-map.yaml` auto-detected as project overlay; its entries seed the skill-global map (deduped).
- Behavior on M2T decks must match the old pipeline (verified end-to-end before completion).

## Error handling

Carried over: hash drift abort, Tavily rate-limit → WebSearch fallback + caveat, validator double-failure → flag, subagent timeout → retry once then flag. New: conversion failure → abort with instruction; missing API key → actionable error; auto-promote date ambiguity → always flag.

## Testing

- pytest from Setup: ported validator/tier tests from M2T; new tests for layered lookup precedence, the auto-promote decision table (recency × tier × value-match matrix), link_check verdicts, and corroboration status/upgrade rules (validated vs unvalidated, independence: near-duplicate detection on syndicated copies, distinct vs unstated underlying origins, conflict detection).
- End-to-end verification: one real M2T deck (diff against old pipeline behavior) + one non-M2T markdown document.

## Non-goals (v1)

- No batching across files; one document per run.
- No automatic claim-text changes, ever.
- No re-research of `needs-rework` claims within the same run.
- No slimming of the M2T French standards doc (follow-up).
- No dedup of M2T's `scripts/cite/` against the skill's scripts (follow-up if drift hurts).

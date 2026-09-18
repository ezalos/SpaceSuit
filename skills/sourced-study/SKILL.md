---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent, Workflow, WebFetch, WebSearch
description: Use when Louis wants MANY items studied to the same depth with sources he can check - "compare every robotics benchmark", "report on the arms I could buy", "catalogue these model families", "what are the datasets in X and how saturated are they", "make me a study of Y". Builds a study dir (one sourced record per item, a receipt on every fact), a validator that refuses unsourced records, a fill-verify-fix agent loop, and a page on social.develle.fr. NOT for a single question (/study) and not for mining existing material for post ideas (/seed).
---

# sourced-study

A study answers a fixed set of questions about many items, on one page, where every fact carries a
receipt: a verbatim quote, the source and section, and a highlighted screenshot of that spot. It is
how the robotics datasets study was built (45 items, one record each).

## Read first, in this order

1. `~/42/social/docs/how-to-build-a-sourced-study.md` - the method, the order to do things in, and
   the defects that cost time. This skill does not repeat it.
2. `~/42/social/studies/2026-09-14-robotics-datasets/SCHEMA.md` - a worked record contract.
3. `~/42/social/CLAUDE.md`, section "Studies" - the standing rules.

## The shape

    studies/<date>-<slug>/
      taxonomy.yaml    families and order (the page structure)
      SCHEMA.md        the record contract every research agent reads first
      items/<key>.yaml one record per item
      glossary.yaml    terms with sources; the page links the first occurrence
      research.json    running / queued, so the page says what is ready
      status.json      written by validate_all
      prov/            receipt screenshots (.jpg)
      samples/<key>/   one real pulled sample per item
      papers/          PDFs (gitignored; the record keeps the fetch command)
      leads/           scoping documents, marked unverified secondary, never cited

The tools in `~/42/social/studies/tools/` are subject-independent: `fetch_paper.py`,
`pdf_highlight.py`, `pull_sample.py`, `validate_item.py`, `validate_all.py`, `clarity_check.mjs`,
`episode_stats.py`, `workflow.mjs`. The renderer `~/42/social/dashboard/lib/study.mjs` builds any
study dir that has `taxonomy.yaml` and `items/`.

## Workflow

1. **Questions.** Agree the questions with Louis and write them in reading order. They become the
   record sections and the page sections. Ask what he will want to know that is not obvious; a
   question added later costs a re-run of every record.
2. **Contract.** Write `SCHEMA.md` with a full example record, then the `validate_item.py` rules for
   those fields, with a test per rule. No agent runs before the validator exists.
3. **Two by hand.** Fill two items yourself, render them, look at the screenshots, fix the page.
4. **The loop.** `workflow.mjs`: a research agent fills a record, a cheaper verifier re-opens every
   receipt with no edit rights, a fix agent repairs what it rejected. One item per workflow run, and
   at most the number of concurrent research agents Louis allows (he sets it; it has been 2 and 3).
   Pass `resume: true` after any interruption.
5. **Deploy as they land.** `make dashboard-check` (look at the screenshots), then
   `make dashboard-deploy`, then rewrite `reviews/study-<slug>.md` so the Review tab says what
   changed and what is ready.
6. **Glossary pass** once the records exist, then the synthesis: cross-check the verdicts against
   each other, write the study's findings, and keep a list of what is worth a post.

## Guardrails that are not optional

- The writer never verifies itself. The verifier gets no edit rights.
- A fact with no receipt is a red `?` on the page, never a hidden gap.
- Leads (deep-research reports, a colleague's summary) are never a citation: re-find every number.
- Downloads go through the rate-capped tools, one at a time, 50 MB/s.
- Prose passes the clarity gate: at most 25 words a sentence, one meaning per word, no hedges.
- No em-dash anywhere. `rip`, never `rm`.
- Papers belong in the library: `~/42/Research/CLAUDE.md` and the `gdrive-sync` skill.

## Observability

Universal baseline: CRITICAL on abort; WARNING on a user correction, fallback or retry; INFO on any
feedback during the run and on an edge-case path.

| Level | Trigger | Message |
|---|---|---|
| CRITICAL | the validator or the render check cannot run (tooling broken) | `sourced-study: <tool> failed: <tail>` |
| WARNING | a research agent dies (rate limit, API error) and is relaunched | `sourced-study: <key> research died (<why>); relaunched with resume` |
| WARNING | Louis corrects the scope, the cap or a field | `feedback: '<paraphrase>'; phase=<where>; changed <what>` |
| INFO | a record is verified and deployed | `sourced-study: <key> verified (<n> receipts); deployed` |
| INFO | an item has no public sample and falls back to the paper's figure | `sourced-study: <key> sample fallback: <why>` |

```
claude-log sourced-study INFO "sourced-study: libero verified (34 receipts); deployed"
```

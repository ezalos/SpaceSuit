---
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Agent
description: Independent fact audit of a drafted post before it can be scheduled. Triggered by /fact-audit [draft path], and run by /post before Step 7.9. An agent with no edit rights re-reads every statement the reader will see (tweet text, card cells, video overlays, image captions) against the saved source pages, and grades each one, hunting above all for entity drift (a fact true of a variant, predecessor, baseline or the lab's overall stack printed as true of the named model). Writes the audit and a facts_verified verdict into the run record; the publish gate refuses to schedule without a pass.
---

# /fact-audit: an independent check of every published statement

## Why this exists

A post once said a model "never saw a robot". The lab's report said that only of its research
checkpoints; the production model behind the number printed next to it used an undisclosed recipe.
The post's own research record held the caveat. Every quote on the post was verbatim. A verbatim quote
proves the words exist on the page, not that they describe the model on the card, and the writer who
dropped the caveat could not see that they had. So the check is done by someone else, from the rendered
output, with no way to fix what it finds.

## Rules that make the audit independent

1. **The auditor never wrote the post.** Dispatch a fresh agent (Agent tool). Never audit in the session
   that drafted the post, and never in the writer's own words.
2. **No edit rights.** The agent may write exactly one file: the audit report. It reports; it never fixes.
3. **It sees what the reader sees, plus the evidence, and nothing else.** Give it the rendered
   statements (the draft's tweets, the card and video data files, image captions) and the saved
   source pages (`page-*.txt`). Never the writer's research notes, ledger summaries or chat context:
   those carry the writer's framing, which is what we are testing.
4. **The model is chosen for care, not price.** Use a capable model; this is the last line before publishing.

## Steps

1. **Locate.** From the draft path, read its run record (`$SOCIAL_HOME/runs/<stem>-run.yaml`): `facts.cards`,
   `facts.videos`, `visual.per_tweet`, and the ledger path `facts.ledger` (only to find the pages
   directory; the ledger itself is NOT given to the auditor).
2. **Dispatch the auditor** with the prompt below, filled in.
3. **Read the report.** If any finding is not SUPPORTED, the audit fails. Show Louis the findings,
   most severe first; the writer fixes them; then run a NEW audit (a fresh agent), never a re-grade of the old one.
4. **Record the verdict** in the run record with `bin/runrecord.py`:
   `facts_verified: {date, by: "<model> fact-audit", statements: N, findings: M, pass: <M == 0>, report: <path>}`.
   ```bash
   uv run --with pyyaml python -c "import importlib.util as u; s=u.spec_from_file_location('rr','bin/runrecord.py'); m=u.module_from_spec(s); s.loader.exec_module(m); m.set_field('runs/<stem>-run.yaml','facts_verified',{...})"
   ```
5. **Dashboard.** The post's banner lists `fact audit` as missing until the pass is recorded.

## The auditor prompt (fill the <...>)

> You are an independent fact auditor with NO edit rights. You may write ONE file:
> `<pages dir>/audit-<YYYY-MM-DD>.md`. Do not modify anything else, do not commit, never use `rm`, never print secrets.
>
> **What the reader sees** (read all of it): tweet text `<draft path>` (every numbered tweet);
> cards `<card data files>` (every `v`, `d`, `label`, `cap`, `sub`, `title`, `hint`, `note`, schema
> slot, image caption); video `<video data files>` (bars, quote, num, cap), and which clip or photo
> plays where (`<credits file>` says what each really shows).
> **The only evidence:** the saved pages `<pages dir>/**/page-*.txt`.
>
> For EACH statement output a row: `id | where | statement | entity named | entity/variant the source
> sentence actually describes | verdict | verbatim source quote (+ file:line) | fix`.
> Verdicts: SUPPORTED; SCOPE-MISMATCH (true of another entity: a variant, checkpoint, production vs
> research model, predecessor, baseline, the lab's overall stack, an early build); OVERSTATED (the source
> is narrower or hedged); UNSUPPORTED (no source sentence); CONTRADICTED; IMPLIED-BY-LAYOUT (each part
> true, but the layout pairs them so a reader infers something false, e.g. one variant's number beside
> another variant's data).
> Search the pages actively for sentences that CONTRADICT or NARROW every absolute claim (only, zero,
> never, none, no, all, every, entirely, same weights). Assume a lab's report describes several entities
> until you have read it end to end.
> End with: (1) the count per verdict and the total statements, (2) every non-SUPPORTED row, most severe
> first, (3) for each model, which variants or versions the sources describe and which one each claim
> should be scoped to. A missed row is the failure we are hunting.

## What this does not replace

- `make facts` (mechanical: ledger scope, verbatim quotes, every cell citing a fact) runs first and must pass.
- `/fresh-eyes` checks whether a cold reader understands the post, not whether it is true.
- The `/post` citation hard-check checks that each claim has a healthy, tiered source.

---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent, WebFetch
description: Mine raw material (a directory, file, URL, or video) for X/LinkedIn post ideas in Louis's voice. Triggered by /seed x <source> or /seed linkedin <source>. Recons the source, fans out subagent miners (one per topic vein) under a hard "non-obvious to an expert peer" bar with verbatim source anchors and citations, curates a ranked shortlist, and writes seed files to $SOCIAL_HOME/seeds/ that /post drafts and /visual illustrates. Use this whenever Louis wants post ideas pulled from existing material — "find me something to post from <dir/doc/link/video>", "what could I post from my teaching notes", "mine this for X content", "any good posts hiding in here" — even when he names a source without saying the word "seed".
---

# /seed — Post-idea miner

Turn a pile of raw material into a ranked shortlist of post ideas worth drafting. The whole
point is leverage: Louis has far more good material than time to notice it. This skill reads
the material the way a sharp peer would, finds the few genuinely non-obvious ideas, and
hands each one off as a ready-to-draft seed.

## Trigger
`/seed x <source>` or `/seed linkedin <source>` — where `<source>` is a directory, a file,
a URL, or a video link.

## Inputs

- `$SOCIAL_HOME` env var (fallback: `$HOME/social`, warn)
- Platform arg: `x` or `linkedin`
- `<source>`: a path to a dir/file, an http(s) URL, or a video link (YouTube/X/Vimeo/etc.)
- `$SOCIAL_HOME/voice/<platform>.md` (required — it defines the bar, the audience, the
  format, and the citation rule; error if missing and point to `/audit <platform>`)

## Workflow

### Step 1: Resolve paths and read the voice

Read `$SOCIAL_HOME`. Verify `voice/<platform>.md` exists; if not, stop and suggest
`/audit <platform>`. Read it fully — its **Content** lanes and **What to avoid** set what
counts as a good idea, and its **Citations** section sets the sourcing rule every seed
inherits. `seeds/` may not exist yet; create it when you write.

### Step 2: Confirm the bar (the audience is the whole game)

A "good idea" is defined entirely by who it's for. The default target reader, learned from
real sessions, is **an AI expert in a *different* subfield** (CV, RL, classical ML) who is
sharp but not at the frontier of Louis's focus areas (LLM systems, agents, harness, eval,
context/memory). The bar an idea must clear:

> Reject anything obvious to anyone who has built one agent / one RAG pipeline / knows
> train-test splits. Keep only ideas with a **specific mechanism, a named failure mode, a
> concrete number, or a counter-intuitive design choice** — and that a strong peer from an
> adjacent field would not already hold. Each must land in **1-3 tweets / a tight post**
> with **low context setup** — no primer required to reach the payoff.

If Louis named a different audience or angle in his request, use that instead. If the source
topic is far from the default focus, restate the bar you'll apply in one line and continue
(don't block on it unless it's genuinely ambiguous).

### Step 3: Ingest the source (by type)

Detect the source type and get to clean text. Keep recon cheap before spending compute.

- **Directory** — map it first. Find human-authored text, ranked by density:
  ```bash
  find <dir> -type f \( -name '*.md' -o -name '*.txt' \) \
    -not -path '*/node_modules/*' -not -path '*/dist/*' -not -path '*/outputs/*' \
    -not -path '*/build/*' -not -path '*/.git/*' -printf '%s\t%p\n' | sort -rn | head -40
  ```
  The biggest authored files are usually the richest. Group them into **3-5 topic veins**
  aligned to the bar (e.g. harness, memory, eval, agent-design). Skip generated/vendored
  trees — they're noise.
- **File** — read it. If long, split into 2-4 veins by section/theme.
- **URL** — fetch with WebFetch (or `python3 ~/.claude/skills/cite/scripts/tavily_cli.py
  extract <url>` for a clean text dump). Treat sections/themes as veins.
- **Video** — try a transcript (`yt-dlp --skip-download --write-auto-sub --sub-format vtt
  -o '%(id)s' <url>` then read the `.vtt`). If that fails, ask Louis to paste the transcript
  or 3-5 key points (mirrors how `/post` handles video). Theme the transcript into veins.

If the source is thin (one short doc, one vein), skip the fan-out and mine it directly in
Step 4 yourself — don't spawn subagents for a single page.

### Step 4: Fan out one miner per vein

For each vein, dispatch a subagent (Agent tool) so the deep read stays out of the main
context and the veins run in parallel. Give every miner the SAME strict contract — the bar
from Step 2, the exact files/text for its vein, and this output schema. Ask each for its
**top 3** seeds only; a ruthless filter beats volume.

Miner prompt template (fill the bracketed parts):
```
You are mining material for <platform> post ideas about <VEIN>.
Read in full: <file paths or pasted text for this vein>.

THE BAR: <paste the Step 2 bar verbatim>. The reader is <audience>. Reject the obvious.
Keep only ideas with a specific mechanism, named failure mode, concrete number, or
counter-intuitive design choice that a strong peer from an adjacent field wouldn't already
hold. Each must fit 1-3 tweets / a tight post with low context setup.

Return your TOP 3 seeds, each EXACTLY:
### Seed N: <5-8 word handle>
- Insight: <the specific concrete claim, 1-2 sentences>
- Why it teaches a peer: <why an adjacent-field expert wouldn't already know this>
- Source: <path/section + a short VERBATIM quote anchoring it>
- Post shape: <rough 1-3 tweet draft, lead with the claim, obey voice/<platform>.md>
- Visual brief: <one line: what to depict + likely template: chart|compare|flow|card>
- Citation: <does it rest on an external fact? if so, name the source from the doc>
- Confidence: <high/med/low that it's correct AND non-obvious to a peer>
Be ruthless — 3 strong beats 6 mediocre. Your whole reply IS the data; no preamble.
```

Tell Louis what you launched (the veins) and end your turn; you'll be notified as miners
finish. Filter out any miner that returns null/garbage.

### Step 5: Curate across all seeds

You now have ~3 × (number of veins) seeds. Curate to the strongest **5-6**:
1. **Dedup** — collapse seeds that are the same insight from two veins; keep the richer one.
2. **Rank** by: concreteness (a hard number/mechanism) × instinct-inversion (does it flip a
   belief the peer holds?) × citability (a real, nameable source) × low context cost.
3. **Drop** anything a one-agent/one-RAG builder already knows, and anything whose only
   support is a vibe. Carry each seed's confidence + citation caveats forward honestly
   (flag technical-report-only or leak-sourced claims — they're citation minefields).

### Step 6: Write the seed files

Write each curated seed to `$SOCIAL_HOME/seeds/<platform>-NN-<slug>.md` (NN zero-padded,
continuing from any existing seeds). Use this format — `/post` reads the post shape, `/visual`
reads the visual brief, and the citations are what `/post`'s citation hard-check will verify:

```markdown
---
id: <platform>-NN
slug: <kebab>
platform: <x|linkedin>
topic: <short topic>
status: backlog            # set to "selected" for any Louis picks in Step 7
visual_status: needs-brainstorm
confidence: <high|med|low + caveat>
source:
  - <path/url + section>
citations:
  - '<named source — title, author, id/url>'
---

# <handle>

**Insight:** …
**Why it teaches a peer:** …
**Post shape:** <1-3 tweet / tight-post draft>
**Source anchor (verbatim):** "…"
**Visual brief:** <what to depict + likely template: chart|compare|flow|card>
**Citation notes:** <what goes in the reply/first-comment; any caveat>
```

### Step 7: Deploy the dashboard, then present the shortlist

**Louis reviews on social.develle.fr, often away from home — anything not on the dashboard
is invisible to him.** Before presenting: put every reviewable artifact on the dashboard,
rewrite `dashboard/REVIEW.md` (what's new + `look_at:` tab + the decision needed — it
renders as the landing banner), and run `make -C $SOCIAL_HOME dashboard-deploy`. Seeds
render automatically; source screenshots render via a `screenshots:` frontmatter list
(repo-relative paths) on each seed. Never end the turn with the dashboard stale.

Show Louis the ranked shortlist — for each: handle, the one-line insight, the lead line, the
number, the citation tag, and confidence. Recommend the top 1-2 to draft first and say why.
Ask (AskUserQuestion) which to mark `selected`; update those files' `status`.

Then point the way: **`/post <platform>`** drafts a seed's text (its Post shape is the
starting point; the citation gate sources every factual claim) and **`/visual <platform>`**
turns the Visual brief into the image via its propose-2-3-and-pick flow. `/seed` does not
draft or render — it finds and frames. A selected seed's `id` flows forward as the run
record's `source` when `/post` drafts it, tying the drafted post back to this seed for the
dashboard.

### Step 8: Offer to commit

```bash
cd $SOCIAL_HOME && git add seeds/ && git commit -m "feat(seeds): mine <platform> ideas from <source>"
```
Ask Louis before running git.

## Common failure modes

- **`voice/<platform>.md` missing**: stop, suggest `/audit <platform>`.
- **Source is generated/vendored noise** (a repo that's mostly `node_modules`/`outputs`):
  the density-ranked find already filters these; if nothing authored remains, say so.
- **Video transcript unavailable**: ask Louis to paste it; don't guess content.
- **Every seed clears only the "interesting to a layperson" bar**: that's under-filtering —
  re-run the bar; the audience is an expert peer, not a general reader.
- **Thin source**: skip the fan-out, mine inline; don't spawn a subagent for one page.

## Non-goals

- Does NOT draft post text (that's `/post`) or render visuals (that's `/visual`).
- Does NOT scrape platforms or publish anything.
- Does NOT touch `voice/<platform>.md` (that's `/audit`) or `data-store.yaml` (that's `/log`).

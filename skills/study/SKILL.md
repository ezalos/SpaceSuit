---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent, WebFetch, WebSearch
description: Curiosity-driven co-study where Claude LEADS. Louis names a subject he's curious about; Claude drives a sourced exploration — presenting verified facts, ORIGINAL source figures, and sharp non-obvious insights, and ALWAYS teeing up the next question / one-step-ahead / outside-the-box angle — iterating until his curiosity is satisfied, saving the whole sourced study as a reusable artifact, then distilling the strongest insight into an X post. Triggered by /study <topic>. Use whenever Louis wants to deeply UNDERSTAND something with you rather than get a quick answer — "let's study X", "I'm curious about Y", "dig into Z with me", "teach me about W and where it's heading", "co-study this" — especially when the goal is understanding plus a potential post, not a one-off answer.
---

# /study — Curiosity-driven co-study (Claude leads)

Louis has more curiosity than time to chase it. This skill turns a spark of curiosity into a
deep, sourced, reusable understanding — and then a post. **You lead**: you bring the facts,
the real figures, the insights, and the next question. The research is the point; the post is
the residue.

## Trigger
`/study <topic>` — where `<topic>` is whatever Louis is curious about.

## Inputs
- `$SOCIAL_HOME` env var (fallback `$HOME/social`)
- The topic / question (Louis's curiosity)
- `$SOCIAL_HOME/voice/<platform>.md` (read when the study turns into a post — the distill step)
- Sourcing engines: WebSearch/WebFetch, the deep-research harness (`Workflow` name `deep-research`
  for breadth), and the cite scripts (`~/.claude/skills/cite/scripts/`) for verification

## Core stance: YOU lead
This is not Q&A where you wait to be asked. Every round you PROACTIVELY bring new sourced
knowledge, name the non-obvious insight, and propose where to look next. Think one step ahead
of Louis's question, and outside the obvious box. Pull him forward; let him steer.

## Workflow

### Step 1: Frame the curiosity
Restate the specific angle of curiosity in one line and confirm the frontier — what does Louis
want to *understand*, and why. Ask at most ONE sharp clarifying question, only if genuinely
ambiguous; otherwise dive in. Create the study file now
(`$SOCIAL_HOME/studies/<YYYY-MM-DD>-<slug>/study.md`) and append to it as you go — the search
must be saved so it's reusable, never trapped in the chat.

### Step 2: The co-study loop (you lead — repeat until satisfied)
Each round, bring all four:
- **Sourced facts (2-4).** Concrete, checkable claims from PRIMARY sources. Verify each —
  WebFetch the source, or the cite scripts (`tavily_cli.py search/extract`, `validate_claim.py`,
  `tier_lookup.py`); for breadth, dispatch the deep-research harness. Each claim carries its
  source + a confidence, and you flag fact vs inference vs speculation honestly.
- **Real figures.** When a claim has a published figure, DOWNLOAD the original (the source's
  real PNG / the figure's download button) into the study dir with provenance + license.
  NEVER hand-build a chart with invented values to "illustrate" a fact — see
  [[post-visuals-use-original-source-figures]]. The figure IS the fact.
- **Insight (1-2).** The non-obvious "so what": the tension, the reframe, the mechanism, the
  implication one step ahead. Facts alone aren't the value — the insight is.
- **The next question(s).** ALWAYS close the round by pushing the frontier: the outside-the-box
  angle, the thing one step ahead, the question that would deepen understanding. Propose the
  next direction; don't wait to be asked.
Fold Louis's reaction ("go deeper on X" / "what about Y" / "satisfied") into the next round.
Append each round's facts, figures, insights, and open questions to the study file.

### Step 3: Sourcing + honesty discipline
- Prefer primary sources; verify every factual claim; record URL + authority tier + confidence.
- Distinguish measured from estimated, fact from forecast, consensus from one paper. If a
  widely-cited number is asserted-not-derived (or is an estimate), say so — that gap is often
  the most interesting finding.
- Figures are originals from the source, with license. No fabricated data.

### Step 4: The saved study (the reusable artifact)
Maintain `studies/<date>-<slug>/study.md` as the durable record:
```markdown
---
topic: <short>
question: <the curiosity>
status: open            # -> satisfied when Louis is done
platform: <x|linkedin|none-yet>
created: <date>
updated: <date>
---
## Curiosity
<the question + why it matters>
## Findings          # verified claims
- claim / source (url, tier) / confidence / caveat
## Figures           # real, downloaded, with provenance
- <file in this dir> — <source url> — <license>
## Insights          # the sharp, non-obvious takeaways
## Open questions     # what's still unresolved / worth chasing
## Thread            # short trace of how the understanding developed
```
This is reusable: future studies and posts draw on it, and it surfaces on the review dashboard.

### Step 5: Satisfy check
Keep going while Louis is engaged; don't force closure, don't pad once he's done. When he says
his curiosity is satisfied, set `status: satisfied`.

### Step 6: Distill -> X (the hand-off)
When Louis is ready — he calls it, OR you proactively flag "there's a strong post hiding here"
(and let him decide) — pick what's most worth sharing:
- The single sharpest, most non-obvious insight (what an expert peer wouldn't already hold).
- The hook (from `voice/<platform>.md` patterns).
Then hand to **`/post <platform>`**, seeded from the study: its verified claims feed `/post`'s
citation gate, and its real figures feed `/visual` (originals, never fabrications). The study
is the source of truth; the post is the residue.

### Step 7: Save + surface
The study persists in `studies/`. Re-deploy the dashboard (`make -C $SOCIAL_HOME dashboard-deploy`)
so it's visible — the dashboard is Louis's review surface, keep it current. Offer to commit.

## Common failure modes
- **Passive answering.** The point is you LEAD — sourced facts + real figures + insight + the
  next question, every round. Waiting to be asked is the failure.
- **Unsourced or hand-wavy claims.** Every fact gets a verified source; flag uncertainty.
- **Fabricated figures.** Use the source's real figure, downloaded, or none.
- **Forcing the post early.** Study until curiosity is satisfied; the post comes after.

## Non-goals
- NOT `/seed` (which mines existing material for post ideas). `/study` starts from Louis's
  curiosity and sources live.
- Does NOT publish. `/post` drafts, `/visual` illustrates (with real figures), `/log` logs.

---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
description: Interview-driven LinkedIn/X post writer in Louis's voice. Triggered by /post linkedin or /post x. Reads $SOCIAL_HOME/voice/<platform>.md, runs a 5-question interview, mines $SOCIAL_HOME/inspiration/ for relevant saved posts as angle inspiration, generates 5 hooks, drafts a full post, iterates with Louis, then writes to $SOCIAL_HOME/drafts/YYYY-MM-DD-slug-<platform>.md.
---

# /post — Post writer

## Trigger
`/post linkedin` or `/post x`

## Inputs

- `$SOCIAL_HOME` env var (fallback: `$HOME/42/social`)
- Platform arg: `linkedin` or `x`
- `$SOCIAL_HOME/voice/<platform>.md` (required — error if missing)
- `$SOCIAL_HOME/inspiration/*.md` (optional: saved bookmarks/likes for the Step 3.5 inspiration pass; skipped if absent)

## Workflow

### Step 1: Resolve paths

Read `$SOCIAL_HOME`. Default to `$HOME/42/social` if unset (warn).

Verify `$SOCIAL_HOME/voice/<platform>.md` exists. If missing:
```
voice/<platform>.md not found. Run /audit <platform> first to generate it.
```
Stop.

### Step 2: Read voice rules

Read `$SOCIAL_HOME/voice/<platform>.md` fully. Internalise format, tone, hook patterns, structure, vocabulary, what-to-avoid, endings.

### Step 2.5: Quality bar and freshness check

Internalise the bar Louis holds posts to (learned from real sessions):
- **Value-first, depth over breadth.** A post earns attention with ONE advanced, non-obvious idea, exhausted, backed by a real number, ending in a portable mental model. Avoid the generic event-recap ("great day, AI is the future"). If a senior person in the audience already knows the point, go one level deeper.
- **Ground every claim in what was actually taught or in a real source.** Do not attribute lessons, stats, or frameworks Louis did not actually cover. The citation gate (Step 6.5) enforces sources; this enforces honesty about provenance.
- **No em-dashes (`—`) and no AI tells.** Use periods, commas, colons. (Mirrors the voice rules.)

**Freshness:** glob `posted/*-<platform>.md` and `drafts/*-<platform>.md`. If the angle Louis is describing was used recently, say so and offer a fresh one before drafting (he has flagged reusing a topic mid-draft).

### Step 3: Interview

Ask one question at a time via AskUserQuestion. Capture answers.

| # | Field    | Options                                                   |
|---|----------|-----------------------------------------------------------|
| 1 | Goal     | Build authority / Inspire / Convert / Entertain / Document |
| 2 | Media    | Text-only / Image(s) / Carousel / Video                   |
| 3 | Message  | (free-text) raw idea — lesson, opinion, story, result    |
| 4 | Emotion  | Curiosity / Urgency / Agreement / Awe / Resonance         |
| 5 | Audience | (free-text) specifically who? not "everyone"             |

If Media = Video, ask a follow-up: "Paste transcript or key points (3-5 bullets)."

If Media = Image(s) or Carousel: the visuals are produced by `/visual <platform>` from the saved draft, and the post TEXT must complement them, not duplicate them (see Step 5). When capturing the Message (Q3), push for the single sharpest, most advanced point rather than a list of everything that happened.

**Run record (start it here).** Also maintain the post's run record (`runs/<same-stem>-run.yaml`, the same `<YYYY-MM-DD>-<slug>-<platform>` stem as the draft) — see its schema in an existing example (`runs/2026-06-30-metr-measurement-ceiling-x-run.yaml`) — writing each stage's output as you complete it. This is what the review dashboard reads. Create it now with `post_id, platform, title, created_at, status: draft, source` (the seed id if drafting from a seed, else `manual`) and `session_id` if available, and capture `interview{goal, media, message, emotion, audience}` as soon as known. The slug/date is not final until Step 7, so use a provisional stem now and re-write the file at save to keep its name in sync with the draft. Every field is optional and tolerant: write stages incrementally, never block on the record.

### Step 3.5: Inspiration pass (optional, non-blocking)

After Q3 captures the raw message, mine Louis's saved corpus for relevant angles before generating hooks. This is his interest graph (what he bookmarked and liked), searched **cross-platform** on purpose: X inspiration informs a LinkedIn post and vice versa. The corpus is written by the `social_extract` tools (`make x-pull` / `make linkedin-pull` in `$SOCIAL_HOME`).

1. **Derive 2-4 keywords** from the Message (and audience): the sharpest topical terms, no stopwords.

2. **Retrieve whole saved items** from `$SOCIAL_HOME/inspiration/*.md`. Skip this whole step silently if the directory or files are absent. Each item is a block of `## author · date`, a permalink line, then the text, delimited by `---`. Pull matching blocks (not bare lines) so author, date, permalink, and snippet stay together, capped so a common keyword does not dump the whole corpus:
   ```bash
   awk -v RS='\n---\n' 'tolower($0) ~ /<kw1>|<kw2>|<kw3>/ {print "===="; print $0; if (++c>=8) exit}' "$SOCIAL_HOME"/inspiration/*.md
   ```
   X bookmarks and likes carry full text (rich matches); LinkedIn likes are URL-only (links, little text to match). From the returned blocks, keep up to ~6 distinct, most-relevant items.

3. **If there are matches**, present them inline as a numbered list:
   ```
   You've saved these on <topic>:
   1. @author (date) "<snippet>"  <permalink>
   2. ...
   ```
   Then ask Louis to reply with the numbers to draw on (e.g. "1, 4"), or "skip". Flag clearly that these are inspiration for ANGLE and FRAMING, **not copy**: do not lift sentences, the post stays original in Louis's voice. Any factual claim borrowed from a saved item still goes through the citation gate (Step 6.5) and needs its own source.

4. **If no matches** (or no corpus): say nothing, proceed to Step 4.

5. **Feed forward:** fold any chosen angle into hook generation (Step 4) and the draft (Step 5). It shapes framing only; it never overrides the voice rules or the one-idea-deep bar.

6. **Run record:** if matches were shown, write `inspiration{keywords, matched[], selected}` to the run record (`selected` = the item numbers Louis drew on, else `[]`).

### Step 4: Generate 5 hooks

Apply hook patterns from `voice/<platform>.md` to the message + audience + emotion. Output:

```
Hook 1 — [Pattern name]
[Hook text — one or two lines]
Rationale: [Why this hook, tied to a specific pattern from voice rules]

Hook 2 — ...
```

Use AskUserQuestion: "Which hook do you want to build the post around?" (Options: 1, 2, 3, 4, 5, Regenerate, Edit one inline.)

If Regenerate: generate 5 new hooks (different patterns), repeat.
If Edit: ask which hook, accept the edited text, use that.

Record the chosen hook. If later iteration pivots the post's core idea, do NOT silently swap the hook: call out that the hook is changing and re-confirm with Louis (he has objected to losing a hook he picked).

**Run record:** write `hooks.generated[]` (one entry per hook: `n, pattern, text` for all 5); once Louis picks, write `hooks.selected: <n>`.

### Step 5: Draft the full post

Build the post around the chosen hook applying every voice rule from `voice/<platform>.md`:
- Length within the documented range
- Signature formatting (Unicode bold sections, bullet hierarchy, etc.)
- Tone matching documented confidence/language conventions
- Structure: opener (the hook) → context → bullets → reflection → gratitude (if applicable) → CTA → hashtags (LinkedIn) or nothing (X)
- Avoid every pattern in "What to avoid"
- **One idea, deep.** Exhaust a single advanced point, back it with a concrete number, close on a reusable mental model. Cut breadth-for-its-own-sake.
- **No em-dashes (`—`).** Periods, commas, colons instead.
- **If a carousel/visual accompanies the post**, the body complements it: hook + narrative + the one takeaway + a nudge to swipe. Do NOT restate the carousel's bullet content in the body.
- **Credits and CTA:** tag generously. Every person and org worth crediting, including names tagged in others' posts about the same event (it widens reach). Never promise to share an asset (deck/slides) unless Louis confirms it is shareable.

Output the full draft.

### Step 6: Iterate

Loop:
- User says "tighter" / "more numbers" / "rewrite the open" / "less formal" / etc. → apply targeted edit, show new draft.
- User says "ship it" → proceed to step 7.
- User says "scrap" → stop, no file written.

**Run record:** append each revision as an entry in `draft.iterations[]` with a short note of what changed (e.g. "tightened open, added stat").

### Step 6.5: Citation hard-check (BLOCKING — do not skip)

Louis's rule: **no factual claim ships without a source.** This gate runs after "ship it"
and before saving. A draft cannot reach `status: ready` with an unresolved factual claim.

1. **Extract every factual claim** from the approved body. A factual claim is any
   externally verifiable statement — types: `number | named-stat | company-fact |
   benchmark | pricing | forecast | historical-event`, plus any claim about what a named
   person or org did/said. Use the rubric in
   `~/.claude/skills/cite/references/sourcing-standards.md` §1. Scope = **everything
   factual** (strict). Pure first-person experience or opinion ("I'm proud", "I taught a
   class") is NOT a factual claim and needs no source.

2. **Source + verify each claim** by calling the existing cite scripts directly (no full
   /cite orchestration needed):
   ```bash
   python3 ~/.claude/skills/cite/scripts/tavily_cli.py search "<claim>"      # find candidates
   python3 ~/.claude/skills/cite/scripts/tavily_cli.py extract <url>         # fetch page text
   python3 ~/.claude/skills/cite/scripts/validate_claim.py <claim.yaml> <page.txt>  # verbatim / anti-fabrication
   python3 ~/.claude/skills/cite/scripts/tier_lookup.py <domain>             # authority tier (1–4 ok)
   python3 ~/.claude/skills/cite/scripts/decisions.py recency <date>
   python3 ~/.claude/skills/cite/scripts/decisions.py status <tier> <recency>
   ```
   A claim **passes** when it has a healthy-link source of `tier` 1–4 whose page text
   actually supports the value (value_match / validate_claim agree).

3. **BLOCK on any claim that fails.** Present the failing claims via AskUserQuestion. For
   each, Louis chooses:
   - **Add source** — he pastes a URL; re-verify it through the scripts above.
   - **Soften** — reword so the line is no longer a verifiable claim; then re-extract.
   - **Waive** — explicit, recorded override (capture the reason).
   Do not save until every factual claim is sourced, softened, or explicitly waived.

4. **Attach the sources per the platform's `voice/<platform>.md` Citations rule.**
   - **X:** mark each claim inline with `[N]` at the point it's made, and add a final
     `Sources:` tweet listing `[1] <url>`, `[2] <url>` … (X wraps every URL to 23 chars, so
     ~6 fit one tweet). For a single standalone tweet, keep the `[1]` marker in-tweet and put
     the link in the first reply.
   - **LinkedIn:** build the first-comment sources block:
     ```
     ----- FIRST COMMENT (sources) -----
     Sources:
     [1] <claim, short> — <url>
     [2] ...
     ```

### Step 6.75: Fresh-eyes gate (BLOCKING — do not skip)

Run the `/fresh-eyes` skill on the approved draft: an independent agent with ZERO session
context reviews each tweet as a cold reader (text + visuals only). Every tweet must score
4+/5 on standing alone, and the text must not duplicate the visual (the visual carries the
information-heavy part; the text carries the takeaway). Iterate until it passes; record the
result in the run record under `fresh_eyes:`.

### Step 6.9: Clarity gate — X only (BLOCKING — do not skip)

X drafts follow the ASD-STE100 subset in `voice/x.md` ("Clarity"). Run the checker over the
assembled thread and resolve every `error` before saving:

```sh
cd $SOCIAL_HOME && bun -e '
import {splitThread} from "./dashboard/lib/thread.mjs";
import {checkThread, RULES} from "./dashboard/lib/clarity.mjs";
const body = await Bun.file(process.argv[1]).text();
const tweets = splitThread(body.replace(/^---[\s\S]*?\n---\n/, ""));
for (const f of checkThread(tweets, {terms: []}))
  console.log(`[${RULES[f.rule].severity}] t${f.tweet} ${f.rule}: ${f.detail}`);
' <path-to-draft>
```

- **`error` blocks the save**: a sentence over 25 words, a word with more than one meaning,
  a hedge, one concept under two names. Rewrite, or waive it with Louis and say why.
- **`warn` does not block**: passive voice where the actor is genuinely irrelevant is fine.
- The checker skips the Sources tweet, because a list of citations is not prose.
- If the thread has domain terms the built-in groups do not know, declare them in the run
  record as `terms: [{use: <approved>, not: [<variant>, ...]}]` and re-run.

Set `clarity_verified: true` only after every error is fixed or consciously waived, and
record any waiver under `clarity_waived`. Same rule as citations: the gate is satisfied by
a decision, never by silence.

### Step 7: Slugify and save

Generate slug from message: lowercase, dash-separated, ≤6 words.

Filename: `$SOCIAL_HOME/drafts/<YYYY-MM-DD>-<slug>-<platform>.md`

Write with frontmatter:

```yaml
---
platform: <linkedin|x>
goal: <answer to Q1>
media: <answer to Q2>
audience: <answer to Q5>
hook_pattern: <name of pattern from voice rules>
status: draft
created: <ISO date>
citations_verified: true          # set by Step 6.5 — true only after every claim resolved
sources:                          # one entry per sourced factual claim
  - claim: "<short>"
    url: "<url>"
    tier: <int>
citations_waived:                 # omit if none; else list reasons for explicit waivers
  - "<claim> — <why waived>"
clarity_verified: true            # X only; set by Step 6.9 once no clarity error remains
clarity_waived:                   # omit if none; else one line per accepted violation
  - "<rule> t<N> — <why kept>"
---
```

Body: the final approved post text, followed by the first-comment block from Step 6.5:

```
<post text, ready to paste>

----- FIRST COMMENT (sources) -----
Sources:
[1] <claim, short> — <url>
[2] ...
```

`citations_verified` MUST be `true` to save (every claim sourced, softened, or waived).
For X, `clarity_verified` MUST be `true` too (every clarity error fixed or waived).
If Louis waived a claim, still set `citations_verified: true` but record it under
`citations_waived` — the gate is satisfied by a conscious decision, never by silence.

**Run record:** write `draft.final` (the final thread text) and `citations{verified, sources[{claim, url, tier}], waived[]}`, reusing the Step 6.5 sources you already assembled. Finalize the run record name to `runs/<YYYY-MM-DD>-<slug>-<platform>-run.yaml` so it stays in sync with the draft filename (rename the provisional file if the slug/date changed).

### Step 7.5: Dashboard refresh (ALWAYS — the draft must appear on social.develle.fr)

Every saved draft ships to the review dashboard immediately (Louis reviews there, not in
files). The dashboard (`dashboard/build.mjs`) has a STRICT schema — follow it exactly:

1. **Draft body format**: the thread MUST use `1/` `2/` … markers, each on its own line
   before that tweet's text (see `drafts/2026-06-30-metr-measurement-ceiling-x.md` as the
   reference). Without markers, `splitThread` falls back to blank-line splitting and
   per-tweet visuals cannot attach. Never use "TWEET 1"-style headers.
2. **Stage the visuals**: copy every figure/animation to
   `$SOCIAL_HOME/visuals/build/<same-stem-as-draft>/` (png/gif/mp4/webm). This dir is
   gitignored (build artifact); commit the GENERATOR script to `visuals/` instead.
3. **Reference them in the run record** — the only supported schemas:
   - **Per-tweet figures (threads)**: `visual.per_tweet: [{tweet: 1, png:
     visuals/build/<stem>/fig1.png, caption: "…", source: "…"}, …]` — paths are
     REPO-RELATIVE (a bare filename silently fails `copyAsset`). Each renders inline
     under its tweet.
   - **Single figure**: `visual.file: <repo-relative path>` (+ optional `source`, `note`).
   - **Fallback**: no `visual:` at all → every image in `visuals/build/<slug>/` renders
     as a grid.
4. **Build + deploy**: `cd $SOCIAL_HOME && make dashboard && make dashboard-deploy`
   (rsyncs to TinyButMighty:/srv/social → social.develle.fr behind Cloudflare Access).
5. **Verify by artifact, not by exit code**: the copied assets are RENAMED
   `<dir>__<file>` — check `dashboard/build/site/assets/` contains
   `<stem>__<figN>.png` for every figure, and only then deploy/report success.
6. **Cloudflare cache trap (recurring failure mode — treat as part of the deploy)**:
   social.develle.fr is proxied through Cloudflare, which edge-caches assets BY FILE
   EXTENSION (png/gif/css/js/woff2/mp4 all qualify). rsync-ing a new file over the same
   name does NOT invalidate the edge copy, so "deploy succeeded" while Louis still sees
   the old video/figures. Rules:
   - `make dashboard-deploy` now runs `make dashboard-purge` (URL-scoped purge via
     `dashboard/purge-cache.sh`) and FAILS LOUDLY if the purge fails. A red purge error
     means the deploy is NOT visible — say so explicitly, never report success.
   - Never purge with `purge_everything`: the develle.fr zone is shared by other
     subdomains (upload, share, alakazam, …). The script purges only social URLs.
   - The purge needs `CLOUDFLARE_API_TOKEN` with `Zone > Cache Purge` permission,
     resolved via `secrets run` in `~/42/GroundControl/network/domains`. If it errors, diagnose
     with `cd ~/42/GroundControl/network/domains && secrets check` — a single broken vault ref
     blocks the whole context resolve.
   - Origin vhosts (nginx :80 = the CF-facing one, Caddy :443) must send
     `Cache-Control: no-cache` so Cloudflare and browsers revalidate (cheap ETag 304s).
     `dashboard/deploy.sh` templates include this; don't strip it.
   - Even after a purge, Louis's BROWSER may hold pre-purge assets — when he reports
     stale content right after a green purge, ask for one hard-refresh
     (Cmd+Shift+R) before debugging further.

This step is not optional and does not wait for "ship it": drafts render in the
dashboard's Drafts section so Louis can review the full card (thread, stages, visuals,
sources) in one place.

### Step 7.9: Ship gate (BLOCKING — the last thing before Louis pastes)

```sh
cd $SOCIAL_HOME && make ship-check DRAFT=drafts/<file>.md
```

Two things, both about what leaves this repo:

- **Text.** Invisible Unicode, exotic spaces, bidi controls and tag characters survive
  copy-paste into X and are invisible in every editor, so a machine has to look. A finding
  BLOCKS. Fix with `make ship-clean DRAFT=...`, then re-run and confirm it is clean.
- **Visuals.** Every image and video the run record declares is scanned for C2PA / EXIF /
  XMP and known vendor marks. This is **report only**, including our own renders: measured
  2026-08-12, they carry no AI marks at all, so cleaning them is a no-op with a real chance
  of corrupting a render. If a mark ever appears, say so and let Louis decide.

**Never strip a third-party figure.** The gate labels them from the run record
(`source_url:`, or a `source:` naming a paper) and never modifies them. Those are other
people's published figures, credited on the post as `[camera N]`; removing their
provenance removes their attribution. Do not "fix" a finding on one.

**Layer B is deliberately not wired.** The upstream skill can also defeat statistical
token-sampling watermarks by paraphrasing. Do not run it on a draft: it rewrites text the
clarity gate and `/fresh-eyes` already approved, trading Louis's voice for a mark X cannot
read anyway.

### Step 8: Output

Print:
```
Draft saved to: $SOCIAL_HOME/drafts/YYYY-MM-DD-slug-platform.md

----- POST -----
<full post text>
----- END -----

----- FIRST COMMENT (sources) -----
<the sources block — paste this as the FIRST comment under the post>
-----

Copy-paste the post to <platform>, then paste the sources as the first comment.
After publishing, run /log <platform> to capture metrics.
```

## Common failure modes

- **`voice/<platform>.md` missing**: stop, suggest `/audit <platform>` first
- **No `inspiration/` corpus or no keyword matches**: skip Step 3.5 silently, never block
- **User scraps mid-iteration**: do not write a file
- **Slug collision with existing draft**: append `-2`, `-3`, etc.

## Non-goals

- Does NOT publish to any platform
- Does NOT touch `data-store.yaml` (that's `/log`)
- Does NOT modify voice rules (that's `/audit`)

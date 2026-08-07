---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, SendUserFile
description: Generate post visuals (LinkedIn carousel + share card) in the terminal design system from a drafted post. Triggered by /visual linkedin or /visual x. Auto-derives a content spec from a drafts/ post body, renders via visuals/render.mjs (headless Chrome), and shows Louis the PNG/PDF output to iterate on.
---

# /visual — Post visual generator

## Trigger
`/visual linkedin` or `/visual x`

## Inputs

- `$SOCIAL_HOME` env var (fallback: `$HOME/social`, warn)
- Platform arg: `linkedin` or `x`
- A drafted post in `$SOCIAL_HOME/drafts/*-<platform>.md`
- Engine: `$SOCIAL_HOME/visuals/render.mjs` + `visuals/templates/` (design: Direction 1, terminal)

## Workflow

### Step 1: Resolve paths
Read `$SOCIAL_HOME`. Verify `visuals/render.mjs` and `drafts/` exist. If the engine is
missing, stop and point to `visuals/README.md`.

### Step 2: Pick the draft
Glob `$SOCIAL_HOME/drafts/*-<platform>.md`. Present numbered list, ask which via
AskUserQuestion. Read its frontmatter (`media`, `hook_pattern`, slug from filename) + body.

### Step 3: Choose formats
Ask via AskUserQuestion (multiSelect) which to produce:
- **Carousel** (LinkedIn-native PDF + PNG pages, 1080×1350) — default if frontmatter `media: Carousel`.
- **Share card** (single image) — then ask square (1080×1080) or landscape (1600×900).
- **Diagram** (single 16:9 image) — a `chart`/`compare`/`flow` technical diagram. The
  default for X posts whose payload is a number, comparison, or mechanism. Produced via
  the propose-and-pick flow in Step 4b, not auto-derived.
Default the card to square unless the platform is X with a wide image in mind. On X, when the
payload is a SOURCE's data, a real source figure beats any diagram (see Step 4b) — do not
fabricate a chart. Otherwise prefer a Diagram over a text card when the post carries a number,
a contrast, or a flow.

### Step 4: Auto-derive the spec from the post body
Build a spec object (see `visuals/README.md` for the exact shape). Heuristics:

- **Identity**: `{ name: "Your Name", handle: "@ezalos", avatar: "YN" }` (constant unless told otherwise).
- **Cover page**:
  - `headline_lines`: the hook (opening 1–3 lines), broken into ≤3 short lines. Set `accent_line` to the contrarian/punchline line (or the line that was Unicode-bold in the post), else the last line.
  - `prompt`: `{ path: "~/<short-context>", cmd: "<verb from the hook>", arg: "<slug>" }`. Pick a verb that matches the post (build / teach / ship / design); default to a fitting command.
  - `tags`: the inline `#hashtags` already in the post body, `#` stripped, capped at 3–4. If none, pull 2–3 key technical terms.
- **Content pages** (2–4): each major bulleted block in the post (the `🔹` / `→` / `🟡🔴🟢` lists) becomes one page.
  - `title`: the lead-in sentence of that block.
  - `bullets`: the list items as `{ mk: "→", tx: "..." }`. Convert any Unicode-bold runs (𝗹𝗶𝗸𝗲 𝘁𝗵𝗶𝘀) back to `<b>like this</b>`. Cap ~4 bullets/page; split a long block across two pages.
  - `eyebrow_path`: `~/<slug>/<section>` for terminal flavour.
  - If the block centers on one number, add `stat: { value, unit }` instead of/under bullets.
- **Closing page** (optional, recommended): a content page echoing the post's CTA
  (e.g. title "Want the slides?" + one bullet "→ Drop a comment").
- **Card** (if chosen): `quote_lines` = the single most quotable line (the contrarian reframe
  or the transferable principle), `accent_line` on the payoff, `sub` = the supporting stat,
  `tag` = one signature hashtag.

Aim for **3–6 carousel pages total**. Surface the derived spec outline to Louis
(page-by-page: title + bullet count) and confirm/adjust BEFORE rendering.

### Step 4b: Diagrams — propose 2-3 concepts, Louis picks (X)

**Original source figures come first (factual data).** If the post's payload is a *source's*
data (a benchmark plot, a published chart), the visual must be that source's REAL published
figure — download the original (its download/export button, or the asset URL), keep provenance
+ license ("Figure: METR, CC-BY"), its **title + legend** (never crop them out), and its
**direct link**; store it under `visuals/sources/`. Do NOT hand-build a chart with invented
values to "illustrate" a fact; that adds imprecision (it caused a real error once). Only build
a diagram (below) for CONCEPTUAL / mechanism visuals, or for data Louis owns. See
`voice/<platform>.md` Visuals and the standing rule.

**One visual per tweet (X threads).** Each tweet gets its own visual, not just the first,
following the same preference order: a real source figure > a highlighted source screenshot >
a conceptual diagram. The screenshot fallback:
`bun visuals/highlight.mjs --url <src> --text "<passage>" --out visuals/sources/<name>.png`
keeps the FULL horizontal width and trims only top/bottom (still showing the title/legend/
caption), highlighting the key point, phone-sized. Record the per-tweet mapping in the run
record under `visual.per_tweet` (`tweet`, `png`, `source`, `caption`); the dashboard renders
each tweet with its visual. Surface each figure's source link in the Sources tweet as `[📷N]`.

For a **Diagram**, do NOT auto-pick the visual. From the post body (and, when drafting from
`seeds/`, the seed's `Visual idea` brief), propose **2-3 distinct diagram concepts**, each as:
one-line description + which template (`chart` | `compare` | `flow`) + the key data it shows.
Present them via AskUserQuestion. On Louis's pick, build a `diagrams: [{ template, ... }]`
spec section using the shapes below, then render.

Diagram spec shapes (see `visuals/README.md` for the full list):
- `chart`: `{ template:'chart', mode:'line'|'bars'|'scatter', title, y_label, y_suffix,
  y_min, y_max, x_ticks:[…], values:[…], marker_index?, marker_label?, caption, prompt? }`
- `compare`: `{ template:'compare', title, panels:[{ label, badge?, state:'good'|'bad'|'neutral',
  blocks:[{t, n?, hot?, cold?}], meter?:{pct,label}, foot? }], caption, prompt? }`
- `flow`: `{ template:'flow', title, nodes:[{label, metric?, sub?, kind:'main'|'child'|'result'}],
  forward_label?, return_label?, caption, prompt? }`

Keep diagram text terse — these render at fixed size; long strings overflow. Numbers in a
diagram still obey `voice/x.md` Citations: the factual source goes in the post's reply, not
on the image.

**Run record:** after proposing, write `visual.concepts[]` (one entry per concept: `label,
desc, template`) into the post's run record — `runs/<same date+slug+platform>-run.yaml`,
matched from the draft you are visualizing (its filename stem). On Louis's pick, write
`visual.selected: <label>`. Additive only: this does not change the propose-and-pick flow.

### Step 5: Write the spec and render
Write the spec to `$SOCIAL_HOME/visuals/build/<slug>/spec.json`. Then:
```bash
cd $SOCIAL_HOME && bun visuals/render.mjs visuals/build/<slug>/spec.json
```
On render error, read stderr, fix the spec (or template), re-run. Do not claim success
without the output files existing.

**Run record:** after a successful render, write `visual.spec_path` (the `spec.json` path)
and `visual.png` (the rendered image) to the same post run record. Additive only.

### Step 6: Show output and iterate
Use SendUserFile to surface `visuals/build/<slug>/`:
- carousel PNGs (`carousel-NN.png`) and the PDF (`<slug>-carousel.pdf`)
- the card PNG

Loop: Louis says "page 2 title shorter" / "swap accent line" / "drop page 4" → edit
`spec.json`, re-render, re-show. The spec is the editable source of truth.

### Step 7: Done
Print the output paths and remind: the carousel **PDF** is what uploads to LinkedIn as a
document carousel; PNGs are for image posts and X.

## Design fidelity
- The look lives in `visuals/templates/theme.css` — do NOT restyle inline. To retune the
  aesthetic, edit theme.css (one place, both platforms).
- Visuals echo the voice: headline = the hook, `<b>` = the post's Unicode-bold emphasis,
  inline-hashtag terms become chips. Keep them consistent with `voice/<platform>.md`.

## Common failure modes
- **No drafts for platform**: stop, suggest `/post <platform>` first.
- **`bun` missing**: the engine needs bun; stop and say so.
- **Headline/bullets overflow the page**: shorten the text in the spec (the templates are
  fixed-size by design); never shrink fonts inline.

## Non-goals
- Does NOT write or edit the post text (that's `/post`).
- Does NOT publish anything.
- Does NOT change the design system per-post (edit theme.css deliberately, not inline).

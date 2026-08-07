---
name: social-video
description: Manim explainer videos for Louis's social posts (LinkedIn/X) in his "deck" house style — page dots, brand corner, gradual reveals, seamless transitions. Triggered by "make a video for my post", "animated explainer", "manim video", or /social-video. Builds on concept-to-video for raw manim mechanics; this skill adds the house style, Louis's animation preferences, and the social-repo publishing pipeline.
---

## Observability

Universal baseline (CRITICAL on abort; WARNING on user correction / fallback / retry /
precondition-fail; INFO on every distinct user feedback message and edge-case path):

```
claude-log social-video INFO  "social-video: feedback: '<paraphrase>'; slide=<sN>; changed <what>"
claude-log social-video WARNING "social-video: layout defect shipped to Louis: <what>"  # he saw it before we did
claude-log social-video CRITICAL "social-video: render failed: <tail>"
```

# /social-video — explainer videos for Louis's posts

One scene file per video in `$SOCIAL_HOME/visuals/<stem>.py`, importing the shared toolkit
`$SOCIAL_HOME/visuals/deck_kit.py`. Reference implementation (17 feedback rounds of
converged taste): `visuals/2026-07-16-mira-mini-linkedin-worldmodel.py`.

## House style (non-negotiable chrome)

- Background `SURF` (#f8fafc), ink text, ONE blue accent + semantic colors (teal=codec,
  orange=model, lavender=latent, red=named entities/negations). Max ~5 colors.
- **Progress dots top-left** (`deck_kit.dots(current, N_PAGES)`), one per page,
  `Transform` on every page turn.
- **NO wordmark on Louis's own posts** (his rule, 2026-07-26: he brands neither his X
  nor his LinkedIn that way). `brand_corner` is for client work (e.g. Alakazam) only.
- **NEVER a graph with undefined axes** (2026-07-26): every plot-like drawing, however
  conceptual, gets labeled x and y axes.
- **Constant arrowheads**: use `deck_kit.arr()` (fixed tip_length); manim's default
  shrinks heads on short arrows into unreadable specks.
- Graphic identity rework in progress (2026-07-26 brief: nailed palette, paper-norm
  shape library, LaTeX, nicer fonts); check visuals/DESIGN.md before styling choices.
- No outer frame around the canvas.
- Titles via `title_text()` (auto-fit, buff 0.85 clears the chrome); captions via
  `caption()`. EVERY Text gets a width guard — never trust a string to fit.

## Louis's animation preferences (learned, apply by default)

1. **Gradual text reveal**: line-by-line `FadeIn(shift=UP*0.2)`, a beat (`wait 0.3-0.6`)
   before/after the impact line; the impact line gets `FadeIn(scale=1.12)` and breathing
   room (`buff 0.7` around it).
2. **Seamless transitions between pages**: never fade-everything when pages share a
   concept. Morph the shared object (`ReplacementTransform` of the front plate only —
   text+stacked plates morph muddy), or keep it on screen while the title changes, then
   build around it. Examples: world-model block → full-width container ("zoom in");
   collapse back to a small box; input/output units DUPLICATING with `animate(path_arc=…)`
   fan-opening into the multiplayer layout.
3. **Progressive diagram reveal**: one element per beat, arrows `GrowArrow` in flow
   order; when a node has two inputs, show BOTH sources first, then wire both arrows in
   one beat. Title writes BEFORE big layout motion.
4. **Live video, never frozen**: end footage with `white_wash_out()` (cover fades in over
   the still-playing clip). Introduce concepts on real footage before diagrams.
5. **Narrative discipline**: don't show a concept before it is introduced (no P1-P4
   before the multiplayer slide); recap slides (cost ledger) come AFTER everything they
   summarize; single-player representation until multiplayer is announced.
6. **Stickers for punchlines** (`sticker()`), star CTA that colors in LATE (grey outline
   first, gold fill + `Indicate` after the context lands).
7. Page count ~8; total 60-90 s; end on partnership/CTA (repo chip + "Report & demo at
   <url>" line, no https://).

## Video-in-video

- Extract PNGs at 10 fps (`ffmpeg -vf fps=10`), play with `make_frame_flipper()`.
- Zooms/crops are BAKED INTO THE PNGs with PIL (smoothstep crop; manim cannot clip a
  moving image). MP→SP quadrant zooms: anchor the crop to the target quadrant corner.
- **CACHE TRAP**: manim's partial-movie cache hashes code, not frame files. After ANY
  frame regeneration, render with `--flush_cache` — otherwise old pixels resurface in
  unchanged segments.

## Numbers discipline

Every figure on screen traces to a source (report section, repo README, HF file sizes, a
measured experiment). Compute-ledger rows spell the arithmetic out: "N nodes x 8 H100 x
~H h = X h". Derived numbers carry `~` and get flagged to Louis with their derivation.
No cost figures on weights/model-card surfaces (compliance rule from the launch kit).

## Workflow

1. **Storyboard first**, get Louis's approval (pages + one line each). State for each page
   what the reader LEARNS, not what moves: Louis's recurring correction is that a beat is
   doing the wrong tweet's job, and that is visible in a storyboard but not in a render.
2. Write the scene: one `Scene`, pages separated by `turn_page(n)`.
3. **Preview loop**: render → read the LAYOUT AUDIT the scene prints → fix everything it
   reports BEFORE showing Louis. Layout defects Louis sees first are failures (log WARNING).
   - `IdentityScene` (visuals/identity/motion.py) audits every beat and prints
     CLIPPED / OVERLAP / CROWDED. **Ship only on a clean audit.**
   - **Audit at `-qh`, the quality that ships.** manim sizes some text relative to pixel
     height, so a `-ql` pass is not evidence: five ANA scenes passed at 854x480 and failed
     at 1920x1080 (2026-08-04).
   - A contact sheet is a second pair of eyes, not the detector: sampling 6-8 frames finds
     gross defects and misses the rest.
   - Two classes the audit cannot see, so never write them: `ReplacementTransform` between
     two strings (use `motion.retitle()`, a crossfade) and a dimension label outside the box
     of the element it annotates (arrows must attach to the box).
4. Iterate per-slide on his feedback (he references slides as s0/s1/…; his s-numbers are
   1-indexed pages — confirm with content, not index math).
5. **Final**: `-qh` (1080p60) → copy MP4 to `visuals/build/<post-stem>/` + make the GIF
   (`fps=12,scale=960`) → `make dashboard && make dashboard-deploy` (purge fires; if it
   fails say the site may be STALE — never report success on a red purge) → commit the
   scene to ~/42/social → deliver the MP4 via SendUserFile.
6. Dashboard wiring: the post's run record needs `visual.file: visuals/build/<stem>/<file>`
   (repo-relative). Full schema: post skill Step 7.5.

## Render commands

```bash
cd $SOCIAL_HOME/visuals
<venv-python> -m manim render -ql --format mp4 --media_dir <scratchpad>/manim <scene>.py <Class>   # preview
<venv-python> -m manim render -qh --flush_cache --format mp4 --media_dir <scratchpad>/manim <scene>.py <Class>  # final
```

Any venv with `manim` works (the mira venv at ~/Work/mira/.venv has it). System deps:
libpango1.0-dev libcairo2-dev (installed on TheBeast).

# bin/lint-blend.mjs — catch double-blend in layer shaders (design spec)

Status: spec only (not built). Surfaced 2026-06-21 during the yaktin-beni
build, where girih-mandala declared `blend: screen` in its meta AND called
`blend_screen(u_below, col, 1.0)` in the shader → the engine screen-blended
the already-screened output a second time, making the layer blinding. The
smoke test passed it (it only checks compile + the 3 degenerate cases); only
a full render exposed the over-brightness. A static lint would have caught it
in ~50ms.

## The rule

For each piece layer (`pieces/<slug>/layers/<name>/` and global `layers/<name>/`):
- Read the layer's effective blend mode: the piece meta's per-layer `blend:`
  override if present, else the layer meta's `default_blend:`.
- If that mode is one of {screen, add, max, multiply, normal} (i.e. NOT
  `replace`), grep the layer's `shader.frag` for a call to the matching
  `blend_<mode>(` helper that passes a `u_below` sample as the `below` arg.
- If found → WARN: "double-blend: layer <name> declares blend:<mode> and also
  calls blend_<mode>(u_below,..) in-shader; the engine applies the declared
  blend — output only the layer's own contribution."

`replace` (and `normal` at alpha 1) layers legitimately composite u_below
themselves (mirror-bloom, heat-shimmer) — exempt them, OR only flag when the
in-shader blend mode MATCHES the declared non-replace mode (the true
double-apply). Matching-mode is the high-precision signal; start there to
avoid false positives.

## Scope / placement

Sits with the existing bin/lint-*.mjs family (palette, idle, composition,
seams). Pure static analysis — no render needed, so it can run in the
new-piece / iterate gate cheaply and in smoke-time. Wire into the §11
lints+metrics gate of /vjay-new-piece and /vjay-iterate.

## Open questions

- Detecting "the arg is a u_below sample" reliably from text: accept a simple
  heuristic (the first arg expression contains `u_below`) and tolerate a few
  misses; the matching-mode constraint keeps false positives near zero.
- Could generalize later to other compositing smells (e.g. a max-blend layer
  that also reads u_history and could runaway), but keep v1 to the one rule.

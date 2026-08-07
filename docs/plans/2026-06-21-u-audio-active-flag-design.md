# u_audio_active uniform — fix "looks different when paused" systemically (design spec)

Status: spec only (not built). Surfaced 2026-06-21 on yaktin-beni: pieces
render a DIFFERENT image when paused than when playing, because the runtime
sets `u_audio_playing = 0` on pause while keeping the stem + section uniforms +
`u_time` frozen-VALID. The house idle-fallback `mix(synthetic, real,
u_audio_playing)` then renders the SYNTHETIC idle look when paused. Per-piece
workaround (force `playing = 1.0`) sacrifices the tuned synthetic idle look.

## The fix
Add a runtime uniform `u_audio_active` = audio is LOADED and at a valid time
(true when `audioPlaying || pausedMidTrack`), distinct from `u_audio_playing`
(actively advancing; false when paused).

In studio/runtime.mjs, alongside `setUniform1f('u_audio_playing', ...)`:
```js
const audioActive = !!(currentMeta?.audio && audioEl
  && (audioPlaying || pausedMidTrack));      // pausedMidTrack already computed
setUniform1f('u_audio_active', audioActive ? 1.0 : 0.0);
```
(`pausedMidTrack` is computed in render() for the u_time pin.)

## Piece-side convention (update the idle recipe + layers/README)
- Gate the PERSISTENT LOOK on `u_audio_active`:
  `mix(synthetic_idle, real, u_audio_active)` — so paused (active=1) uses the
  real frozen uniforms and matches playing, while true idle (active=0) uses the
  synthetic self-play look.
- Reserve `u_audio_playing` for things that SHOULD stop when paused: onset
  sparks, beat shockwaves, live-FFT flashes.

## Why it's worth it
The bug is SYSTEMIC: `mix(synthetic, real, u_audio_playing)` is the documented
idle self-play pattern, so most audio pieces in the catalog render a different
(synthetic) image when paused. A one-uniform runtime change + a convention
update fixes the whole class without sacrificing idle beauty. Pair with wiring
`bin/inspect-pause.mjs` into the /vjay-iterate gate so regressions are caught.

## Migration
Existing pieces keep working (they just keep using u_audio_playing). New/
touched pieces adopt u_audio_active for the look. Optionally sweep the catalog
later (grep `mix(.*u_audio_playing)`).

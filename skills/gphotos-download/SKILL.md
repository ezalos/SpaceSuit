---
name: gphotos-download
description: Use when Louis wants photos pulled from a Google Photos share link onto this machine — e.g. "download this album", "grab the pictures from this link", "pull those photos locally", or whenever he pastes a photos.app.goo.gl / photos.google.com/share URL and wants the files rather than the page. Downloads full-resolution originals from PUBLIC "anyone with the link" albums, no Google account or auth needed.
allowed-tools: Read, Bash, AskUserQuestion
---

## Observability

This skill follows the universal observability baseline (see `docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (Claude was about to be wrong), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during the run.** Every distinct user message that conveys preference, redirection, refinement, or commentary MUST be logged. Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change — already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | Share page fetched but zero `/pw/` URLs found | `gphotos-download: no photos found; album likely NOT public or link expired` |
| CRITICAL | Network/HTTP failure fetching the share page | `gphotos-download: fetch failed: <error>` |
| WARNING | Album has many photos and only the first page was embedded | `gphotos-download: got <n>; very large albums lazy-load, count may be short` |
| WARNING | Downloaded images carry identifying EXIF (GPS/device) | `gphotos-download: EXIF <fields> present on <n> files; flagged to Louis` |
| WARNING | Output directory is inside a git repo that is not ignoring images | `gphotos-download: <dir> is tracked by git; suggested gitignore` |
| INFO | Download completed | `gphotos-download: <n> photos to <dir>, <size> total` |

Concrete invocation examples:

```
claude-log gphotos-download INFO "gphotos-download: starting; out=<dir>"
claude-log gphotos-download WARNING "gphotos-download: EXIF Make, Model present on 10 files; flagged to Louis"
claude-log gphotos-download CRITICAL "gphotos-download: no photos found; album likely not public"
```

# gphotos-download

## What this does

Downloads **full-resolution originals** from a public Google Photos share link.
It scrapes the share page for the `lh3.googleusercontent.com/pw/...` URLs embedded
in its HTML and fetches each with the `=d` (original) size suffix.

No Google account, no OAuth, no API key. It works only on albums shared as
**"anyone with the link"** — which is also its main failure mode.

## Usage

```bash
uv run ~/Setup/skills/gphotos-download/gphotos_dl.py \
    "https://photos.app.goo.gl/XXXXXXXX" \
    --out <output-dir> \
    --prefix <filename-prefix>
```

| flag | meaning |
|---|---|
| `--out` | output directory (required, created if missing) |
| `--prefix` | filename prefix, default `gphoto` |
| `--size` | lh3 size suffix: `=d` original (default), `=s0`, `=w2048-h2048` |

Requires `pillow` for the EXIF check — `uv run` handles it.

## Before running

**Ask where the files should go** unless Louis already said. Photos are bulky and
land somewhere permanent; guessing wrong means moving hundreds of megabytes later.

**Check whether the destination is inside a git repo.** Albums are tens of MB and
git keeps binaries forever. If the target is a repo, add the directory to
`.gitignore` first and say so — `~/Setup/docs/hardware/photos/` is set up exactly
that way: on disk and inside the restic backup, out of the public repo.

## After running

The script prints each file with dimensions, byte size, and an **EXIF warning**
when identifying metadata (device make/model, GPS) survived. Google usually strips
EXIF on `=d`, but it is checked per file rather than assumed.

Relay any EXIF warning to Louis. It matters when photos are heading anywhere
public; it does not when they are private documentation. Do not silently strip
metadata — tell him and let him decide.

## When it returns nothing

Zero photos almost always means **the album is not actually public**. The script
cannot see a page it cannot open, and Google serves a login wall rather than an
error. Ask Louis to confirm the link is set to "anyone with the link", not shared
to specific accounts.

Very large albums lazy-load beyond the first page, so a count that looks short on
a big album is a real limitation, not a bug — say so rather than claiming success.

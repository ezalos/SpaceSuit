---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
description: Audit Louis's social posts to generate or update voice rules. Use when starting fresh (first-time audit) or after publishing ~10 new posts (re-audit). Triggered by /audit linkedin or /audit x. Reads $SOCIAL_HOME/sources/ + $SOCIAL_HOME/posted/, writes $SOCIAL_HOME/voice/<platform>.md, appends to $SOCIAL_HOME/evolution-log.md.
---

# /audit — Voice rules generator

## Trigger
`/audit linkedin` or `/audit x`

## Inputs

- `$SOCIAL_HOME` env var (fallback: `$HOME/42/social`)
- Platform arg: `linkedin` or `x`
- Corpus: `$SOCIAL_HOME/sources/<platform>-posts.md` + `$SOCIAL_HOME/posted/*-<platform>.md`
- For X first-run only: `$SOCIAL_HOME/sources/x-brief.md` (when no corpus exists)
- Existing rules (if any): `$SOCIAL_HOME/voice/<platform>.md`

## Workflow

### Step 1: Resolve paths

Read `$SOCIAL_HOME` from env. If unset, default to `$HOME/42/social` and warn.

Verify these exist:
- `$SOCIAL_HOME/sources/` (must exist, error otherwise)
- `$SOCIAL_HOME/voice/` (must exist, error otherwise)

### Step 2: Determine mode

| State | Mode |
|-------|------|
| `voice/<platform>.md` does not exist | **First-run** (generate from scratch) |
| `voice/<platform>.md` exists | **Re-audit** (propose diff) |

For X with empty corpus (`sources/x-posts.md` missing AND `posted/*-x.md` empty): use `sources/x-brief.md` as the only input.

### Step 3: Read corpus

- For LinkedIn: glob `$SOCIAL_HOME/sources/linkedin*posts*.md` (the hand-curated `linkedin-posts.md` seed plus `linkedin-posts-extracted.md`, written by the `linkedin-pull` extractor) + `$SOCIAL_HOME/posted/*-linkedin.md`.
- For X: glob `$SOCIAL_HOME/sources/x-posts.md` (may not exist) + `$SOCIAL_HOME/posted/*-x.md`.
- If neither exists for X, fall back to `sources/x-brief.md`.

### Step 4: Analyse

Identify:
- **Hook patterns** — 3-5 named patterns with concrete examples from corpus (or from brief)
- **Tone** — confidence, formality, language conventions, mood
- **Structure** — typical post template (opener → … → close)
- **Format** — length range, bullet styles, signature moves (Unicode bold, emoji, etc.)
- **Vocabulary** — terms used freely, recurring expressions, banned patterns
- **Endings** — CTA styles, hashtag conventions
- **What to avoid** — generic patterns, AI tells, things absent from corpus that suggest deliberate omission

Write rules **prescriptively** ("Always use X") not descriptively ("tends to use X").

### Step 5: First-run path

Generate `$SOCIAL_HOME/voice/<platform>.md` using the template below. Present the full content for approval before writing. After approval, write the file.

Template sections (in order):

```markdown
# Voice rules — <Platform>

## Format
[Length range, structural patterns, bullet style, signature moves]

## Tone
[Confidence, formality, language conventions]

## Hook patterns
1. [Name] — [Example from corpus]
2. ...

## Structure
[Opener → ... → close]

## Vocabulary
[Terms used freely; recurring expressions]

## What to avoid
[Generic patterns, AI tells, banned words]

## Endings
[CTA styles, closing patterns]
```

### Step 6: Re-audit path

Read existing `voice/<platform>.md`. Compare against newly-extracted patterns. Present a **diff** of proposed changes — one section at a time via AskUserQuestion (accept / edit / reject).

Apply approved changes to the file.

### Step 7: Append to evolution log

Append one line per accepted change to `$SOCIAL_HOME/evolution-log.md`:

```
YYYY-MM-DD — <platform> — <rule changed> — <reason>
```

Today's date comes from the system. For first-run, the entry is:

```
YYYY-MM-DD — <platform> — initial voice rules generated from N source post(s)
```

### Step 8: Commit

Offer to commit:
```bash
cd $SOCIAL_HOME && git add voice/<platform>.md evolution-log.md && git commit -m "chore(voice): audit <platform> — <summary>"
```

Ask Louis before running git commands.

## Common failure modes

- **`$SOCIAL_HOME` unset**: fall back to `~/42/social`, warn user, suggest adding `export SOCIAL_HOME=$HOME/42/social` to `.zshrc`
- **No corpus and no brief**: stop, tell user to paste posts into `sources/<platform>-posts.md` or write `sources/<platform>-brief.md`
- **Existing rules look better than proposal**: respect Louis's "reject" — never overwrite without explicit accept

## Non-goals

- Does NOT scrape platforms
- Does NOT touch `data-store.yaml`
- Does NOT validate post content — that's `/post`'s job

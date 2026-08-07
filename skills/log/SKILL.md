---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
description: Capture metrics for a published post and move it from drafts to posted. Triggered by /log linkedin or /log x. Prompts Louis for impressions/reactions/comments/reposts, appends a row to $SOCIAL_HOME/data-store.yaml, and moves the draft to $SOCIAL_HOME/posted/ with metrics in frontmatter.
---

# /log — Metric logger

## Trigger
`/log linkedin` or `/log x`

## Inputs

- `$SOCIAL_HOME` env var (fallback: `$HOME/42/social`)
- Platform arg: `linkedin` or `x`

## Workflow

### Step 1: Resolve paths

Read `$SOCIAL_HOME`. Default to `$HOME/42/social` if unset (warn).

Verify these exist:
- `$SOCIAL_HOME/drafts/`
- `$SOCIAL_HOME/posted/`
- `$SOCIAL_HOME/data-store.yaml`

### Step 2: Find unlogged drafts

Glob `$SOCIAL_HOME/drafts/*-<platform>.md`. These are candidates.

Present numbered list:
```
Unlogged <platform> drafts:
1. 2026-04-16-station-f-claude-memory-linkedin.md
2. 2026-04-15-rag-pipeline-thoughts-linkedin.md
3. (none / paste a URL for a post not in drafts)
```

Ask which one via AskUserQuestion.

If "none": prompt for raw post URL + create a stub `posted/` entry (no draft frontmatter to copy).

### Step 2.5: Citation gate (BLOCKING)

Read the chosen draft's frontmatter. If `citations_verified` is not `true`, **refuse to
log**:
```
This draft has not passed the citation hard-check (citations_verified ≠ true).
Run /post on it to resolve every factual claim first — it cannot be logged as posted.
```
Stop. Do not record metrics, do not move the file.

This enforces Louis's rule that nothing is treated as published until every factual claim
was sourced (or explicitly waived). For a **URL-only** entry (no draft), there is no
frontmatter to check — instead confirm with Louis that the sources comment was posted
before proceeding.

### Step 3: Collect metrics

Ask one at a time via AskUserQuestion:
1. Post URL (free-text, must look like https://... )
2. Impressions (integer)
3. Reactions total (integer, sum across like/celebrate/love/etc.)
4. Comments (integer)
5. Reposts (integer)
6. Notes (free-text, optional)

### Step 4: Compute derived fields

```
engagement_rate = (reactions + comments + reposts) / impressions
```

Round to 4 decimals. If impressions is 0, set engagement_rate to null.

### Step 5: Read draft frontmatter (if from drafts)

Extract: `hook_pattern`, `goal`, `media`, `audience`. Also count words in the body.

### Step 6: Append row to data-store.yaml

Read `$SOCIAL_HOME/data-store.yaml`. Parse YAML. Append to `posts` list:

```yaml
- id: <YYYY-MM-DD>-<slug>-<platform>
  platform: <linkedin|x>
  url: <post URL>
  posted_at: <ISO timestamp of logging — yes, logging time is a proxy when posted_at unknown>
  hook_pattern: <from draft frontmatter, or "unknown" if no draft>
  goal: <from draft frontmatter, or "unknown">
  media: <from draft frontmatter, or "unknown">
  audience: <from draft frontmatter, or "unknown">
  word_count: <count from body, or null if no draft>
  metrics:
    impressions: <int>
    reactions: <int>
    comments: <int>
    reposts: <int>
  engagement_rate: <float|null>
  notes: <string|null>
```

Write back. Validate the file still parses (re-read it and confirm yaml.safe_load succeeds).

### Step 7: Move draft to posted

If draft existed:
- Read draft frontmatter, add: `status: posted`, `url: <URL>`, `metrics: {impressions, reactions, comments, reposts}`, `engagement_rate: <float>`
- Move file: `mv $SOCIAL_HOME/drafts/<file> $SOCIAL_HOME/posted/<file>`
- Use Bash `mv`, do not use git mv unless asked.

If no draft (URL-only entry): create `$SOCIAL_HOME/posted/<id>.md` with frontmatter only.

**Run record:** if a run record exists for this post (`runs/<id>-run.yaml`), update it too:
set `status: posted`, `posted_at`, and `metrics{impressions, reactions, comments, reposts,
engagement_rate}` mirroring the data-store row. Leave the file in `runs/` (do not move it);
just update the fields.

### Step 8: Output

Print:
```
Logged: <id>
data-store.yaml updated (now N posts total)
Moved: drafts/<file> → posted/<file>
Engagement rate: X.XX%
```

### Step 9: Offer commit

Offer:
```bash
cd $SOCIAL_HOME && git add data-store.yaml posted/<file> && git rm drafts/<file> && git commit -m "feat(log): <id> — <impressions> imp, <reactions> rxn"
```

Wait for Louis to say yes.

## Common failure modes

- **`data-store.yaml` corrupted after write**: stop, show the error, do not commit
- **Impressions = 0**: still log, but set engagement_rate to null and add a note
- **URL collision**: append `-2`, `-3`, etc. to the id

## Non-goals

- Does NOT call any platform API
- Does NOT modify voice rules
- Does NOT generate posts (that's `/post`)

## Pulling the numbers instead of asking

Before prompting Louis for impressions/reactions/comments/reposts, try the repo's own
puller: `make x-pull` (X) wraps `tweetxvault sync`. It needs X session cookies
(`TWEETXVAULT_AUTH_TOKEN` + `TWEETXVAULT_CT0`, a `~/.config/tweetxvault/config.toml`, or a
browser on the box logged into x.com). As of 2026-08-05 none of those exists on TheBeast and
no vault context carries the names, so it fails with `Missing X session cookies`. When it
does, say so with the exact blocker and fall back to the manual prompt; do not report the
metrics as unavailable without trying the puller first.

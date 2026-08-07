# Design — pre-commit content-scrub hook for `~/Pro/` repos

Date: 2026-05-17
Status: spec, not built
Trigger: during a skill's red/blue iteration in `~/Pro/`, Claude (me) twice committed forbidden-list tokens into a tracked `SKILL.md` as illustrative examples. The pre-existing identity-guard hook checks only `git config user.email`/`.name` — it does not scan staged file content. The forbidden tokens reached `origin/main` and were only caught when I manually ran `grep -rinE` before the next commit. That manual scan is exactly the kind of thing the identity-guard hook should automate.

## Problem

`~/.config/git/template/hooks/pre-commit` blocks a commit only when the resolved git identity inside `~/Pro/` doesn't match the pro-tree noreply identity, or when name/email contains a forbidden string. It does NOT block a commit whose tracked file content contains forbidden strings (banners, comments, illustrative docs, AI-generated content with names in it).

Result: the strict-unlinked policy applies to "content that leaves this tree" in `~/Pro/CLAUDE.md`, but enforcement only covers identity metadata. Content has been leaking and getting caught by an out-of-band manual grep.

## Proposed

A second hook (or extension of the existing one) that, inside `~/Pro/` repos:

1. Runs on `pre-commit` and reads `git diff --cached --name-only -z`.
2. For each staged file (text only — skip binaries unless an env flag is set), greps the staged content for any pattern from a forbidden-strings list.
3. If any pattern is found, blocks the commit with a clear listing of `file:line: matched_token`.
4. Honors a bypass env var (`PRO_CONTENT_SCRUB=off`) for cases where forbidden strings legitimately appear (e.g., the recommended-list section of `~/Pro/CLAUDE.md` itself).

## Forbidden-list source

Three options, pick one:

| Option | Pro | Con |
|---|---|---|
| `~/.config/pro-tooling/forbidden-strings.txt` | Single source of truth shared with the pro-side port skill | Couples the hook to one specific layout |
| `~/.config/git-content-scrub/forbidden.txt` | Independent of port-repo | New file to keep in sync |
| Per-repo `.git-content-scrub` (gitignored) | Allows per-repo opt-in / variations | Each pro repo needs setup |

Recommended: the shared `forbidden-strings.txt` as the default, with a per-repo `.git-content-scrub` override if present. Falls back to no-op if neither exists (so the hook is safe to install into non-pro contexts via the same `init.templateDir`).

## Why not just run the pro-side audit's pattern check on staged content?

That's effectively what this is. The proposed hook is a thin shell layer that loads the same forbidden list, normalizes the same way (BOM/CRLF strip, regex/literal prefix handling), and applies it via `git diff --cached -U0 | grep -iE`. Could be implemented as either a standalone POSIX hook or by calling the existing audit script against a temporary tree of staged content (slower but DRY).

For speed, prefer the standalone POSIX form. The hook should run in well under 100ms per commit.

## Integration

Two ways to install:

1. **Append to existing `~/.config/git/template/hooks/pre-commit`.** Single hook file, two checks (identity + content). Risk: makes the hook longer; harder to disable one without the other.
2. **Separate hook script `pre-commit.d/02-content-scrub`,** with a small dispatcher in `pre-commit` that runs all `pre-commit.d/*` files. Cleaner but requires a dispatcher.

Recommended: option 1 for v1 (one more block in the existing hook). Move to option 2 if/when a third check is needed.

## Open questions for Louis

1. Should the content-scrub also include a fuzzy pass (Levenshtein), or is fuzzy only for port-time audit? Recommend exact-only for the hook (speed; commits are interactive).
2. Should it scan binary blobs too? Recommend skip — commits of binaries to pro repos should be reviewed manually anyway.
3. Bypass mechanism: env var (`PRO_CONTENT_SCRUB=off`) or sentinel string in commit message? Env var is consistent with `GIT_IDENTITY_GUARD=off`.

## Test plan (before merging)

- Commit a tracked file containing a forbidden token to a pro repo → expect block.
- Commit a tracked file containing only allowed content → expect pass.
- Bypass with `PRO_CONTENT_SCRUB=off` → expect pass with a stderr warning.
- Commit in a non-pro repo (outside `~/Pro/`) → hook no-ops.
- Commit a binary file (image) containing forbidden ASCII → expect pass (binaries skipped) unless `--allow-binary`-style env set.
- Forbidden file missing → hook prints warning, does not block.

## Defer or build?

Build when next pro port lands or when another content leak is caught manually. Until then, the manual `grep -rinE` before commit is documented in `~/Pro/CLAUDE.md` and works as a discipline layer.

---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
description: Phase 3 of /cite — patch the document with [N] markers + a Sources footer/section from approved claims. Handles auto-promote source swaps. Offers authority-map promotion gate.
---

# /cite-correct — Apply Approved Citations

## Trigger
`/cite-correct [<slug>]`. Set `S=~/.claude/skills/cite/scripts`; resolve `BUNDLE`, read `meta.yaml`.

## Step 1: Drift check
Re-hash the analyzed markdown (`meta.converted_md` or the original `.md`); compare to `.scan-hash`. Mismatch → abort: "source changed since /cite-diagnose; re-run /cite-diagnose."

## Step 2: Choose output target & profile
- **Editable original markdown** (input was `.md`, file writable): patch in place.
- **Converted/read-only input**: write `corrected.md` into `$BUNDLE` (the deliverable); never touch the non-md original.
- **Profile**: front matter (`marp:` or leading `---` block with `marp: true`) → `marp`; else `plain`.

## Step 3: Partition claims
- **apply**: status∈{approved, auto-approved, auto-promoted}
- **swap-only**: status==auto-promoted (replace existing URL, keep marker)
- **soften**: flagged-unsourceable with proposed_action==soften-language
- **skip**: everything else (rejected/needs-rework/flagged-*)

If apply ∪ soften ∪ swap-only is empty → "nothing to apply", exit.

## Step 4: Number & build patch
Group apply claims by slide/section. Assign `[N]` per group, shared URL shares N, ordered by line.
- Append ` [N]` after each claim sentence.
- `auto-promoted`: replace only the URL in the existing Sources entry (marker unchanged).
- `soften`: replace the claim sentence with `proposed_claim_update`.
- Sources rendering by profile:
  - **marp**: `<small>Sources : [1] [Name](url) · [2] [Name](url)</small>` as the slide's last line before `---`/EOF.
  - **plain**: a `## Sources` section per document section (or one at EOF) with `1. [Name](url)`.

## Step 5: Preview diff gate
Write `$BUNDLE/apply-preview.diff` (`diff -u` original vs patched). Show it; AskUserQuestion Apply / Show-detail / Abort.

## Step 6: Apply
On Apply: edit the target (surgical Edits, not full rewrite). Best-effort verify:
- If profile==marp AND the repo has a Makefile with a `check` target (`grep -q '^check:' Makefile`): run `make check check-citations html` and report pass/fail. (This covers the M2T case unchanged.)
- Else: run `python3 -c "import pathlib,sys; t=pathlib.Path('<target>').read_text(); sys.exit(0 if t.count('[')>=0 else 1)"` as a trivial integrity touch and report "no project build detected; skipped deep verification".
Never revert on failure — report and leave for the user.

## Step 7: Authority promotion gate
For each per-run `authority-map.md` proposal, AskUserQuestion promote yes/no/skip. On yes, append under the right tier in `~/.claude/skills/cite/memory/authority-map.yaml` AND its `.md` mirror — OR, if a project overlay exists and the user prefers, into `docs/references/authority-map.yaml`. Default: the global base map. Project overlays hold only repo-specific entries. After edits: `python3 $S/lint_authority_map.py` must pass; then commit the Setup change:
```bash
cd ~/Setup && git add skills/cite/memory/authority-map.* && git commit -m "chore(cite): promote <N> publishers from <slug>"
```

## Step 8: Final report
Counts of cited / swapped / softened, verification result, promotions, bundle path.

## Common failure modes
- Hash drift → abort.
- Stale line number → abort that claim, apply the rest.
- `make check` overflow (M2T) → report, do not revert.

## Non-goals (v1)
- No auto-retry of build failures. No revert. No claim-value changes without an approved flagged decision.

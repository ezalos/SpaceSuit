---
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion
description: Orchestrates /cite-diagnose, /cite-remediate, /cite-correct on any document (md/pdf/html/docx/url) with review gates between phases. Detects existing state and resumes.
---

# /cite — Source Verification Orchestrator

## Trigger
`/cite <path-or-url>`

Set `S=~/.claude/skills/cite/scripts`.

## Step 1: Resolve state
- `BUNDLE=$(python3 $S/bundle_path.py "<target>")` (does not create it).
- Infer phase from bundle contents:

| State | Phase | Action |
|-------|-------|--------|
| BUNDLE missing | not started | Diagnose |
| exists, all claims `uncited`/`pending-audit` | diagnosed | Remediate |
| exists, some claims finalized & some flagged | remediated | prompt Correct |
| `apply-preview.diff` present and applied | done | report nothing to do |

Confirm the inferred phase with AskUserQuestion.

## Step 2: Diagnose
Invoke `/cite-diagnose <target>` via Skill. Then show outline.md summary and ask: proceed to remediate? [yes/no/abort].

## Step 3: Remediate
Invoke `/cite-remediate <slug>` via Skill. Relay the auto-approved/flagged/unsourceable summary and stop:
"Review flagged claims, edit their YAML status, reply 'go' to correct."
Wait. On `go` → Step 4. On `abort` → stop.

## Step 4: Correct
Invoke `/cite-correct <slug>` via Skill. Its diff-preview is the final gate. Relay its report.

## Common failure modes
- Hash drift mid-pipeline → surface the abort; suggest re-running /cite-diagnose.
- User declines after diagnose → leave bundle, exit cleanly.

## Non-goals (v1)
- One document per run (no batching). No parallel phases.

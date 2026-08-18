---
name: secrets
description: Use when adding an API key or secret to a project, creating or editing a .envrc, wiring a new repo's environment, or when a command fails on a missing secret/env var (e.g. "add my HF token", "set up secrets here", "HF_TOKEN not set", "where does this key go"). Routes config vs vaulted secrets, generates pass:// ref lines, and diagnoses resolution failures.
---

# Secrets: wiring and diagnosing

One test decides everything: **would leaking it grant access or cost money?**
Yes = secret (Proton vault, `pass://` ref). No = config (plain export).

Gray zone defaults to config unless the value alone grants access:
`client_secret` = secret, `client_id` = config; AWS access keys = secret,
account ID = config; webhook URL with an embedded token = secret.
Exception: anything ALREADY in a vault is always a ref, even config-shaped.

## Wire a repo (choose the mode first)

- **Project repo** (dev work, arbitrary commands need real values):
  `proton-envrc <ctx> --resolve >> .envrc && direnv allow`
  Refs resolve into the shell env on cd. The `use proton <ctx> resolve` line
  must stay AFTER the export lines.
- **Capability repo** (a tool wraps every secret use):
  `proton-envrc <ctx> >> .envrc && direnv allow`
  Refs stay refs. The tool (or its front door) runs `secrets run -- <cmd>`.

Contexts = PAT files: `ls ~/.claude/channels/proton-pass/*.pat`. One context
= one vault. Cross-context refs fail `denied` by design; do not work around.

## Add a NEW secret

1. Classify it (test above). Config: plain `export KEY=VALUE`, done.
2. Secret + Louis has bandwidth to vault it: he adds an "API Credential" item
   (var name in the `API Key` field, value in the `Secret` field) via the Pass
   app; agent tokens are READ-ONLY, so an agent can never do this step.
   Then regenerate by replace, not append: drop the previously generated lines first (or generate to a temp file and swap), then `proton-envrc <ctx> [--resolve] >> .envrc`.
3. Secret, not vaulted yet (tolerated interim): plaintext in the gitignored
   0600 `.envrc`, tagged on the same line:
   `export FOO_KEY=xyz   # not-vaulted`
   The periodic audit picks the tag up; never copy the value elsewhere.

## Migrate plaintext -> ref (the value is already in a vault)

Compare in-process (never print), then swap and delete the plaintext:
backup the file 0600 under `~/.local/state/secrets-migration/<date>/` first.
A vaulted value must have NO plaintext copy anywhere afterward.
Compare BOTH exact and trailing-whitespace-stripped sha256: wire only exact
matches. Stripped-only match = vault data-entry defect (stray byte in the
`Secret` field — seen 5x on 2026-07-13); Louis fixes the vault first, then
re-compare. Also check the item's `API Key` field against its title;
mismatched labels are the same defect class.

## Diagnose

- `secrets check` in the repo: per-ref `ok | missing | denied | error`. Since
  2026-08-17 it inspects the resolved value in-process: an empty value or the
  literal `<concealed by Proton Pass>` placeholder reports as `error`, never ok.
- Command fails on a missing var in an agent shell / cron / script (direnv
  did not run there): `secrets run -- <same command>`.
- `secrets run` is fail-closed for the WHOLE `.envrc`: ONE unresolvable ref
  refuses the exec (`... did not resolve to a usable value -- refusing to run
  with a poisoned env`), even for a command that never reads it. Deliberate —
  no partial environment (design: GroundControl
  `docs/plans/2026-07-09-cross-functional-capabilities-design.md`). So a repo
  with one stale ref cannot `secrets run` anything: `secrets check` names the
  offender, then fix or delete that ref instead of routing around it. Do not
  read this refusal as "the vault is down" — it fires on a single bad ref.
- `denied` = wrong context for that vault. That is enforcement, not a bug.
- Offline: resolution fails fast and loud; refs stay refs. Retry online.
- `proton-agent: login failed (token expired or revoked?)` almost never means
  the token: a stale session store poisons fresh logins (~daily, seen
  2026-08-16/17). proton-agent now quarantines `.session` and retries once by
  itself — if it STILL fails after the "quarantined ... retrying" stderr line,
  then suspect the PAT.
- All resolution goes through `pass-cli item view` (reliable for every field
  kind). Never reintroduce `pass-cli run` env-injection: it substitutes the
  concealment placeholder for extra fields (Text always, Hidden per-session;
  pass-cli 2.2.5 defect — diagnosis + upstream issue draft in GroundControl
  `docs/plans/2026-08-16-proton-agent-text-field-resolution-handoff.md`).

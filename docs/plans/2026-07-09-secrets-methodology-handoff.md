# Secrets methodology: handoff for a fresh design session

Status: resolved, 2026-07-13. The fresh session ran; all five challenges are
decided in `2026-07-13-secrets-methodology-design.md`, which supersedes the
"Decisions under challenge" section below. The evidence and incident sections
remain the record of what was measured and what went wrong.

Original status: handoff. This document seeds a clean-context session to design Louis's
secrets methodology. It supersedes parts of
`2026-07-09-cross-functional-capabilities-design.md` (see "Decisions under
challenge"). The prior session accumulated enough scope drift, plus two
verification bugs and one transcript leak, that Louis asked for the design to
be re-examined from scratch rather than iterated in place. Correct call.

Read this whole file before proposing anything. The evidence section is
measured fact and should not be re-derived. The decisions section is NOT
settled: each entry names the challenge the fresh session must actually run,
not rubber-stamp.

## Requirements, in Louis's terms

1. **A brand-new repo, started from scratch, must come with the methodology.**
   A session there has no `.envrc`, no project CLAUDE.md, no auto-memory. Today
   it reads global `CLAUDE.md`, which teaches `export KEY=VALUE` in a
   gitignored `.envrc`, and correctly follows it into the anti-pattern. This
   was the original complaint and it is a knowledge-surface problem, not a
   plumbing problem.
2. **Not everything goes through Proton.** Config values (volumes, hosts,
   ports, model names, feature flags) are environment variables, not secrets.
   Forcing them through a vault is friction without security. The design needs
   an explicit secret-vs-config taxonomy.
3. **For secrets that ARE in Proton: no plaintext copy anywhere, ever.**
   Duplication is the failure mode that makes rotation silently incomplete.
   Legacy plaintext copies of vaulted secrets get dropped, not kept as
   fallback.
4. Qualities, in Louis's words: secure, high hygiene, reliable, low friction.
   "100% seamless for me as a user." No command prefix a human must remember
   (this is also a standing CLAUDE.md rule).
5. `.envrc` + direnv stays as the per-directory mechanism. Refs may live in it.
6. The anon domain's tooling must NOT be lightly accessible. Sequestration
   (project-scoped skill inside its own tree, own Proton context) was endorsed
   explicitly and is not up for re-litigation.

## Verified facts (measured this session; reuse, don't re-derive)

- **Auto-memory is project-scoped.** `~/.claude/projects/<cwd-slug>/memory/`.
  All Proton knowledge sat in the Setup slug, unreachable elsewhere. Global
  surfaces are exactly two: `~/.claude/CLAUDE.md` and deployed skill
  frontmatter (name + description).
- **direnv does not run in agent Bash shells.** Verified: a shell that `cd`s
  into `~/42/TheStables/network/domains` keeps `DIRENV_DIR=-/home/ezalos/Setup` and
  lacks `CLOUDFLARE_API_TOKEN` while two other `CLOUDFLARE_*` vars are present.
  Documented `cd <repo> && ./tool.sh` invocations therefore fail
  half-configured for agents, which invites improvisation. Any design must
  work in shells direnv never touched.
- **Census (2026-07-09): 89 plaintext secret-shaped exports across 23 files.**
  Heaviest: `42/icono-web/.envrc` (35, incl. AWS prod +
  `ICONO_API_PASSWORD_PRODUCTION`), `42/Markdowns2Teach` (7),
  `42/Hush_project` (6, Scaleway), `42/icono-test-e2e` (5),
  `42/TheStables/network/domains` (4, Cloudflare + Google OAuth). Exactly one repo uses
  `pass://` refs: `42/web_wm_onnx`. Census script:
  scratchpad `census.py` pattern: literal RHS, len>=20, shannon>=3.5,
  path/url/version excluded.
- **Proton machinery that already exists and works:** 4 per-context read-only
  PATs on disk (general, money, alakazam, + the anon context) under
  `~/.claude/channels/proton-pass/`; `proton-agent` (auto-login wrapper, on
  PATH); `proton-envrc <ctx>` (generates a ref-style `.envrc` from vault
  items); **`proton-agent run -- <cmd>` already resolves `pass://` refs in the
  child's env.** A resolver does not need to be built. Item format: "API
  Credential" custom items, value in `Secret` field, env var name in `API Key`
  field. Runbook: `~/Setup/plans/2026_06_29-proton_secret_manager.md`.
- **Consumer map for Setup's remaining plaintext:** `DOCKER_USERNAME/PASSWORD`
  -> `42/CurriculumVitae`; `OPENCLAW_GATEWAY_TOKEN` -> `~/openclaw` (compose +
  render.yaml); `CLOUDFLARE_EZALOS` -> develle tunnel work; `Alakazam_*` ->
  unknown consumer; `.secrets.sh` (7 exports) -> `send-email` sources it,
  `nat_manager/nat.py` expects `SFR_BOX_PASSWORD` from it.
- **`<SEQUESTERED_TREE>/njalla-dns/.envrc` was a hand-rolled loader** that
  grepped `~/Setup/.envrc` for its tokens, independent evidence that a shared
  loader matches a real need (CLAUDE.md line 88 pattern, implemented once by
  hand).
- **`~/.claude/skills/link-njalla-domain` is a dangling symlink** (points at a
  `~/Pro/` path deleted when the tree moved). Still not deleted. Decision
  stands: delete, don't repair; skill moves inside the sequestered tree.

## Landed changes (done, verified; do not redo)

- **Containment, 2026-07-09:** `NJALLA` + `CLOUDFLARE_NJALLA` moved from
  `~/Setup/.envrc` (machine-global, exported into every shell because sessions
  launch from `~/Setup`) into `<SEQUESTERED_TREE>/njalla-dns/.envrc`. Values
  verified byte-identical (SHA-256 of RHS vs backup). Both files 0600.
  Backups: `~/.local/state/secrets-migration/2026-07-09/` (0600). Verified:
  scrubbed shells in `~/Setup` and `~/42` see 0 anon tokens; a shell in the
  njalla dir sees all 4 (lengths 40/53).
- Spec committed at `92170d4`:
  `docs/plans/2026-07-09-cross-functional-capabilities-design.md`. Now marked
  as partially superseded by this handoff.

## Incidents (both mine; the next session inherits the lessons)

1. **Transcript key leak.** A masking script handled `KEY=value` lines but
   passed comments through raw; `~/Setup/.envrc` comments held an Anthropic
   key and an OpenRouter key in plaintext, and both printed into the session
   transcript. **ROTATION STILL PENDING as of this handoff: Anthropic key +
   OpenRouter key. Human action; do not let it fall off.** Remove the comments
   only after rotation. Three secret-shaped comments remain in that file.
2. **Vacuous verification, twice.** (a) `$SCRUB` holding `env -u ...` expanded
   unsplit under zsh, so the check tested nothing and printed the hoped-for 0;
   (b) `grep -c ... || echo 0` double-printed because grep -c emits 0 AND
   exits non-zero. Rule for the fresh session: every verification must be able
   to fail, and should be observed failing once before it is trusted.

## Decisions under challenge (the point of the restart)

1. **"Values never in the environment; refs only; tools resolve."** The prior
   session's central rule, adopted after the leak. CHALLENGE: it was
   calibrated on capability tooling and compared eager resolution against
   "values nowhere." For the ~20 project repos the real baseline is plaintext
   on disk AND in env; eager direnv resolution of refs (values in shell env,
   directory-scoped, nothing at rest) strictly improves on that baseline and
   is the only zero-friction option for arbitrary commands (`uv run python
   train.py` needing `HF_TOKEN`). Front doors cover only 3 capability repos.
   The design likely needs TWO modes: tool-side resolution for capability
   tooling and agent shells; something seamless for interactive dev in project
   repos. Also check Louis's actual launch habits: if Claude sessions always
   start from `~/Setup` (tmux), project-repo direnv values may never enter
   agent environments at all, which changes the leak calculus.
2. **`src_secrets` as a subproject.** CHALLENGE: `proton-agent run` already
   resolves refs. What remains is parsing `./.envrc` for `use proton <ctx>` +
   ref exports in a direnv-less shell, a ~50-line shim, possibly not a new
   component at all. The walk-up (`find_envrc`) was already conceded YAGNI:
   front doors `cd` first, so only `./.envrc` matters.
3. **The knowledge surface was underweighted.** The direct answer to
   requirement 1 is: rewrite the global CLAUDE.md "Environment & secrets"
   section around the taxonomy (config = plain export; secret = Proton ref;
   vaulted = no plaintext copy anywhere; `uv` never bare `python`), plus
   probably a global `secrets` skill whose description fires on "add an API
   key / set up .envrc / new secret," so the methodology surfaces at the
   moment of need rather than relying on prose recall. `proton-envrc` is the
   existing wiring tool to point at.
4. **Unmeasured reliability.** Nobody measured `proton-agent` resolution
   latency, cold-session cost, or offline behavior. "Reliable" is a stated
   requirement. Measure before choosing between eager and lazy resolution;
   latency was the original reason a cache was discussed and then dropped.
5. **Migration scope.** The 89 legacy secrets are NOT all migration targets:
   many belong to dead projects and need deletion, not vaulting; some are
   config misclassified by the census. Audit liveness first. Production
   credentials (icono-web) are their own project with their own rollback
   story. Only secrets Louis actively wants in Proton get migrated, and each
   migration ends by deleting the plaintext copy (requirement 3).

## Decisions endorsed (keep unless new evidence)

- Sequestration of the anon domain: project-scoped skill in its own tree,
  separate Proton context/vault so `general` gets denied by authorization, not
  by prose. Delete the dangling global symlink.
- Front doors on PATH for the 3 capability repos (develle DNS/access, NAT),
  each `cd`-ing internally, the pattern `send-email` and `pull-uploads`
  already prove.
- `deploy skills` integrity check: dangling symlink = hard error.
- Migration order: prove on `money` (no dependents) before anything that
  matters; the anon context last.

## Queued, separate from this design

- Wrap-up documentation-debt loop (part C): its own spec. Five specimens
  recorded in the committed spec's "Out of scope" section; their shared shape
  is *confidently wrong docs*, not missing docs.
- Deck capability: handoff doc to be written in `~/42/Markdowns2Teach`, where
  the context lives.
- `~/.claude/memory/MEMORY.md` global-memory file is vestigial; the plans-path
  rule in Setup's MEMORY.md is stale (repo convention is `docs/plans/`).

## How to run the fresh session

Start in `~/Setup`, point the session at this file, and enter brainstorming
before plan mode. Challenge entries 1-5 above in order; measure before
deciding entry 4. Do not touch vault contents without Louis (agent tokens are
read-only anyway). Do not print `.envrc` contents raw: mask literals AND
comments, or print names/lengths only.

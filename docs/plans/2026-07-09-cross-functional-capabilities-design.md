# Cross-functional capabilities: discovery, secrets, and blast radius (design spec)

Status: superseded in part, 2026-07-09 (same day). Louis asked for the secrets
design to be re-examined from clean context; the evidence here stands, but the
central resolution rule, the src_secrets shape, and the knowledge surface are
under challenge. See `2026-07-09-secrets-methodology-handoff.md` for what holds,
what is challenged, and why. One containment change already landed, see
"Already done" below. Surfaced 2026-07-09 from a single complaint: sessions
outside `~/Setup` behave as if the Proton Pass secret manager does not exist,
and each one falls into the same traps.

Investigation showed the complaint was a symptom. The tool is fine. The
integration is inverted.

**A note on placeholders.** `<SEQUESTERED_TREE>` and `<anon-vault>` are not
TBDs. This repo is public, and the whole point of the sequestered tier below is
that the anonymous identity's paths and vault names do not appear in it. The
concrete values are known to Louis and to any session already inside that tree,
which is exactly the property the spec argues for. Treat the placeholders as
part of the design, not as gaps to fill in.

## Evidence

Measured, not assumed:

| Observation | Value |
|---|---|
| Project `.envrc` files on the machine | 48 |
| Of those, using `use proton` | 1 (`web_wm_onnx`) |
| `pass://` refs in `~/Setup/.envrc` | 0 (8 plaintext exports) |
| `pass://` refs in `~/Setup/.secrets.sh` | 0 (7 plaintext exports) |
| `pass://` refs in `main-domain-infra/.envrc` | 0 |
| Proton context tokens minted and present | 4 (general, money, alakazam, anon) |
| Hooks configured in `settings.json` | 0 |

Two structural facts explain the rest.

**Memory does not cross project boundaries.** Auto-memory lives at
`~/.claude/projects/<cwd-slug>/memory/`. Every fact written about Proton Pass
sits in the `-home-ezalos-Setup` slug. A session in `~/42/foo` cannot reach it.
`~/.claude/memory/` exists but is vestigial: 425 bytes, untouched since
February, never injected. Only two surfaces are global in every session:
`~/.claude/CLAUDE.md`, loaded verbatim, and the `name` plus `description`
frontmatter of every skill in `~/.claude/skills/`.

**direnv does not run in the shells agents use.** Verified directly: a `Bash`
tool call that `cd`s into `~/42/TheStables/network/domains` reports
`DIRENV_DIR=-/home/ezalos/Setup`. The session's direnv state is inherited from
wherever Claude Code was launched, and `cd` inside a non-interactive shell does
not re-trigger direnv. So `CLOUDFLARE_API_TOKEN` is unset while two *other*
`CLOUDFLARE_*` variables are set.

That last detail is the trap. The documented invocation for
`link-develle-domain` is `cd ~/42/TheStables/network/domains && ./cloudflare-dns/dns.sh
sync`. For an agent it fails, and it fails *half-configured*: token-shaped
variables are present in the environment, so the agent concludes it is
authenticated and improvises around the tool it was told to use.

Discovery layered on top of a broken invocation is worse than no discovery. The
agent finds the door, the door is stuck, and it climbs through the window it can
see.

## Root cause

`~/Setup/.envrc` was functioning as a machine-global secret bus. It exported
`NJALLA`, `CLOUDFLARE_NJALLA`, `CLOUDFLARE_EZALOS`, `OPENCLAW_GATEWAY_TOKEN` and
`DOCKER_PASSWORD` into the environment of every process on the machine, because
Claude Code sessions launch from `~/Setup`. Other repos depended on this:
`<SEQUESTERED_TREE>/njalla-dns/.envrc` read `$NJALLA` and `$CLOUDFLARE_NJALLA`
from the ambient environment, with a hand-rolled fallback that grepped
`~/Setup/.envrc` directly.

The consequence: the anonymous identity's Cloudflare credential was the most
ambient secret on the machine, reachable from every subagent, in every public
repo, at every cwd. `proton-agent` was built precisely so that "a compromise in
one project cannot authenticate against another context's vault." The deployed
reality was the exact inverse of the design.

Adoption failed for a second reason, documented in `CLAUDE.md` itself:

> Prefer wiring config into the tool so it "just works" over documenting a
> command prefix a human must remember. A missed prefix is a silent failure.

`proton-agent run -- <cmd>` is that prefix. The recommended usage violated the
rule it was written under.

## Principles

1. **A `.envrc` holds references, never values.** A `pass://` ref is inert. It
   survives a `cat`, an `env` dump, a careless paste into a transcript. A
   plaintext token does not.
2. **Resolution belongs in the tool, never in direnv and never in a prefix.**
   direnv is an interactive convenience. Correctness comes from a loader that
   works in any shell.
3. **Discovery scope must match blast radius.** A capability that can leak an
   identity must not be discoverable from a session that lacks the context to
   handle it safely.
4. **`uv`, never bare `python`.** Every Python entry point runs under `uv run`.

Principle 2 is not new. It is `CLAUDE.md` line 88, which already says to rely on
direnv for interactive convenience and on a loader for correctness everywhere
else. The loader was never built, so each repo hand-rolled its own or went
without.

## Threat model, and why refs beat eager resolution

An alternative was considered and rejected: make `use proton <ctx>` resolve
refs eagerly inside direnv, exporting real values into the shell. It is
maximally seamless and it does shrink blast radius from machine-global to
directory-scoped.

It was rejected because it does not address the dominant threat.

- *Accidental disclosure by an authorized process.* A subagent dumping `env` for
  debugging, a `set -x` trace, a config file `cat`. This is not hypothetical: on
  2026-07-09 an agent-authored masking script printed two API keys from
  `~/Setup/.envrc` comments into a transcript, because the masker handled
  `KEY=value` lines and passed comments through. Refs make this class of leak
  impossible. Values in the environment do not.
- *A malicious process on the machine.* Anything that can read a shell's
  environment can also read `~/.claude/channels/proton-pass/` and call
  `proton-agent` itself. Eager resolution and lazy resolution are equivalent
  here, so this threat does not discriminate between the options.

Only the accidental case distinguishes them, and there, values-never-in-env wins
outright. Rejecting eager resolution also removes a per-`cd` Proton round trip,
which removes the need for a resolved-secret cache, which removes resolved
secrets from disk entirely. The stricter option is also the simpler one.

## Architecture

Three layers.

```
  .envrc                     pass:// refs only, inert, gitignored
     |
     +-- direnv (interactive) ----> exports refs into Louis's shell
     |
     +-- src_secrets (any shell) -> parses .envrc, resolves via proton-agent,
                                    execs child with real values in its env only
     |
  front doors on PATH        develle-dns, develle-access, open-port, ...
                             each calls the loader; caller supplies no prefix,
                             performs no cd, and needs no direnv
```

The same `.envrc` is read by both resolvers. Neither path requires a prefix.
Values exist in exactly two places: Louis's interactive shell never (it holds
refs), and a tool's own process for the duration of one call.

### Component: `~/Setup/src_secrets/`

A `uv`-managed subproject, sibling to `src_dotfiles`, same conventions, with
tests. Public repo, zero secrets in the code.

Python API:

- `find_envrc(start: Path) -> Path | None` walks up from `start` to the nearest
  `.envrc`, stopping at `$HOME`.
- `parse(envrc: Path) -> tuple[str, dict[str, str]]` returns the proton context
  declared by `use proton <ctx>` (defaulting to `general`) and the mapping of
  variable name to `pass://` ref. Non-ref exports are ignored, not resolved.
- `resolve(refs: dict, ctx: str) -> dict` batches the lookups through
  `proton-agent`, returning name to value. Never logs a value.
- `run(argv: list[str], cwd: Path) -> int` composes the above and `execvpe`s.

CLI, `secrets`:

- `secrets run -- <cmd>` the primary entry point. Front doors call this.
- `secrets check` resolves every ref reachable from the cwd and reports
  `name: ok | missing | denied`, values never printed. This is the diagnostic
  that tells an agent what is wrong instead of letting it guess.
- `secrets names` lists variable names the cwd would provide. No values, no
  refs.

There is deliberately no `secrets export` that prints shell assignments. Such a
command exists only to be misused, and it re-creates the leak that refs prevent.

### Component: front doors

One cwd-agnostic binary per capability, in `~/Setup/bin`, on PATH everywhere
Setup deploys. Each is a thin wrapper: it knows its own repo path, `cd`s there
internally, and `exec`s through `secrets run --`. `send-email` and
`pull-uploads` already work this way and are the proof the pattern holds.

| Front door | Wraps | Replaces the documented invocation |
|---|---|---|
| `develle-dns` | `main-domain-infra/cloudflare-dns/dns.sh` | `cd ~/42/TheStables/network/domains && ./cloudflare-dns/dns.sh` |
| `develle-access` | `main-domain-infra/cloudflare-access/access.sh` | `cd ~/42/TheStables/network/domains && ./cloudflare-access/access.sh` |
| `open-port` | `Setup/nat_manager/nat.py` via `uv run` | `cd ~/Setup/nat_manager && uv run python nat.py` |

No skill, README, or CLAUDE.md may instruct a `cd` into a capability's repo
after this lands. The `cd` is the bug.

### Component: discovery tiers

| Tier | Capabilities | Surface | Rationale |
|---|---|---|---|
| Ambient | secrets, `send-email`, `share-file`, `pull-uploads`, `notify-louis` | global skill plus PATH binary | low blast radius, wanted everywhere |
| Gated | `open-local-port`, develle domains and access | global skill; confirmation gates live in the tool | touches the public internet |
| Sequestered | the anon domain's DNS and tunnels | project-scoped skill inside the sequestered tree; never global | its guardrails live in a directory-scoped `CLAUDE.md` that only loads inside that tree |

The sequestered tier is the important one. `~/.claude/skills/link-njalla-domain`
is currently a **dangling symlink** pointing at `~/Pro/njalla-dns/skill`, a path
that no longer exists. It must be **deleted, not repaired**. The skill moves to
`<SEQUESTERED_TREE>/njalla-dns/.claude/skills/`, a project-scoped skill
directory. That pattern is already in use elsewhere in that tree.

A global skill can fire from any cwd. If it fires from `~/42/foo`, the
directory-scoped `CLAUDE.md` carrying its hard-stop rules is not loaded, and the
agent operates without them. Sequestration is the only structural fix. Carrying
the guardrails inside the skill body was considered and rejected: it relies on
the model reading and obeying prose, which is the failure mode this whole spec
exists to remove.

Once the anon token lives in its own Proton vault, Setup's `general` PAT cannot
decrypt it. Discovery and authorization then fail independently. Neither relies
on an agent choosing to behave.

### Component: `CLAUDE.md` corrections

The global `~/.claude/CLAUDE.md` currently teaches the anti-pattern. Required
edits, deployed through the `claude_md` dotfile:

- The "Environment & secrets" bullet says secrets go in a gitignored `.envrc`
  using `export KEY=VALUE`. Change to: `.envrc` holds `pass://` refs; values
  live in Proton; tools resolve through `secrets run`.
- Add the `uv` rule. `uv run`, never bare `python` or `python3`, for any project
  entry point.
- Remove any guidance recommending a `proton-agent run --` prefix as the normal
  path. Keep `proton-agent` as the underlying mechanism and as the escape hatch
  for third-party binaries that read the environment directly.
- Add a short capability index: the names of the front doors and the skills that
  wrap them, so a session hand-rolling `curl` against Cloudflare has already
  read that `develle-dns` exists.

### Component: deploy-time integrity check

`python -m src_dotfiles deploy skills` gains a check: every entry it deploys, and
every existing entry under `~/.claude/skills/`, must resolve. A dangling symlink
is a hard error, not a warning. `link-njalla-domain` rotted silently for an
unknown number of weeks, and nothing noticed because nothing looked.

## Data flow

Interactive, Louis in a terminal:

1. `cd <SEQUESTERED_TREE>/njalla-dns`. direnv loads `.envrc`, exports
   `CF_API_TOKEN='pass://<anon-vault>/<id>/Secret'` and sets the proton context.
2. `tunnel.sh up foo`. The script calls `secrets run -- <real work>`.
3. The loader sees the ref already in the environment, resolves it for the child
   process, and execs. Louis typed no prefix and the token never entered his
   shell.

Agent, `Bash` tool, no direnv:

1. Agent runs `develle-dns sync` from any cwd.
2. The front door `cd`s to its repo and calls `secrets run --`.
3. The loader finds no refs in the environment, walks up to the repo's `.envrc`,
   parses the context and refs, resolves them, and execs.

Both paths converge on the same `.envrc`. The agent path never depended on
direnv, which is why it works.

## Failure modes

| Condition | Behaviour |
|---|---|
| Proton unreachable | `secrets run` exits non-zero naming the unresolved variables. No partial environment. Front doors do not proceed with a half-configured env, which is the current failure. |
| PAT expired or revoked | Same, with the remediation command from `proton-agent`'s existing error text. |
| Ref points at a vault the context cannot read | Exit non-zero, `denied`. This is the expected result when a `general`-context caller reaches for the anon vault, and it is the enforcement mechanism, not an error to work around. |
| `.envrc` present, no refs | Loader is a no-op passthrough. Repos with no secrets keep working. |
| A variable is set in the environment already and is not a ref | Passed through untouched. Migration can proceed one variable at a time. |

The last two make migration incremental and reversible. A repo can hold a mix of
plaintext exports and refs while it is being converted.

## Testing

`src_secrets` ships with tests, run under `uv`:

- `find_envrc` walks up, stops at `$HOME`, returns `None` above it.
- `parse` extracts the context, extracts refs, ignores non-ref exports, handles
  quoting and `export` prefixes, tolerates comments.
- `resolve` is tested against a fake `proton-agent` on PATH. No network, no
  real vault.
- `run` execs with resolved values present and refs absent in the child env.
- A regression test asserts no code path writes a resolved value to a log, a
  file, or stdout. This is enforced by a fake `proton-agent` returning a
  sentinel value and grepping all captured output for it.

End-to-end, per capability, before its front door is considered done: invoke it
from a scrubbed environment in an unrelated cwd (`env -u ... develle-dns list`)
and confirm success. That is the exact scenario that fails today.

**A verification note, from this session.** The first attempt to verify the
containment change put `env -u NJALLA ...` in a shell variable and let zsh
expand it unquoted. zsh does not word-split, so the command never existed,
`grep -c` counted an empty stream, and the check reported the `0` it was looking
for. It passed vacuously. Every verification in the migration below must be
written so that it can fail, and must be observed failing at least once before
it is trusted.

## Migration

Ordered so that nothing touches a secret that matters until the loader has been
proven on one that does not.

0. **Already done, 2026-07-09.** `NJALLA` and `CLOUDFLARE_NJALLA` moved out of
   `~/Setup/.envrc` into `<SEQUESTERED_TREE>/njalla-dns/.envrc`, where they are
   consumed. Values verified byte-identical by SHA-256 of the right-hand side
   against a backup. Both files chmod 0600, previously 0664. Backups in
   `~/.local/state/secrets-migration/2026-07-09/`. Blast radius went from every
   shell on the machine to one directory. No Proton involved, fully reversible.
1. Build `src_secrets` with its test suite. No production consumer yet.
2. Convert the `money` context first. Nothing depends on it, so a total failure
   costs nothing. Verify with `secrets check` from a scrubbed environment.
3. Convert `general` and `alakazam`. Verify each consumer end-to-end.
4. Build the three front doors. Update their skills to drop the `cd`.
5. Convert the anon context last, and only after moving its skill into the
   sequestered tree and deleting the global symlink. Verify that a `general`
   context caller receives `denied`, which is the point of the exercise.
6. Delete `~/Setup/.secrets.sh` once `send-email` and `nat.py` read through the
   loader.
7. Apply the `CLAUDE.md` corrections and the `deploy skills` integrity check.

Step 5's verification is the acceptance test for the whole spec. If a session in
`~/Setup` can still reach the anon credential, the design has failed.

## Outstanding, not blocking

- **Rotate two credentials.** An Anthropic API key and an OpenRouter API key sat
  in plaintext comments in `~/Setup/.envrc` and were printed into an agent
  transcript on 2026-07-09. They are burned. Rotation is a human action; agent
  tokens are read-only and cannot write vault items. The comments should be
  removed after rotation, not before, so the old values remain recoverable if a
  rotation goes wrong.
- `~/Setup/.envrc` still holds `DOCKER_USERNAME`, `DOCKER_PASSWORD`,
  `OPENCLAW_GATEWAY_TOKEN`, `CLOUDFLARE_EZALOS` and two `Alakazam_*` values, and
  three secret-shaped comments. These are consumed by `42/CurriculumVitae`,
  `~/openclaw` and `main-domain-infra`. They are in scope for step 3, not for
  the containment change.
- The `share-file` skill documents a `.zshrc` alias that does not exist in any
  tracked dotfile. The reliable invocation is the direct script path. A
  candidate for the same front-door treatment.

## Out of scope

**The deck capability.** Two engines exist (`frontend-slides`, ascendant; Marp
via `~/42/Markdowns2Teach`, declining), sources live in whichever project the
deck is about, citation verification overlaps the `/cite` family, and visual
generation overlaps `/visual`. Building a deck is an iterative loop of reading,
critique, and requesting more source material, not a one-shot command. That is a
product question, not a missing front door, and it should be answered from
inside `~/42/Markdowns2Teach` where the context lives. A handoff document will
be written there.

**The wrap-up documentation-debt loop.** Louis's original request included
making `/wrap-up` treat a wrong-usage conclusion or a discovered footgun as a
signal to improve documentation. That is a separate spec, to be written next.
This session produced five specimens for it, all real:

1. `~/.claude/skills/link-njalla-domain` dangled at a path deleted weeks ago.
   Nothing noticed.
2. The documented `cd ~/42/TheStables/network/domains && ...` invocation fails for agents
   and fails half-configured, which invites improvisation.
3. `~/Setup/.envrc` acted as a secret bus, and `njalla-dns/.envrc` hand-rolled
   the very loader `CLAUDE.md` line 88 prescribes.
4. `CLAUDE.md` recommends `proton-agent run --`, a prefix, while also forbidding
   prefixes on the grounds that a missed one fails silently.
5. `MEMORY.md` states plans always go in `./plans/`; this repo's convention is
   `docs/plans/` and that is where this spec lives.

Note the shape they share. None is a missing document. Each is a document that
is confidently wrong, or a document whose instructions stopped matching the
machine. A documentation-debt loop that only asks "what is undocumented" would
have caught none of them.

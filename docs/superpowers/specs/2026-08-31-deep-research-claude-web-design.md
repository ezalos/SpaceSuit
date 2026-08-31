# deep-research-claude-web — design

Date: 2026-08-31
Status: approved for implementation (v1 scope agreed with Louis)

## Purpose

Launch a long, deep research run **outside the current Claude Code session**, so the
research neither consumes the calling session's context window nor blocks the terminal,
and collect a citation-grade report back as portable Markdown on local disk.

The skill is repo-agnostic: it knows nothing about any particular project, writes plain
Markdown plus a small JSON manifest, and leaves project-specific citation gates to the
caller.

## The finding that reframes the original request

The request was phrased as "run deep research on my *web* Claude rather than my Claude
Code, so it bills against the subscription."

That premise does not hold: Claude Code here authenticates via OAuth against a
`claude_max` organization with no `ANTHROPIC_API_KEY` present. Terminal usage already
draws on the same subscription pool as claude.ai. There is no per-token API billing to
escape, and therefore no billing bridge worth building.

What is genuinely worth building is the layer the original request was reaching for:
a **detached** research run with a real brief, a real output contract, and verified
citations.

Separately: there is no public API for the claude.ai "Research" product feature. The
only way to drive that specific orchestrator is browser automation of a logged-in
session, which was considered and rejected (see Rejected alternatives).

## Evidence

All measured on the local machine, 2026-08-31, Claude Code v2.1.251.

| Capability | Local `claude --bg` | Cloud `claude --cloud` |
|---|---|---|
| Non-interactive launch | yes — `claude --bg "<task>"` | no — requires a TTY |
| `--model` / `--effort` as CLI flags | yes, both honoured | `--effort` not supported; must send `/effort` in-session |
| WebSearch | yes | yes |
| WebFetch (read an arbitrary page) | yes | blocked on the default environment |
| Files land on local disk | yes, verified end to end | no — remain in the sandbox |
| Survives caller's SSH session dropping | yes | yes |
| Survives local machine reboot | no | yes |
| Draws on the Max subscription | yes | yes |

Two mechanical details worth recording, both established by probe rather than by docs:

- `--cloud` and `--bg` each refuse to combine with `--print`. `--bg` takes the prompt
  positionally instead. `--cloud` refuses any non-TTY stdout, but given *any* TTY it
  creates the session, prints the session id and a claude.ai URL, and **exits on its
  own** — it is not an interactive TUI that must be driven. An existing cloud session
  then accepts work from a normal non-TTY call: `claude -p "<work>" --cloud <session-id>`.
- `claude logs <id>` emits a raw ANSI screen dump, not a transcript. It is unusable as a
  data channel. Retrieval must go through files the agent writes.

Cloud egress is configurable after all: cloud environments expose a Network access
setting of `None` / `Trusted` (default) / `Custom` / `Full`. The blocked WebFetch above
was the default `Trusted` environment, not a hard platform limit.

## Scope

**v1 ships the local engine only.** It is the engine that works today with zero setup,
full web access, and file retrieval that has been verified rather than assumed.

The cloud engine is deferred, not abandoned — the upgrade path is written down below so
the decision can be revisited with real numbers instead of re-derived from scratch.

## Architecture

Three units with one job each.

### 1. The charter — a written research brief

A Markdown file produced before any launch, so a run is reproducible, reviewable, and
re-runnable without reconstructing intent from memory.

```markdown
# <one-sentence research question>

## Decision this feeds
<what changes depending on the answer; why the run is worth its cost>

## Must answer
- <sub-question 1>
- ... (3–8 of them)

## Source bar
tier: <e.g. primary sources, company IR, peer-reviewed; what is not acceptable>
recency: <window, e.g. 2024-01-01 onwards>

## Deliverable
<shape: length, whether a comparison table / timeline / shortlist is expected>

## Out of scope
- <explicit exclusions, so the agent does not wander>
```

The skill derives the charter from the user's prompt when the prompt is already specific,
and otherwise asks for the missing fields. It always writes the charter to disk and
always shows it before launching — an expensive detached run must not start from a
misunderstanding.

### 2. The launcher — `bin/deep-research`

A Python CLI (invoked via `uv run`, per the stack convention). It owns process mechanics
and nothing else; it contains no research logic.

```
deep-research launch --charter <path> --out <dir> [--model fable] [--effort max] [--engine local]
deep-research status [run-id]      # state of one run, or all
deep-research collect <run-id>     # print report path + summary; warn on unverified sources
deep-research stop <run-id>
deep-research list
```

`launch` assembles the runner prompt, shells out to
`claude --bg --model <m> --effort <e> "<prompt>"` with the working directory set to the
output dir, parses the short session id from stdout, and writes the manifest.

Manifest at `<out>/run.json`:

```json
{
  "run_id": "2026-08-31-143022-<slug>",
  "bg_session_id": "8c969912",
  "engine": "local",
  "model": "fable",
  "effort": "max",
  "charter": "<path>",
  "out_dir": "<path>",
  "started_at": "2026-08-31T14:30:22+02:00",
  "status": "running"
}
```

### 3. The runner prompt — the contract handed to the detached agent

Assembled from a fixed preamble plus the charter. The preamble carries the output
contract and the citation rules; the charter carries the question. The detached agent is
told to:

- research using WebSearch and WebFetch;
- **fetch every page it cites** and quote it verbatim — a source that cannot be fetched
  and quoted is reported as unverified, never silently downgraded;
- never cite a bare domain, a section index, or a redirect — only exact, live URLs;
- write the output files below, and write the `DONE` sentinel **last**;
- on completion, call `~/.claude/skills/notify-louis/notify.sh done "<one-line summary>"`.

## Output contract

Written into the run's output directory:

| File | Contents |
|---|---|
| `report.md` | The report. Inline `[n]` markers on every data claim. |
| `sources.md` | Numbered entries: exact URL (clickable), authority, title, date accessed, and a verbatim quote supporting the claim. |
| `run-result.json` | `status`, source counts, sub-questions left unanswered, and every source that failed verification. |
| `DONE` | Empty sentinel, written last. |

The sentinel exists because polling for `report.md` races against a partially written
file. `DONE` appearing last makes completion a single atomic-enough signal.

`sources.md` deliberately mirrors the shape a stricter project-level registry would want
(exact URL + authority + title + verbatim quote), so a report can be promoted into a
project with a citation gate without re-doing the research. The skill itself enforces no
project gate.

## Defaults and cost control

Defaults are best model and highest effort, as requested: `--model fable --effort max`.

There is **no programmatic way to read remaining subscription rate limit** — `usage` is
not a CLI subcommand, and `/usage` works only inside an interactive session. The spec
does not pretend otherwise. Cost control is therefore three honest mechanisms rather
than one fake one:

1. **Concurrency cap.** At most 2 running research jobs; a third refuses to launch
   without `--force`. Runaway parallel launches are the realistic way to burn a day's
   quota by accident.
2. **A stated cheaper profile.** When the charter has more than 8 must-answer
   sub-questions, or the caller is already at the cap, the skill proposes
   `--model opus --effort high` and says what is being traded away.
3. **A pointed manual check.** The skill tells the caller to run `/usage` in any
   interactive session when quota is a live concern, rather than guessing.

## Failure modes

| Failure | Detection | Response |
|---|---|---|
| Machine reboots mid-run | manifest says `running`, no `DONE`, session id absent from `claude agents` | `status` reports `lost`; offers relaunch from the same charter |
| Agent finishes but sources failed verification | `run-result.json` lists them | `collect` surfaces them loudly and names each one; never reported as a clean run |
| Agent writes a partial report then stops | no `DONE` sentinel | `status` reports `incomplete`; report is shown but flagged |
| Output directory already holds a run | manifest present | refuse; run ids are timestamped so a fresh dir is the default |
| `claude --bg` launch fails | non-zero exit / no session id parsed | CRITICAL log, no manifest written, error surfaced verbatim |

## Security posture

This was an explicit constraint on the request: no credential handling.

- Neither engine reads, writes, stores, or passes any credential. Both reuse the OAuth
  session Claude Code already holds.
- No browser automation, no claude.ai DOM scraping, no scripted login.
- The launcher only ever spawns `claude` as a subprocess and reads files it wrote.
- Nothing in this skill sends anything outbound on the user's behalf beyond the existing
  Telegram completion ping, which is a local script invocation.

## Observability

Follows the universal skill observability baseline, logging via `claude-log`.

| Level | Trigger |
|---|---|
| CRITICAL | launch fails; charter unwritable; `claude` binary missing |
| WARNING | concurrency cap hit; run reported `lost` or `incomplete`; any source failed verification; caller downgraded model/effort |
| WARNING | user correction during charter construction |
| INFO | run launched (engine, model, effort, sub-question count) |
| INFO | run collected (duration, source count, verified/unverified split) |
| INFO | any user feedback, suggestion, or caveat during the run |

## Testing

- **Unit, pure functions:** charter parsing and round-tripping; manifest read/write;
  the `status` state machine across running / done / incomplete / lost.
- **Integration, real subprocess:** launch a deliberately trivial charter through the
  real `claude --bg`, assert `DONE` and `report.md` appear and the manifest closes out.
  This mirrors the probe that already proved the mechanism works.
- No mock-only tests. The one thing worth testing here is that a real detached process
  really produces real files, which a mock cannot show.

## Deferred: the cloud engine

Recorded so the decision is revisitable rather than re-derived. Making cloud viable
requires, in order:

1. At claude.ai/code, set the environment's Network access to `Full` (or `Custom` with a
   research domain list). Without this, WebFetch stays blocked and no report from a cloud
   run can carry a verified citation.
2. Launch through a TTY shim — a detached terminal multiplexer pane running
   `claude --cloud "<description>"`, then scrape the printed session id. The command
   exits by itself; no TUI driving is involved.
3. Send the charter with `claude -p "<charter>" --cloud <session-id>`.
4. Send `/effort max` as an in-session message, since `--effort` is not accepted as a CLI
   flag for cloud sessions.
5. Solve file retrieval, which is the real remaining work: sandbox files do not sync
   home. The documented route is for the agent to commit and push to a git remote, then
   pull locally. `--teleport` brings the conversation back, not arbitrary files.

What cloud buys in exchange: the run survives a local reboot, and it is watchable from a
phone at claude.ai/code.

## Rejected alternatives

**Browser-driving the claude.ai Research feature.** The only route to that specific
orchestrator, and rejected on three counts: it needs a GUI browser session whose
networking is unreliable in the target environment; it breaks whenever the page markup
changes; and it is the one option that would require touching a logged-in session
directly, against the standing constraint. Its capability advantage does not survive the
fragility.

**In-session subagent fan-out.** Already available and unchanged by this work. It is the
right tool when the research should inform the current conversation; it is the wrong tool
here precisely because it spends the calling session's context, which is the cost this
skill exists to avoid.

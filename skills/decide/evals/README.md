<!-- ABOUTME: How to run the decide skill evals, and the recorded results per arm. -->
<!-- ABOUTME: Scenarios are invented; no private case material belongs in this directory. -->

# decide evals

These are four invented scenarios, each run headless:

1. The agent under test gets the opening message.
2. A simulated Louis (Sonnet) answers its first reply. He uses only the scenario's
   `hidden.md` and never volunteers anything.
3. The agent continues.
4. In S3 only, a late constraint arrives after that.
5. A fresh judge scores the transcript against `rubric.md` and the scenario's
   `reference.md`.

| ID | Scenario | Lens | Tests |
|---|---|---|---|
| S1 | `s1-salary` | negotiation | hidden leverage, unknown counterparty interests |
| S2 | `s2-job-queue` | design | operability and idempotency axes, wide option set |
| S3 | `s3-maintainer-roles` | negotiation + design | menu not prescription, late constraint |
| S4 | `s4-lease` | choice, quick | irreversibility flagged in quick mode |

## Run

Run one scenario per arm:

```
skills/decide/evals/run.sh baseline skills/decide/evals/scenarios/s1-salary <out>
skills/decide/evals/run.sh skill    skills/decide/evals/scenarios/s1-salary <out>
```

`<out>` gets the following:
- `transcript.md`
- `score.json`
- `work/`: the agent's cwd
- `records/`: the decision records in the skill arm (`DECIDE_DIR`)

Put `<out>` in a scratch dir, never in the repo. The models are set with `EVAL_MODEL`
(default `claude-opus-5-5`) and `EVAL_SIM_MODEL` (default `claude-sonnet-5-5`).

## Results

Columns:
- R2: often-missed axes named, out of the total.
- R5: reference option families covered, plus novel ones.
- R1, R3, R4, R6, R7, R8, LATE: pass or fail.

| Arm | Scen | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | LATE |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | S1 | fail | 5/9 | fail | pass | 5+0 | fail | fail | fail | n/a |
| baseline | S2 | fail | 4/7 | pass | pass | 2+0 | fail | fail | fail | n/a |
| baseline | S3 | fail | 5/9 | pass | fail | 4+1 | pass | pass | fail | pass |
| baseline | S4 | fail | 4/4 | n/a | fail | 2+0 | pass | fail | fail | n/a |

Baseline pattern (2026-10-18): never frames the decision or names the principal; asks
3–10 questions up front; gives one recommendation with a thin option spread; states
counterparty interests as fact; misses the middle options (a break clause, a 2-year
term, packages offered side by side).

| skill v1 | S1 | pass | 8/9 | pass | pass | 5+0 | pass | pass | fail | n/a |
| skill v1 | S2 | pass | 7/7 | pass | pass | 5+0 | pass | pass | pass | n/a |
| skill v1 | S3 | pass | 7/9 | pass | pass | 5+0 | pass | pass | fail | pass |
| skill v1 | S4 | pass | 4/4 | fail | pass | 3+0 | pass | pass | pass | n/a |
| skill v2 | S1 | pass | 8/9 | pass | pass | 7+1 | pass | pass | pass | n/a |
| skill v2 | S4 | pass | 4/4 | pass | pass | 3+0 | pass | pass | pass | n/a |
| skill v3 | S3 | pass | 8/9 | pass | pass | 6+0 | pass | pass | pass | pass |

v1 → v2: "Your call" items written as statements with a lean, not questions; the quick
template gets a *Their side* line; the stakeholder pass names those not in the room;
negotiation menus carry the accept-as-is and walk-away reference rows; a "his own role"
axis. v2 → v3: the two-question budget covers the whole decision.
| skill v4 | S1 | pass | 7/9 | pass | pass | 7+0 | pass | pass | pass | n/a |
| skill v4 | S2 | pass | 7/7 | pass | pass | 5+1 | pass | pass | pass | n/a |
| skill v4 | S3 | pass | 8/9 | pass | pass | 6+0 | pass | pass | pass | pass |
| skill v4 | S4 | pass | 4/4 | pass | pass | 5+0 | pass | pass | pass | n/a |

v3 → v4 (generic gaps from a development-only replay of real sessions; no case material kept):
drafts compared against the last agreement; every sub-agent and later reviewer told the
principal and the MUSTs; extras and hard exclusions in pass 1; disclosure, dependence and
the order-of-conversations in the checklist; non-negotiating gatekeepers, cost to the
person asked, and one-document-several-audiences in the stakeholder pass; a partition
brief and a substitute-source reframe; retracted terms as revisit triggers.

Known soft spot: "protected time for the work he loves" is still the axis most often left
to the person to raise (S1, S3).

## What the evals do not exercise

The agent under test has no shell (`--tools`, no Bash, no Skill tool, no MCP), so the
Bash steps are untested here: the 0700 records-dir creation, the in-repo check, and the
`claude-log` lines. A live smoke test (2026-10-18, run from inside a repo) covered them: the
record landed in `~/Decisions/` (mode 700), the repo stayed clean, and the log line was
`decide [INFO] : step=calibrate; quick mode chosen`, with no decision content. The skill arm is also
told to read SKILL.md directly, so the description's triggering is not tested either.

2026-10-18: S3 was rewritten in a new setting (open-source maintainers) after review;
the earlier S3 rows used the previous version. Its baseline and skill rows are below.

| Arm | Scen | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | LATE |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | S3 (new) | fail | 8/9 | pass | fail | 5+0 | fail | fail | fail | pass |
| skill v5 | S1 | pass | 8/9 | pass | pass | 7+0 | pass | pass | pass | n/a |
| skill v5 | S2 | pass | 7/7 | pass | pass | 5+2 | pass | pass | pass | n/a |
| skill v5 | S3 (new) | pass | 7/9 | pass | pass | 6+1 | pass | pass | pass | pass |
| skill v5 | S4 | pass | 4/4 | pass | pass | 4+0 | pass | pass | pass | n/a |

v4 → v5 (final review): the mode is named in the first line when Louis did not choose it;
the records dir is resolved and checked against repos in one Bash step; a late constraint
after agents return is applied at merge and pruning; *assumed* tags in every message, not
just the playback. Runner: `--tools` and `--strict-mcp-config` (no Skill tool, no MCP),
answer keys unreadable, a copy of the skill. (S1 and S4 are from the v5 run before the
tagging edit; S2 and S3 were re-run after it.)

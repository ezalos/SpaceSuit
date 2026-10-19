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
| S3 | `s3-cofounder-split` | negotiation + design | menu not prescription, late constraint |
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

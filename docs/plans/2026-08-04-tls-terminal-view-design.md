# tls — terminal session view (design)

Date: 2026-08-04
Status: **implemented and deployed** — see "Post-implementation" at the end. The zsh `tls`
function is NOT yet swapped to the new wrapper; that switch is Louis's to make.
Owner: Louis

## Problem

`tls` is the reflex command for "what is running where". Today it is a zsh function
that prints, per window, the current command, cwd, and idle time:

```
setup@2026-06-03-14h06 (attached)
  11: claude @ /home/ezalos/Setup (7h ago)
```

That answers "is something running here" but not the two questions actually being
asked: **what is this session about**, and **is it waiting on me**. Both facts already
exist — `agents_dashboard` computes the AI-generated title, the blocked state and its
duration, and task progress, all under 153 tests — but they are only reachable through
a web page.

So this is a rendering problem, not a data problem. `tls` becomes a terminal renderer
over the existing data layer.

## Goals

- One dense, aligned grid: every tmux window, with Claude detail where it applies.
- Answers "what is this" (title) and "does it want me" (state + duration) at a glance.
- **At least as fast as today's `tls`.** It is a reflex command; latency is a feature.
- Non-Claude windows are first-class, not omitted.
- Pipeable: no ANSI when stdout is not a TTY.

## Non-goals (v1)

- No interactive/full-screen mode. Considered and rejected below.
- No filtering or sorting flags beyond `--phase`. YAGNI until the grid proves too long.
- No AI-generated status estimation. That is a separate, later piece of work; this
  design deliberately lands first so the AI work has a place to render into.
- No changes to the web dashboard's appearance.

## Key findings (measured 2026-08-04)

1. **The data already exists.** `agents_dashboard.collect()` yields, per Claude pane:
   `title` (from `aiTitle`), `activity`, `waiting_reason`, `waiting_since`, `tasks`,
   `phase` + `phase_is_guess`, `git_branch`, `cwd`, and the `tmux attach` target.

2. **Non-Claude windows are absent from the model.** `collect()` builds `SessionCard`s
   from Claude panes only; a `zsh` window contributes nothing. This is the one real
   data-layer gap.

3. **`collect()` is too slow for a reflex command, and the reason is fixable.**
   Profiled over 30 panes / 20 Claude sessions:

   | Step | Time |
   |---|---|
   | `ps -t` once per pane (30×) | **1.048 s** |
   | phase scan (4 MB, memoised) | **0.434 s** |
   | transcript tails (20×) | 0.043 s |
   | `capture-pane` (17×) | 0.066 s |
   | `tmux list-panes`, session files | 0.005 s |
   | **total** | **~1.75 s** |

   Today's `tls` costs 0.59 s. Both dominant costs are removable: one
   `ps -eo pid,tty,comm` replaces the 30 spawns, and the phase scan is not needed for
   the default view. Expected result is **~0.2 s**, roughly 3× faster than today while
   showing far more. `uv` startup is 0.04 s and not a concern.

4. **Two different idle clocks exist and they disagree.** Measured on the live machine:

   | Pane | tmux quiet | Claude waiting |
   |---|---|---|
   | `setup:11.0` | 7 h | 15 h |
   | `setup:3.0` | 5 m | 7 h |
   | `curriculumvitae:0.0` | 8 h | 15 h |

   tmux's `window_activity` records when the pane last *drew something*; Claude's
   `statusUpdatedAt` records when it last *needed Louis*. A background job printing
   output moves the first and not the second. Both are useful, so both are shown, in
   separate labelled columns. Collapsing them into one "idle" number would silently
   pick a side.

## Layout

```
   WIN   CMD      QUIET  STATE       WAITING  TASKS  TITLE
  alfred (attached)
   0.0   claude     15h  ○ idle         15h  12/12  Build personal agent with calendar, ma…
   1.0   claude      0s  ◉ working        ·    8/9  Create dashboard for Claude session mo…
   2.0   zsh         9m  ·                ·      ·  ~/42/Alfred

  setup
   0.0   zsh         8h  ·                ·      ·  ~/Setup
   3.0   claude      5m  ⚠ unsent         7h      ·  Execute sunshine moonlight handoff plan
   8.0   claude      3s  ⚠ unsent         3m    5/7  Verify server resilience to disk faults
  11.0   claude      7h  ⚠ unsent        15h      ·  Fix GNOME display keyboard responsiven…
```

| Column | Content | Non-Claude row |
|---|---|---|
| `WIN` | `<window>.<pane>`, right-aligned | same |
| `CMD` | `pane_current_command` | same |
| `QUIET` | since tmux last redrew the pane | same |
| `STATE` | `⚠ permission` · `⚠ question` · `⚠ unsent` · `○ idle` · `◉ working` | `·` |
| `WAITING` | since Claude last needed Louis | `·` |
| `TASKS` | `completed/total` when known | `·` |
| `TITLE` | `aiTitle` | cwd, `~`-collapsed |

The `TITLE` column absorbs the remaining terminal width rather than truncating at a
fixed 80; it is the column most worth the space.

**Dropped from the earlier draft:** the model column (not wanted) and the phase column
(mostly repeats itself — on the live machine 11 of 23 panes read `implem` and 13 of 23
are edit-inferred guesses). Phase moves behind `--phase`, which also re-enables the
0.43 s scan. It will earn its place back when the AI estimation lands.

## Ordering

Grouped by tmux session, blank line between groups, windows in numeric order.

The session header carries the short session name and its **attached state** —
`alfred (attached)` / `alfred (detached)`, green and amber as today. Today's `tls`
shows this and an earlier draft of this design silently dropped it; it is cheap and
it is the fastest way to tell which session you are currently sitting in.

Sessions keep today's ordering: by `session_activity` ascending, so the freshest
session sits nearest the prompt. This is existing `tls` behaviour and preserving it
keeps the muscle memory intact.

"Not started" disappears as a concept. A session with no Claude simply shows its `zsh`
windows and their cwds, which says more than a placeholder would.

## Colour

Suppressed entirely when stdout is not a TTY, so `tls | grep` stays clean.

| Element | Treatment |
|---|---|
| session header | bold cyan (as today) |
| `⚠ permission` | red — hard-blocked, cannot proceed |
| `⚠ question` | amber |
| `⚠ unsent` | blue |
| `○ idle` | **muted amber** — waiting on Louis, but calm |
| `◉ working` | green |
| non-Claude row | dim throughout |
| `WAITING` cell | warms with age: normal under 12 h, brighter past 12 h, dim-red past 24 h |

Idle is deliberately not grey. It *is* waiting on Louis — it just is not blocked — so
it should register without competing with the `⚠` states. The age escalation on the
`WAITING` cell is what separates a 5-minute idle from a 15-hour one, which is the
distinction that actually matters when 7 of 23 panes are idle.

## Architecture

No new project. Four changes inside `~/Setup/agents_dashboard/`.

### 1. `tmux.claude_pids_by_tty() -> dict[str, int]`

One `ps -eo pid,tty,comm`, parsed into a tty→pid map, replacing the per-pane `ps -t`.
Matching keeps the existing rule: `comm == "claude"` or ends with `/claude`, never a
lookalike such as `claude-log`. `claude_pid_for_tty` stays for callers that want a
single lookup. Both `tls` and the web dashboard use the map, so the dashboard's
collection drops from ~1.75 s to ~0.7 s as a side effect.

### 2. Non-Claude windows enter the model

New `WindowRecord`: `window_index`, `pane_index`, `command`, `cwd`, `quiet_since`, and
`claude: PaneRecord | None`.

`SessionCard.windows: list[WindowRecord]` becomes the primary collection, and
**`SessionCard.panes` becomes a derived property** — `[w.claude for w in windows if
w.claude]`. Every existing caller and all 153 tests keep working unchanged, the web
renderer needs no edit, and there is no duplicate join logic. The property is the seam
that makes this a widening rather than a rewrite.

### 3. `collect(with_phase: bool = True)`

When false, skip `read_for_phase` entirely and leave `phase` at `Phase.UNKNOWN`. The
web dashboard passes `True`; `tls` passes `False` unless `--phase` is given. Default
stays `True` so no existing caller changes behaviour.

### 4. `termview.py`

`render_terminal(snapshot, width: int, color: bool) -> str`. Pure — no terminal, no
`os.get_terminal_size`, no `isatty` inside. The caller resolves width and colour and
passes them in, exactly as `render.py` takes its snapshot. That is what makes column
alignment and colour suppression testable without a pty.

**CLI:** `python -m agents_dashboard tls [--phase] [--json]`. `--json` emits exactly the
data the grid renders — including the non-Claude windows, which the existing
`/api/state.json` does not carry — so the view stays scriptable without screen-scraping
the aligned columns. The `tls` shell function becomes a one-line wrapper, registered
through the `add-dotfile` skill rather than hand-edited.

## Degradation

Every one of these already holds in the data layer; the renderer must not undo them.

| Failure | Behaviour |
|---|---|
| no tmux server | today's `No tmux sessions`, exit 1 |
| `ps` fails entirely | full tmux window list, Claude columns blank |
| one session file corrupt | that row's Claude columns blank, table intact |
| transcript missing | title falls back to the derived session name |
| terminal width unknown | assume 100 columns |

The table never disappears because one input is bad.

## Testing

Fixture-driven, in `tests/test_agents_dashboard.py` alongside the existing suite:

- column alignment holds when a title contains wide/CJK characters or an emoji
- a non-Claude row shows its cwd, `~`-collapsed, and `·` in all three Claude columns
- no ANSI escapes appear anywhere when `color=False`
- a session whose Claude data is missing still renders its tmux facts
- the `TITLE` column consumes leftover width at 80, 120 and 200 columns
- session ordering is activity-ascending; windows within a session are numeric
- `SessionCard.panes` still returns exactly the Claude panes after the model change —
  this is the regression guard for the widening

Then the reality check that caught the real bugs last time: run it against the live
machine and reconcile row-for-row against `tmux list-panes -a`. A renderer that
satisfies its fixtures while disagreeing with the machine has failed.

Latency is a stated goal, so it gets measured, not assumed: report wall time for `tls`
and for `tls --phase` against the 0.59 s baseline.

## Considered and rejected

- **Full-screen interactive TUI** (k9s-style: live refresh, `j/k`, `/` filter, Enter to
  attach, detail pane). The most capable option and the best-looking, but it replaces a
  fast reflex with an app you enter and quit, cannot be piped, and is a much larger
  build. Worth revisiting as `tls -i` once the grid is in daily use and its limits are
  known.
- **Grouped cards, two lines per pane.** Reads better, scans worse; 23 panes ran past a
  screen, which turns a glance into a scroll.
- **Reading the running dashboard service instead of collecting locally.** Tempting —
  the service is already up with a cached snapshot — but measured at 1.31 s because the
  3 s TTL mostly misses, it makes a core command depend on a service being alive, and
  the snapshot contains no non-Claude windows. Rejected on all three counts.
- **A single merged "idle" column.** Would silently pick one of two clocks that
  demonstrably disagree by hours.
- **Keeping the phase column by default.** Rejected as mostly self-repeating today;
  available behind `--phase`.

## Risks

- **`ps -eo` output shape.** The one-shot parse must tolerate a truncated `comm` and
  ttys reported without the `/dev/` prefix. Covered by the lookalike test that already
  exists for the per-pane path, extended to the map.
- **Width handling with wide characters.** Titles are model-generated and may contain
  emoji or CJK; naive `len()` misaligns columns. The renderer must measure display
  width, not character count.
- **The widening touches a tested model.** `SessionCard.panes` becoming a property is
  the riskiest edit here; the regression guard above exists specifically for it.

---

## Post-implementation — 2026-08-04

Shipped across 14 commits, `e84945f..5f0df12`. 214 tests.

### Measured outcomes

| | before | after |
|---|---|---|
| `tls` wall time (median of 5) | 0.41 s | **0.24 s** |
| `tls --phase` | n/a | 0.63 s |
| `collect()` for the web dashboard | 1.75 s | **0.53 s** |
| `ps` calls per collection | 30 | **1** |

The dashboard's speedup is a side effect: it shares the batched `ps` map.

### Changed from this design during implementation

- The `_pane_urgency` sort sentinel is `float("inf")`, not `0.0`. With `0.0`, a
  session whose `statusUpdatedAt` is corrupt (coerced to a falsy `0`) sorted to
  the *top* of its urgency group rather than the bottom.
- `with_phase=False` blanks phase inputs through `transcripts.strip_phase_inputs`,
  driven by a declared `PHASE_INPUT_FIELDS`. Clearing signals alone was not
  enough: `classify_phase_with_evidence` checks plan mode *before* signals, and
  `mode` comes from the same tail, so plan-mode sessions leaked a real phase.
- The wrapper uses `uv run --directory`, not `--project`. `--project` selects the
  virtualenv without changing directory, and `agents_dashboard` is not installed
  into it, so imports resolved by cwd and the deployed command worked only from
  `~/Setup`.

### Accepted and left

- The grid fits to exactly 80 columns and wraps below. The fixed columns total
  ~74, so truncating titles cannot rescue a narrower terminal; a real fix means
  dropping columns responsively. The narrowest pane on this machine is 80.
- `display_width` treats only East-Asian `W`/`F` as double-width, so ambiguous
  glyphs (`○`, `⚠`, `…`) count as one. Correct under this locale; would misalign
  under an ambiguous-wide CJK locale.
- `list_panes` drops a pane whose `cwd` contains `:`. Pre-existing; the field
  widening enlarged the surface without changing the behaviour.
- `snapshot_to_dict` still serialises only `panes`. That is what keeps the web
  JSON endpoint unchanged; `tls --json` builds its own payload from `windows`.

### Not done, deliberately

The zsh `tls` function was **not** swapped to the new wrapper. That switch is
Louis's to make. Until he makes it, a shell function takes precedence over a
PATH executable, so the old `tls` is what runs.

### The lesson this branch kept teaching

Four defects were **tests that tolerated the discrepancy they existed to
detect**, each passing green: a stub that could not exhibit the bug; a constant
documenting behaviour it did not control (twice); an invariant test with a
compensating offset; and a wrapper verified only from the one directory where it
worked. Every task here ends with a check against live data because unit tests
structurally cannot catch this class.

---
name: loose-ends
description: Use when Louis says "loose ends", "what's outstanding", "clear the backlog", "anything hanging", "tie up loose ends", or invokes /loose-ends. Enumerates every unfinished thread across this conversation, state/todo.md and live drift probes; validates the list with Louis; then RESOLVES as many as possible on the spot rather than deferring them. Only genuinely-Louis work (sudo, vault, physical, outbound comms, judgement calls) is handed back, and only work blocked on a named future event stays a todo.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
---

# Loose Ends

Five phases. The point of this skill is **phase 4** — everything before it exists
to decide what to do, and everything after it exists to prove it was done.

**The governing rule, and the reason this skill exists:**

> **Deferring is the exception. If you can do it, do it.**
> "I'll add a todo" is not a resolution — it is a way of not resolving something
> while feeling productive. A `state/todo.md` line is the right home for work
> that is *blocked on a named future event*, and the wrong home for work that is
> merely unstarted.

`wrap-up` phase 5 CAPTURES loose ends at session end. This skill CLEARS them, on
demand, with a validation gate. They are deliberately separate: wrap-up runs when
Louis is leaving, this runs when he has appetite to actually finish things.

## Observability

This skill follows the universal observability baseline (see
`docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (would have produced wrong result), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during skill execution.** No judgement about "noteworthy" — log every distinct user message that conveys preference, redirection, refinement, or commentary. Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change — already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level    | Trigger                                                        | Message template                                                        |
|----------|----------------------------------------------------------------|-------------------------------------------------------------------------|
| INFO     | Enumeration finished                                           | `loose-ends: enumerated <n> items (<a> do-now, <b> needs-louis, <c> future)` |
| WARNING  | Louis reclassified an item at the phase-3 gate                  | `loose-ends: reclassified '<item>': <from> -> <to>`                     |
| INFO     | Item resolved with proof                                        | `loose-ends: resolved '<item>'; proof=<what was run/checked>`           |
| WARNING  | DO NOW item turned out to need Louis mid-way                    | `loose-ends: '<item>' escalated to needs-louis: <why>`                  |
| WARNING  | Item deferred to a todo                                         | `loose-ends: deferred '<item>'; blocked-on=<named event>`               |
| CRITICAL | An item was found that causes harm if forgotten                 | `loose-ends: CRITICAL: <what>; risk=<what breaks>`                      |
| WARNING  | A resolution failed or could not be verified                    | `loose-ends: FAILED '<item>': <reason>`                                 |
| INFO     | Nothing outstanding                                             | `loose-ends: nothing outstanding`                                       |

```
claude-log loose-ends INFO "loose-ends: started"
claude-log loose-ends WARNING "loose-ends: deferred 'verify Livebox self-provisioning'; blocked-on=next ISP box swap"
```

# triggers I might have missed: a resolution that silently half-applied; an item that reappears every run because its fix never sticks

---

## Phase 1: Gather

Three sources. Collect from all three **before** classifying anything — an item
in two sources is one item, and deduping later is how the same work gets done
twice.

### 1a. This conversation

Re-read the session for the shapes that actually get forgotten:

1. **Commands handed to Louis, never confirmed run.** "I gave him the line" is
   not "it ran". Re-check live state rather than assuming.
2. **Claims never verified.** Anywhere you said "should work", "that will fix
   it", "expect exit 0" and never saw the result.
3. **Deferred offers.** Every "say the word and I'll…", "want me to…?" that he
   never answered.
4. **Findings surfaced but not acted on** — including other workstreams'.
   Out-of-scope is a reason not to FIX something, never a reason not to RECORD it.
5. **Questions asked of Louis he never answered.**
6. **Work explicitly scoped out**, with the reason — he may want it back in.
7. **Tests or tools left failing, skipped, or unrun.**
8. **Files left dangling** — uncommitted work belonging to this session,
   half-renamed paths, scratch files meant to land somewhere.

### 1b. `state/todo.md`

Read `~/42/GroundControl/state/todo.md` (skip silently if absent on this
machine). Every open `- [ ]` line is a candidate. **Most of them are candidates
for DO NOW, not for staying put** — the backlog is where actionable work goes to
be forgotten, and working it down is half this skill's value.

### 1c. Live drift probes

Cheap, read-only, and they surface things nobody wrote down:

```bash
cd ~/42/GroundControl/monitoring && ./tools/service-checks     # failing services
cd ~/42/GroundControl/monitoring && uv run pytest -q           # failing tests
for r in ~/42/GroundControl ~/42/SpaceSuit ~/42/SevenLeagues; do
  git -C "$r" status --porcelain; git -C "$r" status -sb | head -1
done
systemctl --user --failed --no-legend; systemctl --failed --no-legend
```

Run the host-appropriate ones; skip probes for repos that do not exist here.

**Do not adopt other sessions' in-flight work.** Uncommitted files you did not
touch belong to whoever is editing them (see the
`concurrent-agents-in-groundcontrol` memory). Record them as observations, never
as items to fix.

---

## Phase 2: Classify

Every item lands in exactly one bucket.

| Bucket | Test | Default? |
|---|---|---|
| **DO NOW** | You can complete it *and prove it* in this session, with no credential, no sudo, no external side effect | **Yes — assume this** |
| **NEEDS LOUIS** | Genuinely needs his hands or his judgement | only if the test below passes |
| **FUTURE** | Blocked on a **named** external event | only if you can name the event |

**NEEDS LOUIS is for exactly these:**
- root/sudo, or anything on a host you cannot reach
- vault writes, credential creation, provider/console actions
- physical access, or another person's hands
- **anything that leaves the machine** — email, messages, posts, comments.
  Louis is always the one who hits send; prepare and hand over, never transmit
- a judgement call about what he actually wants, where guessing wrong wastes
  more than asking

**FUTURE is for exactly one thing:** work whose blocker is a *named* event —
"at the next ISP box swap", "after the next GPU driver upgrade", "once the
long-running session ends". If you cannot name the event, it is not FUTURE; it
is DO NOW that you do not feel like doing.

**Red flags that you are mis-classifying:**

| Thought | Reality |
|---|---|
| "I'll just note it for later" | That is FUTURE with no named event. Do it. |
| "It's out of scope for this session" | This skill's scope IS the loose ends. |
| "Louis probably wants to decide this" | Only if getting it wrong is expensive. Otherwise decide, and say what you decided. |
| "It's another session's area" | Their *uncommitted files* are theirs. A stale doc or a failing check is not. |
| "It's too small to bother with" | Small and unfinished is exactly what this skill is for. |

---

## Phase 3: Present and validate — HARD GATE

Show the full list **before touching anything**, grouped by bucket, one line
each: what it is, where it came from, and for DO NOW what you intend to do.

Keep it skimmable — a table, not prose. Then stop and wait.

Louis reclassifies or vetoes freely; his call overrides the classification every
time. Log each reclassification. **Execute nothing before his yes**, including
"obvious" items — the gate is the approval, not the size of the change.

If the list is empty, say so plainly and stop. "Nothing outstanding" is a real
answer, but only after actually walking phase 1.

---

## Phase 4: Resolve

Work the approved DO NOW list. For each item:

1. **Do it.**
2. **Prove it** — run the command, read the output, re-run the probe that
   flagged it. Nothing is resolved because it looks resolved. This is Louis's
   standing rule (*"Nothing is done without proof: run it, test it, read the
   logs"*) and it is the difference between this skill and a status report.
3. **Commit it**, staging explicit paths only — never `git add -A`, never files
   you did not touch. GroundControl/SpaceSuit/SevenLeagues are Louis's repos:
   commit to the default branch and push right after.
4. If it turns out to need him, **escalate it visibly** to NEEDS LOUIS with the
   reason. Never let an approved item disappear silently.
5. If a fix fails, log `FAILED` and report it. A failed fix is a loose end, not
   a closed one.

**While resolving:**
- Commit messages with em-dashes/apostrophes/smart quotes: write to a temp file
  and `git commit -F`. Shell heredocs mangle them.
- Never `--no-verify`. A failing hook is a precondition failure: report and stop.
- Append todo lines with Write/Edit, never a shell heredoc — Safety Net scans the
  full command text, so a todo line that names a guarded command blocks the whole
  call.
- Never `rm`; use `rip` (and `unlink` for symlinks to directories).
- On a non-fast-forward push: `git pull --rebase` once and retry. On conflict,
  abort, log CRITICAL, report — do not resolve conflicts here.

---

## Phase 5: Report and reconcile the backlog

Update `state/todo.md` to match reality: check off what you resolved, prune
checked lines from earlier sessions (git history is the archive), and add ONLY
the FUTURE items — each naming the event it waits on. Commit and push it
explicitly.

Then one report:

```
# Loose ends — YYYY-MM-DD

## Needs you (do these first)
<commands to paste, root ones in a normal tmux window, never !-prefixed>
- <what> — <why only you can do it>

## Resolved (<n>)
- <what> — proof: <what was run and what it returned> · <commit>

## Deferred (<n>)
- <what> — blocked on: <named event>

## Failed (<n>)
- <what> — <why, and what it would take>

## Observed, not mine to touch
- <other sessions' in-flight work, named so it is not invisible>
```

Louis-run commands go **first** in the reply, never buried at the end. Never
`!`-prefix a `sudo` line — `!` runs with no tty, so sudo cannot prompt and the
line dies on "a terminal is required to read the password". Say "paste this into
a normal tmux window" for root commands, and keep `!` for the unprivileged
verification lines after them.

If nothing was outstanding, say that and log
`claude-log loose-ends INFO "loose-ends: nothing outstanding"`.

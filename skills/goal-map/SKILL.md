---
name: goal-map
description: Use when Louis invokes /goal-map, or in a long session asks "where are we", "remind me the goal", "what's left", "when will it be done", "what do you need from me", or comes back after hours away and needs to re-enter the work. Also use when a session is about to park on decisions only he can take.
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Agent
---

# Goal map

Louis re-enters a big session cold. One reply must give him the goal, the docs, the
progress with a finish time, and every decision he owes, each decision complete enough
to answer from a phone without asking a follow-up question. The reply is the template
below, filled. Every slot is required; a slot with nothing to say says so in one line.

You are briefing from INSIDE the session he is talking to: his answer comes back here,
to you.

## 1. Gather (read-only)

- **Goal**: from the session itself first (his own words, quoted, and the done
  criterion). If the session has drifted from the goal it started with, say which goal is
  live now and put the earlier one in one line.
- **Docs**: what the session and the repo's CLAUDE.md point to: goal file, design or
  spec, implementation plan, STATUS, deep-research reports, handoffs, launch doc. Open
  each one: a doc older than the work it describes is labelled stale, with its date.
- **Progress**: each part's state comes from an artifact (commit, file, running job,
  receipt), never from a status line alone. A part with nothing behind it reads
  "not verified".
- Large docs: hand them to a subagent with this template and take back the filled
  slots, so the session keeps its context.

## 2. Reply: the template

Plain text, no tables (they wrap on a phone). Paths absolute. Every number carries its
unit. Times in Louis's zone, computed with `date`. Follow his CLAUDE.md for naming other
sessions. Any term of art a reader of the doc would not know gets a half-line
explanation the first time, or is replaced by what it means.

```
GOAL: <one paragraph, the live goal>. Done = <checkable criterion>.
DOCS:
- <kind: design | plan | research | handoff | status>: <absolute path> (<date; "stale" if so>)
PROGRESS: <k> of <n> parts done
 1. <part>: done, took <duration> (<evidence>)
 2. <part>: in flight, <time left> left (basis: <basis>)
 3. <part>: not started, <estimate> (basis: <basis>)
 Waiting on you: <which decisions hold which parts, or "nothing">
 Finish: <work time left> → about <clock time, zone>, if <conditions, e.g. decisions answered by HH:MM>
DECISIONS (<n>)
 1. <short title>
    Why it matters: <how this decision moves the goal>
    What it affects: <the thing it changes, and how that thing works today>
    Ideal: <what a perfect answer would achieve>
    a) <option>: gains <...>; costs <...>
    b) <option>: gains <...>; costs <...>
    Pareto: <"a dominates b because ..." | "c takes a's X and b's Y" | "real trade-off on <axis>: no option wins both">
    Recommend: <letter>, because <reason>.
Reply "1a 2b", or say more on any of them.
```

Slot rules:

- **Parts**: the natural milestones of the goal, numbered. One part only: say so, and
  give one estimate for the whole.
- **Basis** of every estimate, in this order of preference: a live measurement (a running
  job's ETA or throughput in units/h), then how long a finished part of similar size took
  (commit timestamps), then "guess". No number without its basis.
- **Finish** separates work time from time waiting on Louis, and states the condition.
- **Decisions**: only ones that are Louis's to take and that block or shape the goal.
  None pending: write `DECISIONS: none pending.` A go/no-go is a decision with options
  too (a) go now, b) go later or smaller, c) no), each with what it costs.
  Dropping or shrinking part of his goal is never folded into an option silently: it is
  its own option, named as dropping X.
- Order decisions by what they unblock: the one on the critical path first.

## 3. When he answers

1. Restate each answer in one line: `1 → b: <option>`. Ask only about an answer that
   does not map to one option.
2. Record each choice, quoting his words, where the session keeps decisions (plan,
   STATUS, goal file), and commit it if that file is tracked.
3. Carry on with the work the answers unblocked.

## Common mistakes

| Mistake | Fix |
|---|---|
| A decision with options but no "how it works today" or "ideal" | Fill both slots; they are what lets him answer cold |
| A go/no-go or a budget question listed with no options | Give the options and what each costs |
| Estimates without a basis, or one time for everything | One estimate and basis per part |
| Jargon or message ids as references ("N 8 frames", "message 459") | Say what the thing is |
| A ready-to-paste line for another session | He answers HERE, with "1a 2b" |
| Treating a stale STATUS as the progress | Check each part against its artifact |

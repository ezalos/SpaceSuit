---
name: deep-research-claude-web
description: Use when Louis wants a deep research run that should NOT consume this session - e.g. "research X properly", "go deep on Y", "launch a research run on Z", "find out everything about W and report back". Launches a detached Claude Code background session with a written charter and collects a cited Markdown report. Use the in-session research skills instead when the answer must inform the current conversation.
allowed-tools: Read, Write, Bash, AskUserQuestion
---

## Observability

This skill follows the universal observability baseline.

**Universal baseline:**
- CRITICAL on abort.
- WARNING on user correction (Claude was about to be wrong), fallback, retry, precondition-fail.
- **INFO (systematic) on any user feedback, suggestion, or caveat during the run.** Format: `feedback: '<paraphrase>'; phase=<where>; changed <what>` (or `no change - already on track`).
- INFO on edge-case path hit.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| CRITICAL | `deep-research launch` fails | `deep-research: launch failed: <stderr-tail>` |
| CRITICAL | `claude` binary missing | `deep-research: claude CLI not on PATH; cannot launch` |
| WARNING | concurrency cap hit | `deep-research: cap reached (<ids>); offered force or wait` |
| WARNING | run collected as lost or incomplete | `deep-research: run <id> came back <state>; offered relaunch` |
| WARNING | any source came back unverified | `deep-research: run <id> has <n> unverified sources; surfaced to Louis` |
| WARNING | model or effort downgraded from the defaults | `deep-research: downgraded to <model>/<effort>; reason=<why>` |
| INFO | run launched | `deep-research: launched <id>; <model>/<effort>; <n> sub-questions` |
| INFO | run collected clean | `deep-research: collected <id>; <n> sources all verified; <duration>` |

Concrete invocation examples:

```
claude-log deep-research-claude-web INFO "deep-research: launched 2026-08-31-143022-vector-db; fable/max; 5 sub-questions"
claude-log deep-research-claude-web WARNING "deep-research: run 2026-08-31-143022-vector-db has 2 unverified sources; surfaced to Louis"
claude-log deep-research-claude-web CRITICAL "deep-research: launch failed: claude --bg exited 1"
```

# deep-research-claude-web

Run a deep research task in a detached background session, so it costs neither this
session's context window nor the terminal's attention, and come back with a cited report
on disk.

## When to use this instead of researching in-session

Use this when the research is long, the answer is a document rather than a conversational
reply, and the calling session has other work to do. Research in-session instead when the
answer must immediately inform what you are both doing right now.

## Phase 1: Build the charter

An expensive detached run must not start from a misunderstanding. Before launching:

1. Draft a charter with these sections: the question as a level-1 heading, then
   `## Decision this feeds`, `## Must answer` (3-8 bullets), `## Source bar` with
   `tier:` and `recency:` lines, `## Deliverable`, `## Out of scope`.
2. Fill what the request already tells you. Ask only about fields you genuinely cannot
   infer - use `AskUserQuestion`, and ask about the decision it feeds first, since that
   is what makes the sub-questions answerable.
3. Write it to `<out>/charter.md` and **show it to Louis before launching.**

If the request is vague about what it feeds, say so rather than inventing a decision.
A charter with a made-up purpose produces a report answering nobody's question.

## Phase 2: Launch

```bash
deep-research launch --charter <out>/charter.md --out <out>
```

Defaults are `--model fable --effort max`, which is what to use unless there is a reason
not to. Propose `--model opus --effort high` when the charter has more than 8
sub-questions or the cap is already reached, and say plainly what is being traded away.

There is no programmatic way to read remaining subscription quota. When quota is a live
concern, tell Louis to run `/usage` in any interactive session rather than guessing at
it.

At most 2 runs may be in flight. If the cap refuses a launch, report which runs are
holding it and offer either waiting or `--force` - never force silently.

## Phase 3: Collect

```bash
deep-research status            # all runs
deep-research collect <run-id>  # summary, exits non-zero when anything needs attention
```

`collect` exits 1 when sources went unverified, questions went unanswered, or the run did
not finish. **Report every unverified source by name.** Never present a run with
unverified sources as a clean result - silently downgrading a citation is the exact
failure this contract exists to prevent.

If a run comes back `lost` (the machine rebooted mid-run), the charter is still on disk:
offer to relaunch from it.

## What this does not do

- It does not run on claude.ai. v1 runs locally, detached. The cloud engine is specified
  but deferred; see the design doc for the exact upgrade path.
- It does not enforce any project's citation registry. The report is portable Markdown;
  run the project's own citation gate if the material graduates into a deliverable.

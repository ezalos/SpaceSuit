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
| CRITICAL | `deep-research-web launch` fails or preflight refuses on an account flag | `deep-research: preflight refused: <reason>` |
| CRITICAL | the watcher halts on an account flag | `deep-research: halted on account flags: <types>` |
| WARNING | concurrency cap hit | `deep-research: cap reached (<ids>); offered force or wait` |
| WARNING | run landed as a plain chat, went stale, or failed | `deep-research: run <id> <outcome>` |
| WARNING | any citation graded MISQUOTED, DEAD or UNVERIFIABLE | `deep-research: collected <id>; <counts>` |
| WARNING | usage window exhausted | `deep-research: usage window exhausted; resets at <time>` |
| INFO | run launched | `deep-research: launched <id>; <model>; <n> sub-questions` |
| INFO | run collected with only QUOTED and LIVE grades | `deep-research: collected <id>; <counts>` |

Concrete invocation examples:

```
claude-log deep-research-claude-web INFO "deep-research: launched 2026-08-31-143022-vector-db; fable; 5 sub-questions"
claude-log deep-research-claude-web WARNING "deep-research: collected 2026-08-31-143022-vector-db; 2 MISQUOTED, 1 DEAD"
claude-log deep-research-claude-web CRITICAL "deep-research: preflight refused: account flag detected"
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

The default engine is `claude-web`: the run is a Research conversation on claude.ai in
Louis's own account, visible and continuable in the Claude app. It lives in GroundControl
`deep-research-web/` (private) and is on PATH as `deep-research-web`.

```bash
deep-research-web launch --charter <path-to-charter.md>
```

`launch` prints the chat URL and the exact `status` and `collect` follow-ups; put the
URL in the reply. Runs land under the configured runs root (`~/research-runs` by
default) and inside the configured claude.ai Project.

Exit codes: 0 launched; 2 the browser profile is not logged in (run
`deep-research-web login` in a normal tmux window, it prompts for the emailed code);
3 preflight refused (an account flag, an unavailable model, an unknown project, or a run
already in flight: report which, never `--force` silently); 4 the usage window is
exhausted (tell Louis to check `/usage`); 5 the conversation landed as a plain chat and
not a Research run (the URL is printed; look at it with Louis before relaunching).

At most 1 claude-web run in flight. The watcher polls every five minutes and pings
Louis's Telegram once when the run is done, failed, stale (90 minutes without a report)
or halted (an account flag appeared: stop everything, tell Louis what was asked, never
touch the dismiss endpoint).

The local engine of v1 still exists for a run that must write files on this machine:
`deep-research launch --charter <out>/charter.md --out <out>` with the collect flow
documented in GroundControl's design doc history. Use it only when Louis asks for it.

## Phase 3: Collect

```bash
deep-research-web status                 # every run; running / done / failed / stale / plain-chat
deep-research-web collect <run-id>       # report.md, sources.md, run-result.json, grades
```

`collect` writes `report.md` with `[n]` markers at the citation offsets, `sources.md`,
`run-result.json` in the v1 shape, and the raw `conversation.json`. Exit 6 means still
running; exit 1 means something needs attention. If Louis continued the conversation on
his phone, a later `collect` picks up the newest report.

### Citation grades

The Research product cites pages but does not quote them, so the v1 "quote on the page"
check runs only where the report itself quotes. Every citation gets one grade:

- **QUOTED**: the report's cited span contains a double-quoted string of 12+ characters
  and the live page contains it. Same strength as v1's VERIFIED.
- **LIVE**: the page resolves; nothing verbatim to match. The URL is real and specific,
  the claim is unproven.
- **MISQUOTED**: the page is live and the quoted string is not on it. Treat the claim as
  wrong until checked.
- **DEAD**: 4xx/5xx or a bare domain. **UNVERIFIABLE**: timeout, PDF, unreadable page.
- **UNCHECKED**: `--no-verify`; nothing was fetched. Never the basis for calling a report clean.

**Report every non-QUOTED citation by name, with its grade.** Never present LIVE as
verified. `collect` also prints a word-overlap hint of must-answer questions the report
may not have touched; it is a hint, not a verdict.

## What this does not do

- It does not use an official API: none exists for the claude.ai Research feature. It
  drives Louis's own logged-in session from a dedicated browser profile, which the
  Consumer Terms prohibit; Louis accepted that risk on 2026-09-16. Keep the volume human
  and never work around an account flag.
- It does not enforce any project's citation registry. The report is portable Markdown;
  run the project's own citation gate if the material graduates into a deliverable.

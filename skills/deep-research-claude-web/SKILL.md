---
name: deep-research-claude-web
description: Use when Louis wants a deep research run that should NOT consume this session - e.g. "research X properly", "go deep on Y", "launch a research run on Z", "find out everything about W and report back". Runs the charter as a Research conversation on claude.ai in his own account (visible and continuable in the Claude app) through the deep-research-web engine, and collects a cited Markdown report with graded citations. Use the in-session research skills instead when the answer must inform the current conversation.
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
| WARNING | run needs a reply, went stale, or failed | `deep-research: run <id> <outcome>` |
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

Run a deep research task as a Research conversation on claude.ai in Louis's own account,
so it costs neither this session's context window nor the terminal's attention, stays
visible and continuable in the Claude app, and comes back as a cited report on disk with
every citation graded.

## When to use this instead of researching in-session

Use this when the research is long, the answer is a document rather than a conversational
reply, and the calling session has other work to do. Research in-session instead when the
answer must immediately inform what you are both doing right now.

## Phase 1: Build the charter

An expensive research run must not start from a misunderstanding. Before launching:

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
Louis's own account, visible and continuable in the Claude app. It lives in this repo under `deep_research_web/` and is on PATH as `deep-research-web`.

```bash
deep-research-web launch --charter <path-to-charter.md> --name <lowercase-kebab-name>
```

**Always pass `--name`**: it names the run's directory in the library (see Phase 3), so pick
the name a reader would search for (`libero-plus-vs-pro-requirements`, not the question's
first words). Lowercase kebab-case, at most 60 characters. `launch` prints the chat URL and
the exact `status` and `collect` follow-ups; put the URL in the reply. Runs land under the
configured runs root (`~/research-runs` by default) and inside the configured claude.ai Project.

Exit codes: 0 launched; 2 the browser profile is not logged in (run
`deep-research-web login <name>` (the claude-usage account name) in a normal tmux window,
it prompts once for whatever the mail carries: the code, or the sign-in link pasted whole);
3 preflight refused (an account flag, an unavailable model, an unknown project, or a run
already in flight: report which, never `--force` silently); 4 the usage window is exhausted:
no saved account had room, the account switched to was refused too, or claude-usage could
not run (tell Louis to check `/usage`); 5 no research started and the assistant is
waiting on an answer (the URL is printed; look at it with Louis before relaunching).

Each account has its own saved profile, named as in claude-usage. `deep-research-web login <name>`
logs one in. `deep-research-web switch [<name>]` makes one live. With no name, `switch` picks
the saved account with the most weekly room by claude-usage's meters. `deep-research-web profiles`
lists them, as a table. A launch refused on usage tries each saved account with room, most room
first. It never asks a refused account twice, and it names the earliest window that reopens when
none is left. An account flag never switches.
Judge which account a profile holds only from `/api/account`, which `login` prints. This engine's
live account is separate from Claude Code's; `claude-usage switch` only moves Claude Code's.

At most 1 claude-web run in flight. The watcher polls every five minutes and pings
Louis's Telegram once when the run is done, failed, needs a reply, stale (90 minutes
without a report) or halted (an account flag appeared: stop everything, tell Louis what
was asked, never touch the dismiss endpoint).

The local engine of v1 still exists for a run that must write files on this machine:
`deep-research launch --charter <out>/charter.md --out <out>` with the collect flow
documented in `docs/superpowers/specs/2026-08-31-deep-research-claude-web-design.md`. Use it only when Louis asks for it.

## Phase 3: Collect

```bash
deep-research-web status                 # every run; running / done / needs-reply / failed / stale
deep-research-web collect <run-id>       # report.md, sources.md, run-result.json, grades
deep-research-web report <run-id>        # the collected report.md on stdout, or --out PATH
```

`deep-research-web stop <run-id>` stops a run that is still in flight; `deep-research-web list`
shows every known run with its state and link.

Any command takes a unique run-id PREFIX; an ambiguous one is refused with its candidates
rather than guessed. `report` reads only the disk, so it costs nothing and needs no browser:
`report <id> > file.md` is the whole report, byte for byte.

`needs-reply` means the assistant spoke and never launched research. Nothing in the
conversation JSON distinguishes a clarifying question from a refusal or a plain answer, so
this one state covers all three: open the chat, read it, answer or relaunch. `status`
persists it (such a run would otherwise age into `stale`), but never persists `done` —
the watcher owns that, and writing it early would steal the auto-collect and the ping.

Exit 2 from any command means the browser profile is logged out or busy; run
`deep-research-web login <name>` (the claude-usage account name) in a normal tmux window,
or wait for the other command.

`collect` writes `report.md` with `[n]` markers at the citation offsets, `sources.md`,
`run-result.json` in the v1 shape, and the raw `conversation.json`. Exit 6 means still
running; exit 1 means something needs attention. If Louis continued the conversation on
his phone, a later `collect` picks up the newest report.

### The library

When the host configures `DEEP_RESEARCH_WEB_ARCHIVE_ROOT`, `collect` (yours or the
watcher's) also copies the finished run into `<root>/<start date>-<name>/` and prints the
path: charter, report (frozen), graded sources, `archive.json`, a `README.md` written once.
The engine never commits. **After a collect, commit that directory and the regenerated
`<root>/README.md` in the repo that hosts the root, and push**, as that repo's own rules say.
Refer to a report by that path from then on, not by the run id. `deep-research-web archive
<run-id> [--name X]` copies or renames a run by hand (a watcher-collected run of another
session, a name typo).

### The claims check, opt-in

`deep-research-web check-claims <run-id>` asks an independent headless reader to check the
report's load-bearing claims against primary pages and writes `verification.json` /
`verification.md` into the run and its library copy. It is never automatic: it spends usage
and takes minutes. Run it when the report feeds a decision, or when Louis asks; report its
REFUTED and PARTIALLY lines by name, and its `most_consequential` paragraph verbatim.

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

The quote is matched with markdown backticks removed and edge punctuation trimmed, so a
report that pulls its own sentence period inside the closing quote mark is not called a
misquote; the 12-character evidence floor applies to what survives trimming, not to the
padded original. A difference in the WORDS still grades MISQUOTED.

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

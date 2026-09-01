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

No `--runs-root` is needed: launch derives it from `<out>`'s parent directory whenever
`--runs-root` is not given, so the run is always discoverable by that same derived root.
`launch` prints the exact `status` and `collect` follow-up commands, each with the
`--runs-root` it derived - use those printed commands verbatim rather than guessing the
root yourself.

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
deep-research status --runs-root <runs-root>            # all runs
deep-research collect <run-id> --runs-root <runs-root>  # summary, non-zero on trouble
```

Use the `--runs-root` that `launch` printed for this run. Without it, both commands fall
back to the default `~/research-runs` and will report "no runs found" for a run launched
under a different `--out`.

`collect` exits 1 when sources went unverified, questions went unanswered, or the run did
not finish. **Report every unverified source by name.** Never present a run with
unverified sources as a clean result - silently downgrading a citation is the exact
failure this contract exists to prevent.

### Independent verification (on by default)

`collect` refetches every source the run cited and greps the live page for the verbatim
quote. This is the only part that does not take the research agent's word for anything:
everything else in the summary is the agent grading its own homework.

Each source comes back as one of three things, and the last two both exit 1:

- **confirmed** - the quote is on the page.
- **CONTRADICTED** - the page loaded but does not contain the quote. Treat this as the
  citation being wrong until proven otherwise. Do not pass the claim on.
- **UNVERIFIABLE** - nobody could check it: a 4xx/5xx, a timeout, a bare domain (which
  the citation contract forbids anyway), a non-HTML document such as a PDF, a page past
  the read limit, or a quote too short to be evidence (under 12 characters - "the"
  appears on every page, so confirming it would prove nothing). Bot-blocked publishers
  and PDFs land here routinely, so this is a "you decide", not a fabrication.

`collect` also fails when the run lists fewer sources than it claims to have cited:
under-listing would otherwise be a free pass, since only listed sources get checked.

Matching normalises both sides first - tags stripped, entities decoded once, curly quotes
and dashes folded to ASCII, soft hyphens and zero-width characters removed, block
boundaries treated as spaces, whitespace collapsed - so a real quote is not failed over
typography. Script, style and `<sup>` contents are excluded, so a quote can neither be
"found" in markup the reader never sees nor broken by a footnote marker.

**What VERIFIED does and does not mean.** It means the quoted string really appears in
that page's readable text. It does NOT mean the string came from the article body: page
text is unscoped, so navigation, cookie banners and sidebars are quotable too, and two
adjacent blocks can read as one line. So VERIFIED rules out a fabricated quote; it does
not by itself prove the quote supports the claim. That judgement stays yours.

`--no-verify` skips all fetching and prints the self-reported counts with a warning. Use
it for a quick offline look, never as the basis for calling a report clean.

If a run comes back `lost` (the machine rebooted mid-run), the charter is still on disk:
offer to relaunch from it.

## What this does not do

- It does not run on claude.ai. v1 runs locally, detached. The cloud engine is specified
  but deferred; see the design doc for the exact upgrade path.
- It does not enforce any project's citation registry. The report is portable Markdown;
  run the project's own citation gate if the material graduates into a deliverable.

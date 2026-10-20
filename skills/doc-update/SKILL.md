---
name: doc-update
description: Use when Louis corrects how an agent acted and wants the lesson to hold for every future session ("this should be documented", "make sure this never happens again", /doc-update), or when a correction restates a rule that should already have been followed.
---

# doc-update

Turn one correction into a rule the next agent reaches and follows. Two things must hold at
the end:
- **content**: the rule is written once, without a second reading, in the doc that owns it;
- **path**: an agent starting from the repo's CLAUDE.md reaches that doc at the moment it needs it.

**REQUIRED SUB-SKILL:** writing-for-agents, loaded before step 4. It governs every line this
skill writes: the target behaviour stated positively, leading words, one source of truth.

## 1. The lesson

Write the rule in one sentence: the situation, then the behaviour, then why. Add Louis's words,
quoted and dated, as its provenance.
Done when the sentence names a behaviour you could check against a transcript.

## 2. The trace

Find the session that erred: the UUID Louis gives, `claude-session <prefix>`, or this session.
Its transcript is `~/.claude/projects/<its cwd, each / replaced by ->/<uuid>.jsonl`. Transcripts are
large: a subagent reads it and reports:
- **what it had**: the CLAUDE.md files injected at start, the skills it invoked, the docs it read
  or grepped, in order, with times;
- **the decision point**: the agent's own reasoning when it erred, quoted, and the rule or
  wording it leaned on;
- **every existing rule on the topic**, verbatim with path:line, from what it loaded and from the
  repo's docs;
- **trigger words**: the words the agent was thinking in at that moment ("cap", "margin").

Then name the miss, one class or several:
- **absent**: no doc says it.
- **ambiguous**: a doc says it, and its wording allowed the agent's reading.
- **unreachable**: a doc says it clearly, and nothing the agent loaded leads there.
- **overruled**: the agent reached the rule, and another rule pulled the other way.

Done when every class you name carries a quote from the transcript or a doc.

## 3. Red

Before any edit, a fresh subagent gets the situation at the decision point, written as the
agent saw it, with no hint of the answer. It reads a frozen copy of the docs (`git archive` of
the commit before any fix, the lane's own included, into a scratch folder), starts from the
CLAUDE.md files, follows whatever pointers it chooses, and reports what it would do, the lines
that decided it, and any wording that pulls the other way or reads two ways.
Done when it goes **red**: it repeats the mistake. A green run here means the trace missed the
cause: redo step 2.

## 4. The content

Edit the doc that owns the topic: the most specific doc every session facing this situation
is sent to. Rewrite the existing rule in place, and merge or delete the wording that allowed
the misreading, so one meaning lives in one place. Agent memory is a copy at most, never the
owner.

Ask Louis before editing when the fix is a judgement he has not made:
- an **overruled** miss: show both rules and which one the new wording lets win;
- a new wording that still admits two readings: show both readings.
Edit after his answer. An absent, ambiguous or unreachable fix that only writes down his own
words goes ahead.

Done when the doc states the rule once and a grep of the trigger words across the repo's docs
finds nothing that contradicts it.

## 5. The path

The chain is CLAUDE.md → a category doc (the index for money, launches, evals, ...) → the
owning doc; a skill the agent was running at the decision point is a link too. CLAUDE.md
carries category pointers; it gets a new line only when no category covers the topic. Each
pointer is worded with the trigger words from step 2, front-loaded, one trigger per branch.
Done when every link exists and names a trigger word.

## 6. Green

A new fresh subagent runs step 3's test on a copy of the edited docs. A second one runs the
**control**: the same situation moved just past the rule's boundary, where the old behaviour
is the right one (a cost over the ceiling, a case the rule excludes).
Done when the test goes **green** (it reaches the owning doc through the chain and acts by the
rule), the control keeps the old behaviour, and the wording each flags as two-way is resolved.
Each red run sends you back to step 4 or 5 with what it read; after 3 rounds, what still fails
goes to Louis.

## 7. Land and report

Commit under each repo's own rules. Report to Louis:
- the rule, and the owning doc by absolute path;
- the pointer chain, link by link;
- the miss class, with its quote;
- the red and green runs: what each did and read.
If the session that erred must act on the rule now, add a ready-to-paste message for it.

---
name: decide
description: Use when Louis invokes /decide, says "help me decide", "what are my options", "what are the trade-offs", "should I X or Y", or faces an important or hard-to-reverse choice - a negotiation (salary, a deal, roles between people), a strategic or career move, or an engineering design choice.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, WebSearch, WebFetch
---
<!-- ABOUTME: Objectives-first decision skill: frame, ideal per axis, catastrophes, then a -->
<!-- ABOUTME: background option search pruned to the Pareto set with explicit trade-offs. -->

# Decide

Values first, options second. Agree with Louis on what is being decided, the ideal outcome on
**every** axis and what must never happen. Then search options wide in the background, keep
only the Pareto-efficient ones, and show what each one gives up. No numeric weights, ever.

Two failures matter most: an axis Louis has to raise himself afterwards, and options that are
variations of one idea.

## Step 0: Calibrate (silent, seconds)

- **Mode.** *Full* for an important or hard-to-reverse decision; *quick* for a small or
  reversible one, or when Louis says "quickly". Quick mode on a hard-to-reverse decision is
  allowed, but say once, in one line, what cannot be undone.
- **Lens.** *Choice* (pick among options), *negotiation* (someone else must agree), *design*
  (building something; options are combinations of sub-choices). They combine.
- **Freshness.** Read what the context already holds (files, earlier messages, documents)
  before asking anything. Which version of each fact is current? What changed? When there
  are drafts of an agreement, compare the newest draft with the last thing actually agreed:
  an agreed term that silently disappeared is a finding, not a detail.
- **Record.** Create `${DECIDE_DIR:-~/Decisions}/YYYY-MM-DD-<slug>.md` from
  `references/record-template.md` (make the dir with mode 0700 if missing). Never inside any
  repo, whatever the cwd. Say the path in chat once.

## Step 1: The playback (one message, the only required round trip)

Load `references/axes.md` and run its three passes (Louis's words, the why-ladder, the
checklist, plus the stakeholder pass) before writing. Then send exactly this shape, short
enough for a phone:

```
**Deciding:** <the decision, its scope, its deadline> · **not deciding:** <what is out>
**For:** <principal: whose interests this defends> · **Others with a say:** <who reads, signs, judges or agrees>

**Ideal outcome**: if anything were possible:
- **<axis>**: <the magic outcome on this axis>
- … (every axis from the three passes, the often-forgotten ones included)

**Their side** (assumed unless stated):
- <party>: <likely ideal> (*assumed, low|medium|high*)

**Never:** <MUST 1> · <MUST 2> …
**How it could go badly:** <pre-mortem item> · <pre-mortem item>

**I'm assuming:** <fact> · <fact> (correct any)
**Two questions that would change the answer:** <q1> · <q2>   (omit if none)

What's wrong or missing?
```

Rules of this message:

- State facts you would otherwise ask about as **assumptions** Louis can correct. A question is
  allowed only if its answer could change which options win (see `references/pruning.md`,
  value of information). At most two.
- A guessed interest of another party is always tagged *assumed* with a confidence level.
- Then stop and wait. A second round only if his answer changes the frame itself.
- The two-question budget covers the **whole decision**, not each message. After the
  playback, every remaining unknown becomes an assumption or a **Your call** statement.

Quick mode compresses this into the quick output below and does not wait.

## Step 2: Options (background, full mode)

Write the confirmed frame, axes, MUSTs and assumptions into the record. Then dispatch the
option sub-agents **in parallel**, one per strategy brief in `references/option-strategies.md`
that fits the lens (at least four). Each gets the record path, reads the record itself, and
returns options only. Tell each whose interests it serves (the principal from the record) and
that web searches use the general fact, never names or details from the record.

Merge, deduplicate, and run the diversity check from that file. If the survivors cluster
around one idea, dispatch the missing strategies once more.

**A constraint Louis adds while agents run:** append it to the record's constraint log with a
timestamp, send it to the running agents (SendMessage), and apply it at the merge. Never
restart the search for it.

## Step 3: Consequences and pruning (background)

Follow `references/pruning.md`: consequences as ranges, drop what breaks a MUST, drop what is
dominated, explain the survivors against each other with Even Swaps, mark options that survive
only under one assumption about another party, and (design lens) name sensitivity points.

## Step 4: Output

Load `references/lenses.md` and fill the template for the lens:

- **choice**: recommendation, then the Pareto table
- **negotiation**: a menu
- **design**: a decision record

When another party has a say, negotiation wins: a menu, never "the answer". You may say which
option you would open with, and why.

Every output ends with these, in this order:

- **Your call:** the items only Louis can decide, each written as a statement with your lean
  ("cash vs equity: I'd lean equity because …"), never as a question he must answer.
- **Assumptions:** each with a confidence level.
- **Ready to paste:** text he can send or say, kept apart from the reasoning (if any).

Update the record (options, Pareto set, status). When Louis decides, fill in `## Outcome`.

## Quick mode (inline, one screen, no sub-agents)

```
**Deciding:** … · **For:** … · **Lens:** …
**Axes:** <axis> · <axis> · … (checklist pass included)
**Their side:** <party>: <interest> (*assumed, confidence*)   ← whenever another party exists
**Never:** <MUST> · **Can't undo:** <what is irreversible, if anything>
| Option | Wins on | Gives up |   ← at least 3 genuinely different options, a middle one included
**Lean:** <option, one line why>   (a menu instead if another party has a say)
**One question:** <only if it could flip the lean>
```

The same quick check fits micro-decisions (a wording, a claim): true? can this person say or
sign it? consistent with everything else? which axis loses?

## After the skill: MUSTs stay in force

Any sub-agent or reviewer spawned later for this decision (a drafter, a red-team, a
"make it stronger" pass) is told the principal and the `## Never` list in its prompt. A
reviewer optimising for an audience, at the principal's expense, is the failure this prevents.

For the rest of the session, re-read the record's `## Never` section:

- before any outbound text (a draft, a message, a letter);
- before a commit;
- before an irreversible step;
- after a compaction (re-read the whole record then).

## Common mistakes

| Mistake | Fix |
|---|---|
| Jumping to options from a one-line ask | The playback comes first, even for "help me decide X" |
| A list of questions | Assumptions he can correct, plus at most two questions that would flip the answer |
| One recommendation in a negotiation | A menu: what each party gets, when it breaks |
| Their interests stated as fact | *assumed, confidence*; the important ones become questions he can ask them |
| Three versions of one idea | Strategy briefs, then the diversity check |
| Scores times weights | Dominance, then Even Swaps |
| Restarting for a late constraint | Constraint log, notify the agents, apply at merge |

## Observability

This skill follows the universal observability baseline (see
`docs/plans/2026-04-21-skill-storage-observability-design.md`).

**Universal baseline:** CRITICAL on abort, WARNING on correction/fallback/retry/precondition-fail, INFO on edge path and on every piece of Louis's feedback.

Messages name the step and a generic event only: never an axis, an option, a party or
any detail of the decision.

**Skill-specific triggers:**

| Level | Trigger | Message template |
|---|---|---|
| INFO | Louis adds an axis after the playback | `feedback; step=playback; axis added by Louis` |
| WARNING | A MUST is breached or nearly breached | `step=<step>; MUST nearly breached; caught at checkpoint` |
| INFO | Diversity check ran a second round | `step=options; diversity recheck ran a second round` |
| INFO | Quick mode on a hard-to-reverse decision | `step=calibrate; quick mode chosen on irreversible decision` |

Log via: `claude-log decide <LEVEL> "<message>"`

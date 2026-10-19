# `decide` skill: design

Status: draft, awaiting Louis's approval. Nothing is built yet.

## Intent

When Louis faces a decision that matters (social, strategic or engineering), the agent
helps him decide well instead of handing him its first plausible answer. In his words, two
things must not fail:

1. **Every axis of the ideal outcome gets named.** Nothing that would make the outcome
   better gets forgotten.
2. **The option search is wide and diverse.** No good idea is missed because the search
   stopped early.

The rest is in support of those two: agree on what is being decided, keep catastrophes
out, show the Pareto-efficient options with their trade-offs explicit, and ask for
information only when it could change the answer.

## Why: evidence from two real sessions (abstracted, the content is private)

Two long sessions were read for decision moments. The same failures came up in both:

- **Wrong principal.** A reviewing sub-agent optimised for an audience (the person who
  would judge the output) and quietly removed guarantees Louis had negotiated. Nobody had
  written down whose interests the work defends.
- **Wrong frame.** "Pick the best split" when the real task was "bring several splits and
  build one together with the other party". The agent prescribed one answer and Louis had
  to correct it.
- **Other parties' interests missing.** The decisive input (what the other side wants)
  was never asked for.
- **Axes surfacing late,** one message at a time, while background searches were already
  running: relationship strength, leverage timing (bargaining power drops once you depend
  on the other side), keeping options open, reuse of the work later, truthful versus
  persuasive, legal or status risk, emotional load.
- **Single answers.** Several decisions got one option and no alternatives.
- **Hard limits stated once, never re-checked.** A stated "never do X" was later breached.
- **Stale facts.** Options built on an old version of the deal, or a step already taken.
- What worked: turning "never get locked in" into dated deadlines and must-have terms.

Both sessions also held a few big decisions and many small ones (wording, which signer,
which route), so one heavy flow for everything would be wrong.

## Goals

- Agree with Louis, interactively, on the frame, the principal, the stakeholders, the full
  set of ideal-outcome axes and the catastrophes.
- Do the heavy work (option generation, consequences, Pareto pruning) in the background
  and show him the result, not the process.
- Make every trade-off between surviving options explicit: what you gain, what you give up.
- Keep a decision record that survives compaction and is the shared state for sub-agents.
- Work for social situations (negotiation, career, relationships, business strategy) and
  engineering design.

## Non-goals

- Weighted-sum scoring or numeric weights. They give false precision and hide the
  trade-offs Louis wants to see. Use dominance and Even Swaps.
- A general library of mental models.
- Deciding for Louis when another party has a say. The skill never outputs a single
  prescription in a negotiation.
- Advice on his inner life. The `reflect` skill owns that.

## Anti-goals (a failure even if the flow runs)

- A missed axis that Louis brings up afterwards.
- Options that are variations of one idea.
- An interrogation: questions whose answers would not change the Pareto set.
- A sub-agent serving someone other than the principal.
- Assumed interests of other parties presented as facts.
- Walls of text. Louis reads on his phone.

## Backbone

The flow follows PrOACT (Hammond, Keeney and Raiffa, *Smart Choices*) in Keeney's
value-focused order: objectives before options. It borrows:

- Kepner-Tregoe MUSTs and Klein's pre-mortem for catastrophes;
- TRIZ's Ideal Final Result for the "magic" framing;
- strategy tables for combining sub-choices;
- dominance plus Even Swaps for the trade-offs;
- value of information to gate questions.

Two lenses add to it:

- **Negotiation:** interests over positions, BATNA, a joint Pareto frontier, MESOs.
- **Design:** set-based design, ATAM tradeoff and sensitivity points, an ADR-shaped
  output.

## The flow

### 0. Calibrate (seconds)

Decide two things:

- **Mode.**
  - **Full** for an important or hard-to-reverse decision.
  - **Quick** for a small or reversible one: no sub-agents, about one screen, run inline.

  Louis can say which. Otherwise the agent proposes one in a single line.
- **Lens.** Choice, negotiation or design. Lenses can combine. For example, a negotiation
  whose content is a structure is negotiation plus design.

Then check freshness. What is already in the context (files, earlier messages, docs)?
What has changed since the last time? Which version of each fact is current? Search the
context before asking Louis anything.

### 1. Frame (interactive)

Play back to Louis, and get his confirmation on:

- **What is being decided, and what is not:** the decision, its scope, and the deadline.
- **The principal.** Whose interests the result defends. Usually Louis, but it is stated,
  never assumed.
- **Stakeholders.** Everyone who will read, sign, judge or agree to the result.

### 2. Ideal outcome on every axis (interactive; the most important step)

For each axis, the agent describes the magic outcome: what the best case would look
like, with feasibility ignored. Coverage comes from three passes, merged before
Louis sees them:

1. **What Louis named.**
2. **A "why does that matter?" ladder** on each of his axes, to reach the end behind the
   means. For example, salary may stand for security, freedom or status.
3. **A checklist of often-forgotten axes:**
   - money;
   - time and effort;
   - reversibility and option value;
   - relationships with each party;
   - reputation;
   - learning;
   - legal, status or compliance risk;
   - emotional load;
   - reuse of the work later;
   - truthful versus persuasive;
   - leverage and timing.

   The engineering lens adds quality attributes: reliability, performance, security,
   cost to run, operability, simplicity and time to ship.

Then a **stakeholder pass**: for each stakeholder, their likely ideal, with every guessed
item marked as an assumption with a confidence level.

Louis confirms or edits the merged axis list. The agent asks once, explicitly: "what's
missing?" This is the step where Louis must be in the loop.

### 3. Catastrophes (interactive, short)

- **MUSTs:** hard constraints. Any option that breaks one is out, whatever its scores.
- **A pre-mortem:** "it's later and this went badly: why?" It surfaces catastrophes Louis
  did not list.

MUSTs go in the record. They stay in force for the rest of the session, not just until
the skill ends.

**Enforcement (my call):** no hook in v1. The skill tells the agent to re-read the record's
MUSTs at checkpoints: before any outbound text (a draft, a message, a letter), before a
commit, and before any irreversible step, and again after a compaction. The record path is
stated in the conversation so it can be found again. Sending as Louis is already guarded
by his global rules. A hook gets added only if a MUST is breached despite the checkpoints.

### 4. Options (background)

In full mode, several sub-agents run in parallel. Each is given the record and a
deliberately different generation strategy:

- push one axis to its extreme (one agent per two or three axes);
- combine sub-choices with a strategy table;
- wait, do nothing, or buy time;
- reframe the problem (change who, when or what is being decided);
- borrow from analogous fields or from what an expert in X would do;
- in the negotiation lens, a package designed around the other side's likely interests.

The orchestrator merges the results and deduplicates them. Diversity is checked: if the
survivors cluster around one idea, it runs another round with the missing strategies.

### 5. Consequences, then prune (background)

For each option and each axis, give a consequence as a range (pessimistic, likely,
optimistic), with sources where facts are needed. Then:

1. drop options that break a MUST;
2. drop dominated options;
3. explain each remaining option against its neighbours with Even Swaps;
4. note any option that only survives under one assumption about another party.

For the design lens, also name the sensitivity points (where a small change flips the
choice).

### 6. Questions that could change the answer (gated)

An unknown becomes a question only if its answer could change which options are
Pareto-efficient:

- the agent searches the context first;
- if the answer is not there, it asks Louis;
- for another party's interests, it turns the question into one Louis can ask them, often
  as an opening move.

Everything else is listed as an assumption.

### 7. Output

Decision first, short, phone-readable, with bold for key words. Its shape depends on the
lens.

- **Choice:**
  - one line of recommendation;
  - a table of 2–4 Pareto options against the axes, with each option's losing axis in
    bold;
  - the dated first moves.
- **Negotiation:**
  - a menu of options, each showing what each party gets and when it breaks;
  - the invariants every option keeps;
  - the opening question to ask the other side.

  No single prescription.
- **Design:**
  - a record (ADR-shaped) of the chosen option, the rejected ones and why;
  - the tradeoff and sensitivity points;
  - what would make the team revisit it.
- **Lenses combined (my call):** negotiation wins whenever another party has a say. The
  output is then a menu, and each item can be ADR-shaped in the design lens. The agent
  may say which option it would open with, and why, but it never names one as "the
  answer".
- **Always:**
  - "your call" items, listed explicitly;
  - the assumptions, each with a confidence level;
  - ready-to-paste text kept apart from the reasoning.

### Quick mode

Quick mode runs inline in one pass. It names:

- the lens;
- the axes (checklist pass included, shown in one line);
- the MUSTs;
- at least three genuinely different options;
- which axis each option loses on;
- one question, if the value-of-information rule says it is worth asking.

It still names the principal. Quick mode also serves the frequent micro-decisions
(wording, for example):

- Is it true?
- Can this person sign or say it?
- Is it consistent with everything else?
- Which axis loses?

## The decision record

One markdown file per decision is the single source of truth. It holds:

- the frame and the principal;
- the stakeholders and their interests (each marked stated or assumed);
- the axes with their ideal outcomes;
- the MUSTs;
- a constraint log with a timestamp per entry;
- the options and the Pareto set;
- the open questions;
- the outcome, filled in once Louis decides.

Sub-agents receive its path and read it. They do not get a summary. When Louis adds a
constraint mid-search, it goes into the log and the running agents are told; the search
is not restarted. The record survives compaction, so after a compaction the agent
re-reads it.

**Location (Louis's call):** `~/Decisions/YYYY-MM-DD-<slug>.md` on the machine where the
session runs. Records never go in any repo, private or shared: a work repo is readable by
the very people a decision may be about. `/home` is in the restic backup, so the folder is
backed up without extra work. Louis may want a Notion sync later. For now that only means
keeping the record plain markdown with a stable front matter, so it can sync later
without a format change.

## Channel and pacing (my call)

The skill is written for an interactive terminal session. It needs only plain text, so it
also works over Telegram (no AskUserQuestion is required).

Full mode folds steps 1–3 into **one** phone-sized playback:

- the frame;
- the principal and the stakeholders;
- the merged axes with their ideals;
- the MUSTs and the pre-mortem.

It ends with "what's wrong or missing?". A second round happens only if Louis's answer
changes the frame. That keeps Louis in the loop without turning it into an interrogation.

Sub-agents doing web searches never put names or private details from the record into
their queries. They search for the general fact.

## Placement and constraints

- The skill lives at `SpaceSuit/skills/decide/` (public). It is registered and deployed
  as a global skill via the `add-dotfile` skill. The skill text must contain nothing from
  Louis's private cases. Its examples are invented and generic.
- `SKILL.md` stays lean. The lens details (negotiation, design) and the axis checklist go
  in reference files the skill loads only when needed.
- Triggers: `/decide`, or "help me decide", "what are my options", "trade-offs", "should I
  X or Y".
- **Self-offer (Louis's call: yes).** A skill description fires only on what Louis types.
  So one line goes in the global CLAUDE.md (in SpaceSuit's generic base): when about to
  recommend a single option for an important or hard-to-reverse decision, offer
  `/decide` in one line and carry on. Louis chooses whether to use it. The agent never
  runs it unasked.
- Interplay:
  - `goal-map` lists the decisions Louis owes; a decision record can feed it;
  - `dialectic` and `grilling` are optional stress tests on the Pareto set;
  - `dsearch` (a deep research run) can be used when an axis needs heavy outside facts.

## Testing with private material

There are two eval sets.

**Long-lived (in SpaceSuit):** invented scenarios only, for example a salary talk, a
service architecture choice and a role split among project maintainers. Each has a reference axis list and a
reference option spread written for it. They draw on the abstract patterns in the "Why"
section above, never on case details.

**Development-only (private):** the two real sessions, used while the skill is being built
and then dropped. Louis asked for a strong guarantee. It comes from not creating the data
in the first place, rather than from cleaning it up afterwards:

- **No case file is ever written.** Each eval agent reads the original transcript at run
  time, from where it already sits (`~/.claude/projects/…`, mode 0600).
- **Results are agent reports only.** They contain scores and abstracted gap names (for
  example "missed: leverage timing"), never quotes, names or figures. Nothing is written
  to the scratchpad, `/tmp`, a repo or `~/Decisions/`.
- **No egress.** Eval agents run without web tools, so no case content can leave in a
  search query.
- **Residual, stated honestly:** each eval run's own transcript is saved by Claude Code in
  the same `~/.claude/projects/` area, with the same permissions and the same backup as
  the source transcript. That adds no new place where the data lives, but it does add
  copies there. Louis can remove those run transcripts himself afterwards.
- **"Dropped" means:** the dev-only eval leaves no prompts, fixtures or reference lists in
  SpaceSuit. Only the general patterns survive, and they already appear in this document.

Grading: an agent judge compares the skill's axes and options against a reference list.
For the real sessions, that list is the axes Louis raised late and the options he reached
for. Each judge sees the transcript only, never this repo.

## Left to the builder

- The exact wording of the prompts, the sub-agent count per mode, and the file layout of
  the references.
- The record template.
- The eval mechanics, following `superpowers:writing-skills`: compare runs with and
  without the skill, within the rules in "Testing with private material".

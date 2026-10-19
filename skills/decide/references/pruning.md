<!-- ABOUTME: Consequences, MUST filter, dominance, Even Swaps and the question gate. -->
<!-- ABOUTME: How the decide skill turns many options into a small Pareto set with clear trade-offs. -->

# From options to the Pareto set

## 1. Consequences as ranges

For each option and each axis, give *pessimistic / likely / optimistic*, in the axis's own
unit (money, months, number of moving parts, "relationship strained / neutral / warmer"). Use
a source when a fact is needed, and say which estimates are guesses.

## 2. MUST filter

Drop any option whose *likely* or *pessimistic* outcome breaks a MUST. Say which MUST in one line,
since the dropped option may be one Louis expected to see.

## 3. Dominance

Option X dominates Y when X is at least as good on every axis and better on one. Drop Y.
Compare using the likely value, and keep any Y whose optimistic value beats X on an axis
Louis ranked high (note why it was kept).

## 4. Even Swaps (making trade-offs explicit, no weights)

For two survivors, find the axis where they differ least. Ask what change on another axis
would make that difference worth nothing to Louis. Then rewrite one option as if it had been
swapped. Repeat until one option dominates or until the trade-off is down to one plain
sentence.

Worked example (invented): two job queues.

| | Self-hosted queue | Managed queue |
|---|---|---|
| Cost/month | 0 | 40 |
| On-call burden | medium | low |
| Time to ship | 1 week | 2 weeks |

If Louis would pay 40/month to cut the on-call burden from medium to low, then swap: the
self-hosted queue at "cost 40, burden low" is equivalent to it. Now the only difference is time
to ship, which the self-hosted queue wins. If he would *not* pay that, the trade-off is stated
as **"40/month buys a lighter on-call; self-hosted ships a week sooner"**. That one sentence is
the output, not a score.

Never multiply scores by weights. When Louis's answer to a swap question is unknown, the swap
question itself goes in **Your call**.

## 5. Fragile survivors

Mark an option that survives only under one assumption about another party ("works only if
they value equity over cash"). In a negotiation, that assumption is a question for the other
party.

## 6. Sensitivity points (design lens)

Name the assumption where a small change flips the choice ("above about 50k jobs a day, the
managed queue wins"). These become the revisit triggers.

## Question gate (value of information)

An unknown becomes a question to Louis only if all three hold:

- the context does not contain the answer (search it first);
- some plausible answer would change which options are Pareto-efficient or which one leads;
- he can answer it now.

Otherwise it is an assumption with a confidence level. An unknown about another party
usually goes to Louis as a question **he** asks **them**.

<!-- ABOUTME: Template for a decide skill decision record (one file per decision). -->
<!-- ABOUTME: Records live in ${DECIDE_DIR:-~/Decisions}, never in a repo; sub-agents read them by path. -->

# Record template

Copy everything below the line into the new record. Keep the front matter keys stable,
since a sync may read them later.

---

```markdown
---
date: YYYY-MM-DD
slug: <slug>
lens: choice | negotiation | design (combinations allowed: negotiation+design)
mode: full | quick
status: open | decided
---

# <the decision, one line>

## Frame
Deciding: … · Not deciding: … · Deadline: …
For (principal): …

## Stakeholders
- <party>: <interest> (stated | assumed, low/medium/high)

## Axes and ideals
- **<axis>**: <ideal outcome>

## Never
- <MUST>

## Pre-mortem
- <how it could go badly>

## Assumptions
- <fact> (confidence)

## Constraint log
- <YYYY-MM-DD HH:MM> <constraint> (source: Louis | context)

## Options
- <name>: <one line> · family: <family>

## Pareto set
| Option | Wins on | Gives up |
Trade-offs: <Even Swaps sentences>

## Open questions
- <question> (who answers: Louis | the other party)

## Outcome
<filled when Louis decides: what, when, and why>
```

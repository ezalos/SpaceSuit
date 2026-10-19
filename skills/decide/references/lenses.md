<!-- ABOUTME: The three lenses of the decide skill (choice, negotiation, design) and their outputs. -->
<!-- ABOUTME: Each output template is filled at the last step; the negotiation lens wins when combined. -->

# Lenses and output templates

Every template is phone-readable: lead with the decision, keep the reasoning short, bold
the key words, and separate anything to paste. Each lens has its own template; the
always-ending sections come after it (see SKILL.md step 4).

## Choice: pick among options

```
**Lean:** <option>: <one line why>

| Option | Wins on | **Gives up** | Worst case |
|---|---|---|---|
| … 2–4 Pareto survivors, one row each; the losing axis in bold |

**Trade-off in one line:** <the Even Swaps sentence>
**First moves:** <dated step> · <dated step>
**Revisit if:** <the condition that flips the lean>
```

## Negotiation: someone else must agree

Interests come before positions. Know Louis's BATNA (his best alternative if there is no
deal) and estimate theirs. The Pareto frontier is **joint**: look for terms that cost one
side little and are worth a lot to the other.

```
**Walk-away:** <his BATNA, one line> · **Their likely BATNA:** <…> (*assumed*)

**Menu** (each option is a whole package):
1. **<name>**: he gets <…> · they get <…> · breaks if <…>
2. …  (2–4 packages; presenting 2–3 at once (MESOs) shows what they value)
   Plus **reference rows:** accept as is · walk away to the BATNA, each with what it gives up

**Every package keeps:** <invariant> · <invariant>
**Open with:** <which package or question first, and why>
**Ask them:** <the question that resolves the most important assumed interest>
```

Never a single "the answer" while another party has a say.

## Design: building something

Options are combinations of sub-choices. Keep several alive until the data decides
(set-based design). The output is a decision record (ADR-shaped):

```
**Decision:** <chosen or leading option> · **Status:** proposed

**Context:** <the forces in two lines: load, team, constraints>
**Options considered:**
- **<option>**: wins on <…>, **gives up** <…> · rejected / kept because <…>
**Trade-off points:** <where improving one quality attribute costs another>
**Sensitivity points:** <assumption> → flips to <option> if it changes
**Revisit if:** <measurable trigger>
```

## Combined lenses

When another party has a say, negotiation wins. The menu items can each carry the design
fields (owner, interfaces, when it breaks), but none of them is labelled the chosen one.

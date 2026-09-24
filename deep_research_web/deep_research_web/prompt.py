# ABOUTME: Renders a charter as the prompt for a claude.ai Research conversation.
# ABOUTME: Carries the citation instruction that makes QUOTED grades possible downstream.
from __future__ import annotations

from deep_research.charter import Charter

CITATION_RULE = (
    "Cite a specific page for every claim, never a bare domain or a section index. "
    "Wherever a claim rests on a number, a date or a sentence, quote the page verbatim "
    "in double quotes right next to the claim."
)

# Nobody is at the keyboard: a clarifying question would sit unanswered until the run
# goes stale, so the charter is the whole brief and the run starts on it as given.
START_RULE = (
    "Do not ask clarifying questions; start researching immediately with the charter as given."
)


def build_web_prompt(charter: Charter) -> str:
    must = "\n".join(f"- {q}" for q in charter.must_answer)
    scope = "\n".join(f"- {s}" for s in charter.out_of_scope) or "- nothing excluded"
    return f"""Research question:
{charter.question}

This decision depends on the answer:
{charter.decision}

You must answer every one of these, each under its own heading:
{must}

Source bar:
- tier: {charter.source_tier}
- recency: {charter.recency}

Deliverable shape:
{charter.deliverable}

Out of scope, do not spend effort here:
{scope}

{CITATION_RULE}
{START_RULE}
If a must-answer question cannot be answered from available evidence, say so plainly
under its heading. A documented gap is worth more than a confident guess.
"""

# ABOUTME: Builds the prompt handed to the detached research agent.
# ABOUTME: Carries the output contract and the citation rules; report quality lives here.
from __future__ import annotations

from pathlib import Path

from .charter import Charter
from .status import DONE_SENTINEL, REPORT_NAME, RESULT_NAME, SOURCES_NAME

PREAMBLE = """You are running an unattended deep research task. Nobody is watching, so
finish the job and write the files: an unwritten answer is a failed run.

Research method:
- Use WebSearch to find candidate sources and WebFetch to read them.
- Read a page before you cite it. A source you have not fetched is not a source.
- Prefer primary sources over coverage of primary sources.
- When sources conflict, say so explicitly and prefer the most recent from the most
  reputable publisher, rather than silently picking one.
- If a must-answer question cannot be answered from available evidence, say that
  plainly in the report. A documented gap is worth more than a confident guess.

Citation rules, which are absolute:
- Every data claim carries an inline [n] marker keyed to sources.md.
- Every source is an exact, live, clickable URL to the specific page carrying the
  claim. Never a bare domain, never a section index, never a redirect.
- Every source entry quotes the page verbatim, proving it says what you cite it for.
- If a source cannot be fetched and quoted, list it under unverified in
  run-result.json and say so in the report. Never silently downgrade it.
- An unverified source may NEVER back an [n] marker. Listing a source as unverified is
  not permission to cite it anyway: drop the claim it would have supported, and send the
  must-answer question it belonged to to unanswered instead. Every [n] marker in the
  report must point to a source you fetched and quoted.
"""


def build_runner_prompt(
    charter: Charter,
    out_dir: Path,
    notify_script: str | None = None,
) -> str:
    # resolve(), not just Path(): the prompt promises absolute paths, and the detached
    # agent's working directory need not match the caller's, so a relative path would
    # land the output files somewhere the poller never looks.
    out = Path(out_dir).resolve()
    must = "\n".join(f"- {q}" for q in charter.must_answer)
    scope = "\n".join(f"- {s}" for s in charter.out_of_scope) or "- nothing excluded"

    notify = ""
    if notify_script:
        notify = (
            f"\nWhen the sentinel is written, announce completion by running:\n"
            f'  {notify_script} done "<one line summary of what you found>"\n'
        )

    return f"""{PREAMBLE}
Research question:
{charter.question}

This decision depends on the answer:
{charter.decision}

You must answer every one of these:
{must}

Source bar:
- tier: {charter.source_tier}
- recency: {charter.recency}

Deliverable shape:
{charter.deliverable}

Out of scope, do not spend effort here:
{scope}

Write exactly these files, using absolute paths:
1. {out / REPORT_NAME}
   The report, with inline [n] markers on every data claim.
2. {out / SOURCES_NAME}
   A numbered list matching those markers. Each entry: the exact URL as a clickable
   Markdown link, the publishing authority, the page title, the date you accessed it,
   and a verbatim quote from the page supporting the claim.
3. {out / RESULT_NAME}
   JSON with keys: status ("complete" or "partial"), sources_total, sources_verified,
   unanswered (list of must-answer questions you could not answer), unverified
   (list of objects with url and reason), and sources.

   sources is a list holding one object per [n] marker used in the report:
     {{"n": 1, "url": "<the exact page URL>", "quote": "<text copied from that page>"}}

   Each quote must be a contiguous span copied verbatim from the page you fetched,
   long enough to be unambiguous (roughly 5 to 25 words) and short enough to sit on
   one line. Do not paraphrase, do not stitch separated fragments together, do not
   summarise. EVERY ONE OF THESE IS REFETCHED AND CHECKED AGAINST THE LIVE PAGE after
   the run finishes, so an invented or approximated quote is not a shortcut that
   works: it comes back as CONTRADICTED and fails the run. If you cannot copy an exact
   supporting span, the source belongs in unverified and the claim it would have
   supported comes out of the report.
4. {out / DONE_SENTINEL}
   An empty sentinel file. Write it LAST, after all three files above are complete.
   It is the only signal the caller polls, so writing it early reports a half-finished
   run as a finished one.
{notify}"""

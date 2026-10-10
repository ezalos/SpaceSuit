---
user-invocable: true
allowed-tools: Read, Write, Glob, Grep, Bash, Agent
description: Adversarial audit of a paper's references before submission - every bib entry checked against a primary record (arXiv, Crossref, the cited page), every citing sentence checked against the cited paper's own text with a verbatim quote, and a scan for LLM tells (meta-comments, placeholders, illustrative data). Use before any arXiv or venue submission, or when the user asks to "check the citations", "audit the references", "make sure no reference is hallucinated". Read-only on the paper; writes a report.
---

# paper-citation-audit

arXiv's code of conduct makes every author responsible for every reference, however the text was produced. A
hallucinated reference, or an LLM meta-comment left in the text, counts as "incontrovertible evidence" that the
authors did not check, and the penalty is a 1-year ban followed by mandatory peer review. This skill is the check:
assume every citation is wrong until a primary source proves it. The scripts never call a reference good on weak
evidence (anything short of VERIFIED is a finding) and never let a citing sentence go unjudged in silence (it shows
as UNCHECKED).

It never edits the paper. It writes a report; the paper's owner fixes.

## What it guards against

Honest authors whose paper may hold LLM-made mistakes: an invented or wrong reference; a wrong venue, year, title or
author list; a sentence that attributes to a source a claim the source does not make; a meta-comment, placeholder or
invented number left in any shipped file (text, comments, style files, figures, the .bib). It is built for normal
LaTeX habits: macros, `\input` and its kin, captions, conditionals, natbib and biblatex. Out of scope: a deliberate
attacker hiding citations or text from the tool. The citation-count guard, for instance, trusts that the paper was
built from the source it is given.

## Trigger

`/paper-citation-audit <latex dir or main .tex>`

## Inputs

- **Build the paper first**, from the current source: `tectonic -X compile --keep-intermediates main.tex`
  (`--keep-intermediates` keeps `main.aux`, `.toc`, `.lof`), or `latexmk -pdf main.tex`, or pdflatex, bibtex,
  pdflatex twice. Both outputs are required: `main.aux` with its `.toc`/`.lof`/`.lot` beside it (the citation-count
  guard of step 2; refused when older than any .tex it covers) and the PDF (required for full recall:  the only place
  where macro-made text, numbers and tells are visible; without it step 3 misses them and says so).
- The `.bib` file(s) and the main `.tex`; files it pulls in are read in by step 2.
- `S="$HOME/.claude/skills/paper-citation-audit/scripts"`, `SF="<SpaceSuit checkout>/scholar_fetch"` (the
  open-access fetcher).
- `O="$HOME/.cache/paper-citation-audit/<slug>"` for all outputs: a persistent directory, not /tmp, which may be
  small and cleared on reboot while a run's texts and verdicts are still needed.
- **Every path passed to a script, and every path in a subagent brief, is absolute** (`$HOME` expanded, not `~`):
  step 4 runs from `$SF`, and subagents start in another directory.
- Optional `CONTACT_EMAIL` in the environment puts the Crossref and OpenAlex requests in their polite pool.

Every script takes `OUT_DIR` first, then its inputs (any number of `.bib` files), and removes its own output file
before it starts, so a refusal never leaves a stale result.

## Steps

Run 1 to 4 yourself; they are deterministic. Steps 5 and 6 are the adversarial part.

1. **Bib entries against primary records.** `python3 -I $S/bib_check.py $O <refs.bib> [more.bib ...] [--only KEY]`
   The parser counts the raw `@`-entries and refuses a file unless it parsed exactly as many with no error, and
   refuses a key defined twice, in one file or across files (fix the .bib, never work around it). Every arXiv id and
   DOI the entry names in ANY field (eprint, doi, url, journal, note, howpublished, a DBLP `volume = {abs/<id>}`; not
   the abstract) is looked up, and every record must agree: arXiv ids through the arXiv API, DOIs through Crossref
   then DataCite (an arXiv DOI through arXiv). Ids are read whole: `2106.012345` is malformed, never the paper
   2106.01234. A url naming neither (an openreview forum, a project page) beside an id is a `note:`; an eprint of
   another archive (pubmed, hdl) too. Only an entry with no identifier falls back to its cited page (parsed
   `citation_*` meta tags or `<title>`, never the URL) and then to an OpenAlex title search. Each record is compared
   on the title (normalised equality; Greek letters keep their names), the whole author list in order by surname and
   first initial ("and others" allows a longer record list; a brace-protected corporate author is compared whole; the
   whole family name), the year (`year`, or `date`; arXiv: the v1 year or the journal_ref year), and every printed
   venue field: journal, journaltitle, booktitle, eventtitle, a non-link howpublished, series, institution, school,
   organization, and any venue claim in note, addendum or type (a lead phrase such as "Accepted at", an acronym or a
   year left once links and ids are removed: "ICML 2023", "Oral presentation, ICML 2017"). Venues match on equal word
   sets once filler is dropped, or through an acronym with nothing left over, never a subset (Nature is not Nature
   Communications, CVPR is not CVPR Workshops; journal, international, IEEE and ACM are distinguishing words). When a
   venue field is still unconfirmed, OpenAlex (by DOI or arXiv DOI, every location; else an exact-title search for a
   published version) and DBLP (by exact title) are asked, one request each. A field that is only an arXiv/CoRR form
   needs no venue check, but an id inside it ("arXiv preprint 1706.03763", "CoRR abs/1706.03763") is looked up, as is
   a Mendeley `arxivId` field; an OpenAlex location that is a repository (Zenodo, a university archive) never
   confirms a venue; a `crossref` field is VENUE_UNCHECKED; title, subtitle and titleaddon are compared together; a
   duplicate field is refused. Every year inside a venue field or note claim must be the entry's year or a record's
   (else MISMATCH: year (venue)), and a `date` must agree with `year` (else MISMATCH: year (date)). A main venue
   field the DOI's own record (Crossref, DataCite) contradicts is MISMATCH: venue "another version?", whatever a
   title search finds elsewhere. howpublished is checked on its text beside any link, and a note that is only a name
   ("Nature Machine Intelligence") is a venue claim. A DOI record that is an erratum or correction is MISMATCH:
   record type (erratum). Two entries with one normalised title get a DUPLICATE_ENTRY note. Volume, pages, publisher,
   address, month, editor and edition are never checked (every entry carries that note). What could not be checked is
   a `note:` line; lookup errors print as `ERROR` lines. `--only KEY` (repeatable) re-checks single entries.
2. **Where each key is cited.**
   `python3 -I $S/cite_contexts.py $O <refs.bib> [more.bib] <main.tex> <main.aux> [--also-cite=NAME,...]
   [--cite-macro=NAME=key1,key2 ...]` Gives every citing sentence with its line, the sentence before and after, keys
   cited but missing from the bib, bib entries never cited, every `.tex` under the paper directory never read in
   (`tex_not_read`), files `\input` in the preamble (`preamble_inputs`: macro definitions, whose text reaches the
   reader only through macros, so check it in the PDF), `notes` and `findings`. A sentence that stands in several
   places is one row whose `occurrences` list each place (file, line, prev, next, table). Every cite anywhere inside
   a table float (`table`, `table*`, `sidewaystable`, or a minipage or center with `\captionof{table}`: caption,
   `\tablenote`, threeparttable notes, a "Source: \cite{x}" row) carries the whole float as `table`, its `\input`
   files inlined, and so does a cite whose sentence or neighbour names a table's `\label` as the argument of any
   command (`\ref`, `\cref`, `\autoref`, a custom `\tabref`, `\hyperref[...]`); a figure holding a tabular counts as
   a table float, and a label inside an `\input` file counts; a float holding what the scan cannot read
   (`\includegraphics`, `\pgfplotstabletypeset`, `\csvautotabular`) carries an "unreadable, check the PDF" marker
   instead. Every `\input` (braced or not), `\include`, `\subfile`, `\import`, `\subimport`, `\inputfrom` and
   `\includefrom` is read in (printed as `read in ...`); one that resolves to no file is refused. The preamble is
   skipped (except the `\title`, `\author` and `\thanks` arguments, which `\maketitle` typesets), and every
   `\newcommand`/`\renewcommand`/`\providecommand`/`\DeclareRobustCommand`/`\newrobustcmd`/`\renewrobustcmd`/
   `\providerobustcmd`/`\def`/`...DocumentCommand` (head and body) is skipped wherever it sits; a definition whose
   body cites fixed keys refuses until `--cite-macro=NAME=key1,key2` names it (each use of `\NAME` then cites those
   keys), one that passes `#1` to a cite command until `--also-cite=NAME` names it. Completeness comes from LaTeX
   itself: each `\citation` line of `main.aux` (and of the aux files it `\@input`s; with biblatex, its
   `\abx@aux@cite` lines, `\textcite` counting twice) is one expanded citation call, compared per key with the source
   scan in both directions. More in the .aux: a macro, a `\let` alias, or an unknown command hides citations, and the
   run refuses (declare it with `--also-cite`, an alias of a cite command, or `--cite-macro`). More in the scan: a
   cite LaTeX never expanded (a false conditional, `\verb`, an excluded environment), refused. A surplus that the
   build's `.toc`/`.lof`/`.lot` explain (a heading or caption printed again in its list) is a `notes` line. With no
   such list in the build, exactly one extra call per caption or heading cite (a caption measured, then set) is a
   `CAPTION_TWICE` note (MINOR); any other remaining surplus on a key cited in a caption or heading is a
   `CAPTION_SURPLUS` finding listing every source line of the key (usually a caption measured twice; confirm in the
   PDF); any other surplus refuses. It also refuses an unknown cite-like command and a cite command with zero keys.
   Before calling a never-cited key harmless, `grep -rn '<key>' <latex dir>`.
3. **LLM tells.** `python3 -I $S/llm_tells.py $O <paper dir> <paper.pdf> --bundle <submission dir or tarball>`
   Pass `--bundle` whenever the arXiv submission exists (the directory or tarball you upload): the file scan then
   covers exactly the files that ship, since notes kept beside the source (a refs-check.md) may not. Markdown
   formatting (`**bold**`, `## heading`) is a tell in TeX-like files and the PDF only, never in .md or .txt files,
   where it is the format.
   Scans every text file under the paper directory (.tex .bib .sty .cls .bbl .tikz .pgf .def .txt .md) and every file
   a TeX file `\input`s whatever its suffix: typeset text, and, in TeX-like files, the `%` comments and hidden blocks
   that ship in the public arXiv source; and the PDF text. Before matching, `\cmd{...}` wrappers are reduced to their
   content, `~`, `\\` and zero-width characters read as a space, and lines are joined, so `\emph{Here} is the revised
   paragraph` and phrases wrapped over lines are found. BLOCKER hits: meta-comments ("here is the revised paragraph",
   "below is the revised", "I have revised", "I've made the following changes", "this paragraph was rewritten", "in
   this version, I", "hope this helps", "of course!", "remember to cite", "as a large language model", "I apologize",
   "Changes made:", "Summary of changes", "As requested,", "I've kept", "Revised version:", "Great question!",
   markdown such as `**Key changes**` or a `## heading`), placeholders (`[Your Name]`, `[Affiliation]`, `[Email]`,
   `[NUMBER]`, `[value]`, `[INSERT ...]`, `[X]%`, `8X%`, `XX`, `XX.X`, `9X.4`, lorem ipsum), missing or unresolved
   citations (`??`, `(author?)`, `(?)`, `[citation]`, `[Reference]`, `[ADD REF]`, `[ref needed]`, `(add citation)`,
   `<citation>`), todo/tbd/fixme. REVIEW hits (illustrative, placeholder, hypothetical, `[n]`, "here are the", "feel
   free to", "of course,", "this revised version", "to be confirmed", "results pending", "Anonymous Authors", "please
   verify these numbers", a first-person-singular sentence ("I made", "I've", "I tightened") in the typeset text or
   the PDF, and, in the PDF text only, any bracketed phrase such as `[Some Words]`): legitimate in a paper, so read
   each one. In the typeset body of the .tex files only (never the PDF, where natbib's own output looks the same, nor
   comments), a citation typed as text is a BLOCKER, since it never reaches the .aux, the bib check or the claim
   check: a name with "(2023)" or "[2023]" ("Smith (2023)", "Chi and Song (2023)", "Brown et al.'s (2020)", "OpenAI
   (2023)", "Smith et al. [2023]"), any parenthesised group ending in a year ("(Smith 2023)", "(Liu et al. NeurIPS
   2023)"; "(July 2023)" is a false alarm to dismiss), and "et al., RSS 2023", "Liu and colleagues (2023)". REVIEW,
   in the same scope: a literal `[12]` outside math; a sentence naming "X et al." with no cite command, or only
   `\citeyear`; an identifier typed as text and never looked up (`arXiv:2306.03310`, `doi:10.`, an arxiv.org or
   doi.org link).
4. **Fetch every cited paper's text.**
   `cd $SF && uv run --quiet --project . python $S/fetch_texts.py $O <refs.bib> [more.bib]`
   A run starts by DELETING `$O/texts/*.txt`, `$O/verdicts-*.jsonl` and `$O/verdicts-checked.json` from any previous
   run, so a failed re-fetch can never leave a stale text and old verdicts never mix with new ones; re-running step 4
   after step 5 means re-running step 5. `fetched.json` gives per key the `text_file` (the key is percent-encoded in
   the file name), the step that served it, `chars`, `pdf` (whether the body was a PDF) and `landing`. Any text that
   is not a PDF, or is under 5,000 chars, is `landing: true`: a landing page or abstract, not the paper.
5. **Adversarial claim check, by subagents.** Split the cited keys into batches of about 6 (each batch a set of
   keys, with all their rows from `contexts.json`), and dispatch one `general-purpose` Agent per batch, in parallel,
   with the brief below. Each writes `$O/verdicts-<batch>.jsonl`.
6. **Quotes checked by machine.** `python3 -I $S/quote_check.py $O` re-finds every quote in the fetched text (case,
   punctuation and line-break hyphens ignored, minus signs kept, `$-$`, `\(-\)`, `{-}` and `\textminus` read as a
   minus sign, "10,030" as one number, whole words only). A SUPPORTED, PARTIAL or CONTRADICTED verdict needs a quote
   found in the text, else QUOTE_NOT_FOUND; a PARTIAL goes to NEEDS_REVIEW when a number of its claim is not in its
   quote, or a number of its sentence is in neither its claim nor its quote. A SUPPORTED quote that hedges (suggest,
   may, might, could, preliminary) behind a sentence that asserts (shows, proves, demonstrates, always, establishes)
   goes to NEEDS_REVIEW. A SUPPORTED or PARTIAL quote the source negates right before it (not, no, never, nor,
   without, fails to) goes to NEEDS_REVIEW. The claim reaches past its sentence, in both directions: the numbers of
   the next sentence when it has no cite of its own, of the previous sentence when it has none and the citing
   sentence opens with a pronoun, demonstrative or back-reference (these, this, they, above, both, former, latter,
   aforementioned, previous, preceding, same, see, cf., source, from: "The numbers above are reported by ..."), and
   of the table around or named by the cite (`table`), at every occurrence of a repeated sentence, must be in the
   claim or the quote, else NEEDS_REVIEW; an unreadable table is NEEDS_REVIEW too. False alarms are expected: a human
   rules on each. A SUPPORTED verdict goes to NEEDS_REVIEW when the citing sentence holds a user macro (its value is
   only in the PDF), when a number of the claim is not in the quote with its sign, when a number of the citing
   sentence is in neither the claim nor the quote, or when the quote shares no content word with the claim. Verdict
   values are upper-cased and validated; NO_TEXT stands only when no text was fetched or `fetched.json` says
   `landing`, else it is INVALID_VERDICT. Every (key, sentence) of `contexts.json` with no valid verdict comes out
   UNCHECKED; a verdict whose sentence is not in `contexts.json` is an orphan: it covers nothing and is left out of
   the totals. Agents' quotes are never trusted on their word.
7. **Re-run lookup errors.** Before writing the report, re-run step 1 (`--only KEY`) for any entry with a LOOKUP_ERROR,
   or a VENUE_UNCHECKED with an `ERROR` line (a 429 or a timeout says nothing about whether the paper exists); report
   one only if it persists, as LOOKUP_ERROR, never as NOT_FOUND. Re-dispatch the batches whose pairs came out
   UNCHECKED or INVALID_VERDICT, then re-run step 6 so their new verdicts are checked too.
8. **Report.** Write `$O/REPORT.md` (and copy it next to the paper's review files if the project keeps them):
   - A severity for every finding, from the table below.
   - For each finding: the key, the sentence (file:line and the sentence itself), the status or verdict, the quote
     or the record that disagrees, and the fix (reword the sentence, cite a different source, correct the entry).
   - Counts: entries per status, (key, sentence) pairs per verdict, texts not fetched or landing only.
   - What was NOT checked: every `note:` line of step 1, sources with no paper text, claims about numbers in figures
     the text extraction cannot read.

## Severities

| Status, verdict or output field | Severity |
|---|---|
| bib-check.json `ID_NOT_FOUND: <field>` (the id is malformed or names no record), `NOT_FOUND` | BLOCKER |
| `MISMATCH: title`, `MISMATCH: authors`, `MISMATCH: venue` | MAJOR (BLOCKER if the record is a different paper) |
| `TITLE_VARIANT` (similar but unequal titles, both shown: LIBERO-Pro vs LIBERO-Plus is one) | MAJOR until a human rules |
| `LOOKUP_ERROR: <field>` after the re-run of step 7 | MAJOR (unverified) |
| `MISSING_FIELD: <field>` (absent, empty once LaTeX is stripped, or authors only "others") | MAJOR |
| `VENUE_UNCHECKED` (a venue field no record, OpenAlex or DBLP entry confirms; a crossref field) | MAJOR |
| `AUTHORS_UNCHECKED` (no record lists the authors) | MAJOR |
| `MISMATCH: year`, `MISMATCH: year (venue)`, `MISMATCH: year (date)`, `YEAR_UNCHECKED` (no record carries a year) | MINOR |
| `MISMATCH: record type (erratum)` (the DOI names an erratum or correction) | MAJOR |
| note `DUPLICATE_ENTRY` (two entries, one title) | MINOR |
| `WEAK_TITLE_SEARCH` (no id; matched by an OpenAlex title search) | MINOR, MAJOR with any MISMATCH |
| `PAGE_MATCH` (no id; the cited page's metadata agrees) | MINOR |
| `VERIFIED` with `note:` lines | none, but every note goes under "not checked" |
| contexts.json `cited_not_in_bib` | BLOCKER |
| contexts.json `findings` `CAPTION_SURPLUS` | MAJOR |
| contexts.json note `CAPTION_TWICE` (one extra call per caption cite, no list in the build) | MINOR |
| contexts.json `tex_not_read` | MAJOR until a human rules each file is not part of the paper |
| contexts.json `in_bib_not_cited` | MINOR |
| contexts.json `preamble_inputs` (macro files; their typeset text is checked in the PDF only) | none; list them |
| contexts.json `notes` (a surplus the .toc/.lof/.lot explain) | none; list it |
| tells.json BLOCKER level, "citation as plain text: never audited" included | BLOCKER |
| tells.json REVIEW level (a literal `[12]`, "et al." with no cite command, an identifier typed as text included) | MINOR |
| verdicts-checked.json CONTRADICTED | BLOCKER |
| NOT_SUPPORTED, QUOTE_NOT_FOUND (a SUPPORTED, PARTIAL or CONTRADICTED quote missing or not in the text), UNCHECKED, INVALID_VERDICT (also a NO_TEXT given on a fetched full text) | MAJOR |
| UNVERIFIED (NO_TEXT given on a long non-PDF text, which may be the paper itself) | MAJOR |
| NEEDS_REVIEW (a user macro in the sentence; a number of the claim or sentence, or its sign, not where it must be; or a quote sharing no content word with the claim) | MAJOR until a human rules |
| NO_TEXT (the claim is unverified, like UNCHECKED) | MAJOR |
| PARTIAL | MINOR |
| SUPPORTED, quote found and every check passed | none |
| an orphan verdict row (its sentence is not in contexts.json) | none itself, but its pair stays UNCHECKED |

## Known limits

- The `.aux` guard compares counts per key, so two independent defects on one key can cancel: a hidden citation
  (an undeclared macro) and a cite LaTeX never expanded (in a false `\if...`) on the same key leave the counts equal.
  Definitions are checked on their own (a definition citing fixed keys refuses until declared), which closes the
  common case; the rest is why `tex_not_read`, the PDF and a reading of each refusal still matter.
- Volume, pages, publisher, address, month, editor and edition are never checked. DBLP may answer a bot check
  instead of data; the venue then stays VENUE_UNCHECKED with an `ERROR` line.
- Venue names match by words, acronyms and a short alias table (NeurIPS/NIPS/Advances in Neural Information
  Processing Systems, IROS); another renamed venue gives a false MISMATCH: venue to rule on.
- Plain-text citations without a year ("LIBERO (Liu et al.)") are only REVIEW (et al. without a cite), and a
  name-only mention ("the LIBERO paper") is not caught at all. A claim about a figure is checked only through its
  caption: plotted values are not read.
- Meta-comments and placeholders come from a finite list of phrases; a new phrasing passes. Read the built PDF
  once, end to end, before submitting: the scan is a net, not a reader.
- The claim checks use a neighbour sentence and a table float; an attribution that runs on for several sentences
  rests on the agents' brief.
- Text produced by macros (`preamble_inputs`, `\newcommand` bodies) is only seen in the PDF; numbers set by them are
  flagged as user macros, not checked.

## Subagent brief (step 5; fill the angle brackets, all paths absolute)

> You are an adversarial reviewer checking citations in a paper before arXiv submission. arXiv bans authors for a
> year when a reference is hallucinated or misattributed, so assume each citation is WRONG until the cited paper's
> own text proves it.
> For each key below, read its `text_file` from `<O>/fetched.json` (the cited paper, extracted from its PDF; when
> `landing` is true it is a landing page or abstract, not the paper). The texts contain binary bytes: search them with
> `grep -a`. For each citing sentence (its `prev` and `next` sentences are given for context), state the claim the
> sentence attributes to that source (only the part the citation covers), then search the text for it. Verdicts:
> SUPPORTED (the text states it; give a verbatim quote of at least 20 characters copied exactly from the text file,
> not from memory, holding the claim's key words and every number of the claim with its sign; a number of the
> sentence that the citation does not cover belongs in neither the claim nor the quote, so say so in the note),
> PARTIAL (the text supports part, or supports it only loosely; quote the part it supports, verbatim, and say which
> part fails; a source that states it more weakly, such as "suggests" or "may" behind the sentence's "shows" or
> "proves", is PARTIAL), NOT_SUPPORTED (the text does not say it), CONTRADICTED (the text says otherwise; quote
> it verbatim), NO_TEXT (no text file, or `landing` is true and the text does not contain the claim; when a
> landing text does contain it, judge it as usual).
> Numbers must match exactly (a "79.6" in the sentence needs 79.6 in the source, or say where it comes from). A number
> hidden behind a LaTeX macro in the sentence is in the built PDF's text: `pdftotext <paper.pdf> -` and `grep -a` it.
> The claim includes what continues the attribution, in both directions: a following sentence with no cite of its
> own that goes on about the source ("It reaches 79.6% on LIBERO-90", "The authors report ..."), a preceding
> sentence the citing one refers back to ("ACT reaches 79.6% ... These numbers are reported by \citet{act}"), and
> a table or figure whose caption or notes cite the source (the float is in the `table` field); state those parts
> in the claim and check them too.
> Names of methods, datasets and authors must match. A sentence that cites a paper for something that paper only
> cites from elsewhere is PARTIAL.
> Do not use your memory of the paper; only the text file counts. Do not browse.
> Write one JSON object per line to `<O>/verdicts-<batch>.jsonl` with keys: key, file, sentence (copied exactly from
> contexts.json), claim, verdict, quote, note. One line per (key, sentence) pair you were given, none skipped. Then
> reply with the counts per verdict and the 3 most serious problems.
> A row whose `occurrences` lists several places stands in each of them: check the claim at every place (each has
> its own prev, next and table). When a sentence, or its neighbour, names a table (Table~\ref{...}), open that
> table (its text is in `table`; else find it in the paper or the PDF) and check its numbers too.
> Keys and sentences: <paste the batch from contexts.json, every row with all its occurrences>

## Rules

- Read-only on the paper and its repository. Outputs go to `$O`; copy the report where the project wants it.
- Never fix a finding yourself in the paper: hand the report to its owner.
- A finding needs its evidence: the record that disagrees, or the quote, or the line. "Looks wrong" is not one.
- Network: arXiv asks for one API request every 3 seconds (bib_check spaces them); 429 and 5xx answers are retried
  with backoff, and what still fails is a LOOKUP_ERROR, never a NOT_FOUND. Nothing here costs money.
- Tests: `cd $HOME/.claude/skills/paper-citation-audit && uv run --no-project --with pytest python -m pytest -q
  -p no:cacheprovider --basetemp <a dir under $HOME/.cache> tests/` (no network: every lookup is canned).

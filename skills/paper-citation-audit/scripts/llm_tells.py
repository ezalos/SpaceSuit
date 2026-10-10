# ABOUTME: Scans every .tex and .bib under a paper dir (typeset text and the comments shipped in the arXiv source) and
# ABOUTME: the PDF text for unchecked LLM output; writes tells.json. Usage: llm_tells.py OUT_DIR PAPER_DIR paper.pdf
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts, also under python -I
from bib_check import fresh_output, split_args
from cite_contexts import INPUT, blank, body, input_name, resolve_input, sentence_spans, visible

TEX_LIKE = {".tex", ".sty", ".cls", ".bbl", ".tikz", ".pgf", ".def"}  # % starts a comment in these
TEXT_SUFFIXES = TEX_LIKE | {".bib", ".txt", ".md"}  # every text file an arXiv source upload may ship
EXCERPT_CHARS = 80  # context shown on each side of a hit
# Patterns run case-insensitively on whitespace-joined text with LaTeX wrappers removed, so a phrase wrapped over two
# source lines, or split by \emph{}, ~ or a zero-width space, still matches.
WRAPPER = re.compile(r"\\[A-Za-z]+\*?\{([^{}]*)\}")
UNWRAP_PASSES = 3  # nested wrappers such as \textbf{\emph{x}}
SPACE_LIKE = re.compile("(~|\\\\|[\u200b-\u200d\u2060\ufeff])")  # ~, \\ and zero-width characters read as a space
MARKDOWN = "markdown formatting (an assistant's reply)"  # TeX-like files and the PDF only
_EDITED = r"(?:revised|rewritten|updated|improved|edited|polished|corrected|rephrased)"
BLOCKER = {
    "assistant meta-comment": r"\b(?:here is|here's|here are) (?:a|an|the|your)\b.{0,40}?\b(?:summary|draft|version|"
                              r"paragraph|section|rewrite|revised|revision|changes|edits|key points|abstract|"
                              r"introduction|conclusion|text)\b|"
                              r"would you like me to|let me know if|\b(?:i )?hope this helps\b|\bas an ai\b|"
                              r"\bas a (?:large )?language model\b|\bi (?:can|cannot|can't) (?:help|assist)\b|"
                              r"certainly[!,] here|\bsure[!,.]? (?:below|here) (?:is|are)\b|"
                              r"as of my (?:last )?(?:knowledge )?(?:update|cutoff|training)|my knowledge cutoff|"
                              r"\bi apologi[sz]e\b|\bof course!|\bremember to (?:cite|add|insert|replace)\b|"
                              rf"\bbelow is (?:the|a|an|your) {_EDITED}\b|\bi(?: have|'ve) {_EDITED}\b|"
                              rf"\b(?:the|this) {_EDITED} (?:text|paragraph|abstract|introduction|draft|section|"
                              r"version) (?:above|below)\b|\bi(?: have|'ve) made the following\b|"
                              rf"\b(?:the|this) (?:paragraph|section|abstract|introduction|text|sentence) (?:was|has "
                              rf"been) {_EDITED}\b|\bin this (?:version|revision|draft),? i\b|"
                              r"\b(?:key changes|changes made|summary of changes)\b|\bas requested,|"
                              r"\bnote: i(?: have|'ve)? (?:kept|changed|removed|added)\b|\bi(?: have|'ve) kept\b|"
                              r"\brevised version:|\babsolutely! here\b|\bgreat question!|"
                              r"\bthis version (?:tightens|improves|clarifies|shortens|streamlines|emphasi[sz]es)\b|"
                              r"\b(?:revised|updated|rewritten|polished|improved) (?:abstract|version|introduction|"
                              r"paragraph|text|draft|section|conclusion):|"
                              r"\bif you(?:'d| would)? (?:like|want|prefer)\b|"
                              r"\bi (?:don't|do not) have access to\b|\bwant me to\b|\blet me know whether\b|"
                              r"\bbased on my training\b|\bnote to (?:the )?authors?\b|\bthis revision addresses\b|"
                              rf"\bthe following is a {_EDITED}\b",
    MARKDOWN: r"\*\*[^*\n]{1,60}\*\*|(?:^|\s)#{1,4} [a-z]",  # bold and headings; Markdown's own format in a .md
    # XX.X and 9X.4 match without their %, which TeX reads as the start of a comment; X stays a variable name alone
    "placeholder or invented data": r"\[(?:insert|your|author|affiliation|institution|email|name|number|value|date|"
                                    r"add|verify|check|placeholder|tbd)\b[^\]]*\]|<insert[^>]*>|\bfirstname lastname\b|"
                                    r"\[x+\]\s*\\?%|lorem ipsum|(?-i:\b\d+X\b)\s*\\?%|"
                                    r"\bfill (?:it|this|them) in\b|\breplace (?:this|these|with (?:the )?real)\b|"
                                    r"\bdummy (?:data|values?|numbers?)\b|(?-i:\b\d*X[\dX]*\.[\dX]+\b|\bXX+\b)|"
                                    r"\bxx+\s*\\?%",
    "missing citation marker": r"\[(?:citations?|cite|refs?|references?|(?:add|insert|needs?) (?:ref|citation)[^\]]*|"
                               r"ref needed|citation needed)\]|\((?:add )?citation(?: needed)?\)|"
                               r"\binsert (?:citation|reference)\b|"
                               r"<(?:citation|cite|ref|reference)>|\[\?\]|\[\d+\?\]|"
                               r"\(author\?\)|\(\?\)",  # natbib prints the last two for an undefined key
    "unresolved reference": r"\?\?",  # LaTeX prints ?? for an unresolved \ref or \cite
    "todo left in text": r"\b(?:todo|tbd|fixme)\b",
}
REVIEW = {  # legitimate in a paper, and also what an LLM writes over invented numbers: a human reads each
    "ambiguous wording": r"\billustrative\b|\bplaceholders?\b|\bhypothetical\b|\[(?:x|n)\]",  # [n] is also {1..n}
    "assistant-style phrase": rf"\bhere (?:is|are) the\b|\bbelow is the\b|\bfeel free to\b|\bof course,|"
                              rf"\b(?:the|this) {_EDITED} version\b",
    "pending or anonymous": r"\bto be (?:confirmed|determined|added|filled in)\b|\bresults? pending\b|"
                            r"\b(?:please )?verify (?:this|these|the) (?:numbers?|values?|results?)\b|"
                            r"\banonymous authors?\b",
}
# Typeset text and the PDF: a paper rarely speaks in the first person singular, an assistant's reply always does.
FIRST_PERSON = [("REVIEW", "first person singular (an assistant's voice?)",
                 r"(?-i:\bI(?: (?:have|am|made|can|could|would|will|kept|added|removed|changed|tightened|rewrote|"
                 r"revised|shortened|did|don't)|'(?:ve|m|d))\b)")]
# PDF text only: a bracketed phrase there is printed as is, as no LaTeX command would print it.
PDF_ONLY = [("REVIEW", "bracketed phrase in the PDF", r"\[[A-Za-z][A-Za-z .-]{2,40}\]")]


# Typeset .tex only, never the PDF (where natbib's own output looks just like this): a citation typed as text instead
# of \cite never reaches the .aux, the bib check or the claim check.
_NAME, _YEAR = r"[A-Z][\w'\u2019-]*", r"(?:19|20)\d\d[a-z]?"
PLAIN_CITATION = {
    # Name (2023), Chi and Song (2023), Brown et al.'s (2020), Smith et al. [2023]; any (... 2023) group;
    # et al., RSS 2023
    "citation as plain text: never audited": ("BLOCKER", rf"(?-i:{_NAME}"
                                                         rf"(?:\s+(?:and|\\?&)\s+(?:{_NAME}|colleagues|coworkers))?"
                                                         rf"(?:\s+et\s+al\.?)?(?:['\u2019]s)?\\?\s*[(\[]{_YEAR}[)\]]|"
                                                         rf"\([^()]{{0,80}}?[A-Z][^()]{{0,60}}?,?\s+{_YEAR}\)|"
                                                         rf"et\s+al\.?,?\\?\s*[(\[]?(?:[A-Z][\w-]*\s+)?{_YEAR})"),
    "identifier in text, not a \\cite: not looked up": ("REVIEW", r"arXiv[:\s~]*\d{4}\.\d{4,5}(?:v\d+)?|\bdoi:\s*10\.|"
                                                             r"arxiv\.org/(?:abs|pdf|html)/|doi\.org/10\."),
    "numbered citation as plain text": ("REVIEW", r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]"),
}
NUMERIC_ARG = re.compile(r"(?:\\[A-Za-z]+\*?|\})\s*\[\d+\]")  # \newcommand{\x}[1], \todo[1]: arguments, not citations
MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|\\\[.*?\\\]|"
                  r"\\begin\{(equation|align|gather|multline|eqnarray)\*?\}.*?\\end\{\1\*?\}", re.DOTALL)


ET_AL = re.compile(r"\bet\s*~?\s*al\b")
CITE_COMMAND = re.compile(r"\\(?![Cc]iteyear)[A-Za-z]*[Cc]ite")  # \citeyear cites the year, not the names


def et_al_without_cite(text: str, file: str) -> list[dict]:
    """REVIEW: a sentence naming "X et al." with no cite command (or only \\citeyear) in it, a citation the audit
    may never see."""
    return [{"level": "REVIEW", "kind": "et al. without a cite command", "file": file, "where": "typeset text",
             "line": text.count("\n", 0, a) + 1, "match": "et al.", "text": " ".join(text[a:b].split())}
            for a, b in sentence_spans(text) if ET_AL.search(text, a, b) and not CITE_COMMAND.search(text, a, b)]


def complement(text: str, shown: str) -> str:
    """The characters `shown` blanked (comments and hidden blocks), everything else blanked: newlines kept."""
    return "".join(o if o != s else ("\n" if o == "\n" else " ") for o, s in zip(text, shown))


def _edit(chars: list[str], orig: list[int], pattern: re.Pattern, keep: bool) -> tuple[list[str], list[int]]:
    """Each match replaced by its group 1 (keep) or by one space, every kept character keeping its source offset."""
    s, out_c, out_o, last = "".join(chars), [], [], 0
    for m in pattern.finditer(s):
        out_c += chars[last:m.start()]
        out_o += orig[last:m.start()]
        a, b = m.span(1) if keep else (m.start(), m.start())
        out_c += chars[a:b] if keep else [" "]
        out_o += orig[a:b] if keep else [orig[m.start()]]
        last = m.end()
    return out_c + chars[last:], out_o + orig[last:]


def prepare(text: str) -> tuple[str, list[int]]:
    """The text as a reader sees it, whitespace-joined, with each character's offset in the source."""
    chars, orig = list(text), list(range(len(text)))
    for _ in range(UNWRAP_PASSES):
        chars, orig = _edit(chars, orig, WRAPPER, keep=True)
    chars, orig = _edit(chars, orig, SPACE_LIKE, keep=False)
    chars, orig = _edit(chars, orig, re.compile(r"\s+"), keep=False)
    return "".join(chars), orig


def scan(text: str, file: str, where: str, rules: list | None = None) -> list[dict]:
    """Hits of rules, (level, kind, pattern) triples: by default the BLOCKER and REVIEW tables."""
    joined, orig = prepare(text)
    hits = []
    if rules is None:
        rules = [(lv, k, p) for lv, t in (("BLOCKER", BLOCKER), ("REVIEW", REVIEW)) for k, p in t.items()]
        rules += (PDF_ONLY if where == "pdf text" else []) + (FIRST_PERSON if where in ("typeset text", "pdf text")
                                                             else [])
    for level, kind, pat in rules:
        for m in re.finditer(pat, joined, re.IGNORECASE):
            at = orig[m.start()]
            hit = {"level": level, "kind": kind, "file": file, "where": where, "line": text.count("\n", 0, at) + 1,
                   "match": m.group(), "text": joined[max(0, m.start() - EXCERPT_CHARS):m.end() + EXCERPT_CHARS]}
            if where == "pdf text":
                hit["page"] = text.count("\f", 0, at) + 1
            hits.append(hit)
    return hits


def scan_file(path: Path) -> list[dict]:
    if path.suffix == ".pdf":
        text = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout
        return scan(text, path.name, "pdf text")
    text = path.read_text(errors="replace")
    plain_rules = [(lv, k, p) for lv, t in (("BLOCKER", BLOCKER), ("REVIEW", REVIEW)) for k, p in t.items()
                   if k != MARKDOWN]
    if path.suffix == ".bib":  # note, howpublished and (in some styles) abstract fields print
        return scan(text, path.name, "bib file (printed fields)", plain_rules)
    if path.suffix not in TEX_LIKE:  # .md, .txt: Markdown is their format, not a tell
        return scan(text, path.name, "text", plain_rules)
    shown = visible(text)
    hits = scan(shown, path.name, "typeset text")
    if path.suffix == ".tex":
        typeset = blank(blank(body(shown), MATH), NUMERIC_ARG)
        hits += scan(typeset, path.name, "typeset text", [(lv, k, p) for k, (lv, p) in PLAIN_CITATION.items()])
        hits += et_al_without_cite(typeset, path.name)
    return hits + scan(complement(text, shown), path.name, "comment or hidden block (public in the arXiv source)")


def scan_paths(paths: list[Path]) -> list[dict]:
    """A directory stands for every text file under it (TEXT_SUFFIXES); a file is scanned as given; and every file a
    TeX-like file \\inputs is scanned too, whatever its suffix."""
    files = [f.resolve() for p in paths
             for f in (sorted(x for x in p.rglob("*") if x.suffix in TEXT_SUFFIXES) if p.is_dir() else [p])]
    for f in files:  # grows while it runs: an \input file can \input another
        if f.suffix in TEX_LIKE:
            for m in INPUT.finditer(visible(f.read_text(errors="replace"))):
                if (found := resolve_input(input_name(m), [f.parent, files[0].parent])) and found not in files:
                    files.append(found)
    return [h for f in files for h in scan_file(f)]


def shipped(bundle: Path, out_dir: Path) -> Path:
    """The submission as a directory: the directory itself, or a tarball unpacked under OUT_DIR/bundle."""
    if bundle.is_dir():
        return bundle
    dest = out_dir / "bundle"
    with tarfile.open(bundle) as tar:
        tar.extractall(dest, filter="data")  # the data filter refuses absolute paths, links out and devices
    return dest


def main() -> None:
    out_dir, bibs, others, opts = split_args(sys.argv[1:])
    paths = bibs + others
    if not paths:
        raise SystemExit("usage: llm_tells.py OUT_DIR PAPER_DIR paper.pdf [more files ...] [--bundle DIR|TARBALL]")
    target = fresh_output(out_dir / "tells.json")
    if (bundle := opts.get("bundle")):  # exactly the files that ship to arXiv, not notes beside the source
        paths = [shipped(Path(bundle[-1]), out_dir)] + [p for p in paths if p.suffix == ".pdf"]
    if not any(p.suffix == ".pdf" for p in paths):
        print("WARNING: no PDF given: tells that macros or \\input files produce are only seen in the built PDF")
    hits = scan_paths(paths)
    target.write_text(json.dumps(hits, indent=1, ensure_ascii=False))
    for h in hits:
        loc = f"{h['file']}:{h['line']}" + (f" (page {h['page']})" if "page" in h else "")
        print(f"{h['level']:8} {h['kind']:30} {loc}  [{h['where']}]  ...{h['text']}...")
    print(f"{sum(h['level'] == 'BLOCKER' for h in hits)} BLOCKER, {sum(h['level'] == 'REVIEW' for h in hits)} REVIEW")


if __name__ == "__main__":
    main()

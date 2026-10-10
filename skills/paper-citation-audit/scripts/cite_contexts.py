# ABOUTME: Lists every citing sentence (line, neighbours) per key in a paper's .tex files, keys missing from the bib and
# ABOUTME: never cited, checked against the build's .aux. Usage: cite_contexts.py OUT_DIR refs.bib main.tex main.aux
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts, also under python -I
from bib_check import brace_end, fresh_output, load_bibs, split_args

# Commands whose brace argument is a key list (natbib, biblatex, plain LaTeX); a "...cites" form takes several lists.
KEY_COMMANDS = {"cite", "citet", "citep", "citealt", "citealp", "citeauthor", "citeyear", "citeyearpar", "citenum",
                "citetitle", "citeurl", "citedate", "Citet", "Citep", "Citealt", "Citealp", "Citeauthor", "parencite",
                "Parencite", "textcite", "Textcite", "autocite", "Autocite", "footcite", "footcitetext", "smartcite",
                "Smartcite", "supercite", "fullcite", "cites", "Cites", "parencites", "Parencites", "textcites",
                "Textcites", "autocites", "Autocites", "footcites", "smartcites"}
TEXTCITE = {"textcite", "Textcite", "textcites", "Textcites"}
# biblatex (backend=bibtex, standard styles, measured with tectonic) writes two \abx@aux@cite lines per key for these
TEXTCITE_AUX_WRITES = 2
NON_CITING = {"nocite", "citetext", "citestyle", "citeindextrue", "citeindexfalse"}  # no key, or no claim
CITE_LIKE = r"[A-Za-z]*[Cc]ite[A-Za-z]*"  # \Citet, \parencite, \citeyearpar ...
DEFINITION = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand|newrobustcmd|renewrobustcmd|"
                        r"providerobustcmd|(?:New|Renew|Provide|Declare)DocumentCommand|[gex]?def)\*?"
                        r"\s*\{?\s*\\([A-Za-z]+)")
PREAMBLE_TEXT = re.compile(r"\\(?:title|author|thanks)\s*(?:\[[^\]]*\])?\s*\{")  # typeset by \maketitle
INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}|\\input\s+([^\s{}%\\]+)|"
                   r"\\(?:sub)?(?:import|inputfrom|includefrom)\*?\s*\{([^}]*)\}\s*\{([^}]+)\}")
AUX_CITE = re.compile(r"\\citation\{([^}]*)\}|\\abx@aux@cite(?:\{\d+\})?\{([^}]*)\}")
DEF_ARGS = re.compile(r"\s*\}?\s*(?:\[[^\]]*\]\s*)*(?:#\d\s*)*")  # after \newcommand{\x: }, [n], [default], #1#2
AUX_LISTS = (".toc", ".lof", ".lot")  # a caption or heading cite is typeset again when its list is printed
LIST_CITE = re.compile(r"\\[A-Za-z]*[Cc]ite[A-Za-z]*\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]*)\}")
TITLE_START = re.compile(r"\\(?:captionof\s*\{[^}]*\}|caption|chapter|(?:sub)*section|(?:sub)?paragraph)\*?\s*"
                         r"(?:\[[^\]]*\])?\s*\{")
# A table float (table, table*, sidewaystable...), or a minipage or center with \captionof{table}: whatever cites in it
# vouches for the whole float, whose numbers LaTeX sets apart from the citing sentence.
TABLE_FLOAT = re.compile(r"\\begin\{([A-Za-z]*(?:table|figure)[A-Za-z]*\*?)\}.*?\\end\{\1\}|"
                         r"\\begin\{(minipage|center)\}(?:(?!\\end\{\2\}).)*?\\captionof\{table\}.*?\\end\{\2\}",
                         re.DOTALL)
LABEL = re.compile(r"\\label\{([^}]+)\}")
ARGUMENT = re.compile(r"[{\[]([^{}\[\]]+)[}\]]")  # \ref{x}, \tabref{x}, \hyperref[x]: any argument may name a label
TABULAR_IN = re.compile(r"\\begin\{(?:tab|tblr|NiceTabular|supertabular|longtable)")  # a figure holding a table
UNREADABLE = re.compile(r"\\(?:pgfplotstabletypeset|csvautotabular|csvreader|includegraphics|DTLdisplaydb)\b")
TABLE_UNREADABLE = "unreadable, check the PDF:"  # quote_check sends a claim on such a table to review
AUX_INPUT = re.compile(r"\\@input\{([^}]+)\}")
URL_ARG = re.compile(r"\\(?:url|href)\s*\{[^}]*\}")  # % is literal inside a URL, not a comment
OPT_ARG = re.compile(r"\s*(?:\[[^\]]*\]|\([^)]*\))")  # [pre][post], and biblatex multicite (pre)(post)
KEY_ARG = re.compile(r"\s*\{([^}]*)\}")
SENTENCE_END = re.compile(r"[.!?]\s+(?=[A-Z\\])")
NEXT_IS_CITE = re.compile(r"\s*\\[A-Za-z]*[Cc]ite")
ABBREV_BEFORE = re.compile(r"(?:\bet al|\be\.g|\bi\.e|\bcf|\bfigs?|\beqs?|\bvs|\bsecs?|\btabs?|\bresp|\bapprox|"
                           r"\bapp|\balg|\bthm|\blem|\bprop|\bdef|\bcor)$",
                           re.IGNORECASE)
HIDDEN = re.compile(r"\\iffalse\b.*?\\fi\b|\\begin\{comment\}.*?\\end\{comment\}", re.DOTALL)
COMMENT = re.compile(r"(?<!\\)%.*")
PARAGRAPH = re.compile(r"(?:[^\n]|\n(?!\s*\n))+")  # a run of lines with no blank line inside


def blank_span(text: str, a: int, b: int) -> str:
    """text[a:b] replaced by spaces, newlines kept, so offsets and line numbers stay valid."""
    return text[:a] + re.sub(r"[^\n]", " ", text[a:b]) + text[b:]


def blank(text: str, pattern: re.Pattern) -> str:
    for m in reversed(list(pattern.finditer(text))):
        text = blank_span(text, m.start(), m.end())
    return text


def input_name(m: re.Match) -> str:
    """The file an INPUT match names: \\input{x}, \\input x, or \\import{dir/}{x} as dir/x."""
    return m.group(1) or m.group(2) or str(Path(m.group(3)) / m.group(4))


def visible(tex: str) -> str:
    """The text LaTeX typesets: comments, \\iffalse...\\fi and comment environments blanked."""
    shielded = URL_ARG.sub(lambda m: m.group().replace("%", "\0"), tex)  # same length: offsets survive
    return blank(blank(shielded, COMMENT), HIDDEN).replace("\0", "%")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentences as (start, end) offsets; never split before a \\cite or after an abbreviation such as 'et al.'."""
    spans = []
    for pm in PARAGRAPH.finditer(text):
        start = pm.start()
        for m in SENTENCE_END.finditer(text, pm.start(), pm.end()):
            if NEXT_IS_CITE.match(text, m.start() + 1) or ABBREV_BEFORE.search(text, pm.start(), m.start()):
                continue
            spans.append((start, m.start() + 1))
            start = m.end()
        spans.append((start, pm.end()))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def cite_matcher(names: set[str]) -> re.Pattern:
    """Any \\...cite... command, plus the commands named with --also-cite or --cite-macro whatever their name."""
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"\\({alts + '|' if alts else ''}{CITE_LIKE})(?![A-Za-z])\*?")


def cites(text: str, a: int, b: int, matcher: re.Pattern) -> list[tuple[str, list[str], int]]:
    """(command, keys, offset) for each cite-like command in text[a:b]."""
    out = []
    for m in matcher.finditer(text, a, b):
        cmd, j, keys = m.group(1), m.end(), []
        while (o := OPT_ARG.match(text, j)):
            j = o.end()
        while (k := KEY_ARG.match(text, j)):
            keys += [x.strip() for x in k.group(1).split(",")]
            j = k.end()
            while (o := OPT_ARG.match(text, j)):
                j = o.end()
            if not cmd.endswith("s"):
                break
        out.append((cmd, [x for x in keys if x], m.start()))
    return out


def resolve_input(name: str, roots: list[Path]) -> Path | None:
    for root in roots:
        p = root / name.strip()
        for c in ([p] if p.suffix == ".tex" else [p.with_name(p.name + ".tex"), p]):
            if c.is_file():
                return c.resolve()
    return None


def _close(text: str, i: int) -> int:
    return brace_end(text, i) or len(text)


def definitions(shown: str, where: str, extra: set[str], macros: dict, matcher: re.Pattern) -> str:
    """The text with every \\newcommand / \\def (head and balanced body) blanked, in the preamble and the body alike.
    A cite with fixed keys in a body needs --cite-macro for that macro, one with #1 needs --also-cite: their uses
    cite keys the scan would otherwise not see."""
    spans = []
    for m in DEFINITION.finditer(shown):
        name, j = m.group(1), DEF_ARGS.match(shown, m.end()).end()
        if "DocumentCommand" in m.group() and shown[j:j + 1] == "{":
            j = DEF_ARGS.match(shown, _close(shown, j)).end()  # the argument specification
        end = _close(shown, j) if shown[j:j + 1] == "{" else j
        line = shown.count("\n", 0, m.start()) + 1
        for cmd, keys, _ in cites(shown, j, end, matcher):
            if any(re.fullmatch(r"#+\d", k) for k in keys) and name not in extra:
                raise SystemExit(f"refusing: {where}:{line}: \\{name} passes its argument to \\{cmd}; its uses cite "
                                 f"keys the scan cannot see. Read it and pass --also-cite={name}")
            fixed = [k for k in keys if not k.startswith("#")]
            if fixed and cmd not in NON_CITING - {"nocite"} and name not in macros:
                raise SystemExit(f"refusing: {where}:{line}: \\{name} cites {', '.join(fixed)} in its definition. "
                                 f"Pass --cite-macro={name}={','.join(fixed)} so each use counts as citing them")
        spans.append((m.start(), end))
    for a, b in reversed(spans):
        shown = blank_span(shown, a, b)
    return shown


def body(text: str) -> str:
    """The text with its preamble (before \\begin{document}) blanked, since it only loads and defines; except the
    \\title, \\author and \\thanks arguments, which \\maketitle typesets."""
    start, kept = text.find("\\begin{document}"), 0
    if start <= 0:
        return text
    out = ""
    for m in PREAMBLE_TEXT.finditer(text, 0, start):
        if m.start() >= kept:
            end = _close(text, m.end() - 1)
            out += re.sub(r"[^\n]", " ", text[kept:m.end()]) + text[m.end():end]
            kept = end
    return out + re.sub(r"[^\n]", " ", text[kept:start]) + text[start:]


def title_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every caption and heading argument, braces balanced."""
    return [(m.end(), _close(text, m.end() - 1)) for m in TITLE_START.finditer(text)]


def table_floats(text: str, roots: list[Path], inlined: dict, labels: dict) -> list[tuple[int, int, str]]:
    """(start, end, float text) of every table float, its \\input files inlined (and noted in inlined, so their own
    cites get the float too); the text is the TABLE_UNREADABLE marker when it holds what the scan cannot read."""
    out = []
    for f in TABLE_FLOAT.finditer(text):
        whole, files = f.group(), []
        for m in INPUT.finditer(f.group()):
            if (p := resolve_input(input_name(m), roots)):
                files.append(p)
                whole = whole.replace(m.group(), visible(p.read_text(errors="replace")), 1)
        if "figure" in (f.group(1) or "") and not TABULAR_IN.search(whole):
            continue  # a figure is a table float only when it holds a tabular
        found = LABEL.findall(whole)  # after inlining: the label may sit in the \input file
        whole = f"{TABLE_UNREADABLE} {bad.group()}" if (bad := UNREADABLE.search(whole)) else " ".join(whole.split())
        inlined.update({p: whole for p in files})
        labels.update({lb: whole for lb in found})
        out.append((f.start(), f.end(), whole))
    return out


def collect(texs: list[Path], extra: set[str], macros: dict[str, list[str]] | None = None):
    """(contexts per key, unknown commands, scan) over the given files and every file they pull in (\\input,
    \\include, \\import and kin, read in and said so). A
    --cite-macro use cites its fixed keys."""
    macros = macros or {}
    contexts: dict[str, list[dict]] = {}
    seen: dict[tuple[str, str], dict] = {}
    unknown, calls, textcite, titled, where, inlined, preamble = [], Counter(), Counter(), {}, {}, {}, set()
    labels: dict[str, str] = {}
    matcher = cite_matcher(extra | set(macros))
    queue, done = list(texs), {t.resolve() for t in texs}
    while queue:
        tex = queue.pop(0)
        defined = definitions(visible(tex.read_text(errors="replace")), str(tex), extra, macros, matcher)
        preamble |= {p for m in INPUT.finditer(defined, 0, max(0, defined.find("\\begin{document}")))
                     if (p := resolve_input(input_name(m), [tex.parent, texs[0].parent]))}
        text = body(defined)
        for m in INPUT.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            found = resolve_input(input_name(m), [tex.parent, texs[0].parent])
            if found is None:
                raise SystemExit(f"refusing: {tex}:{line}: {m.group().strip()} resolves to no file; its text would go "
                                 "unaudited")
            if found not in done:
                done.add(found)
                queue.append(found)
                print(f"read in {found} ({m.group().strip()} at {tex.name}:{line})")
        titles, tables = title_spans(text), table_floats(text, [tex.parent, texs[0].parent], inlined, labels)
        spans = sentence_spans(text)
        flat = [" ".join(text[a:b].split()) for a, b in spans]
        for n, (a, b) in enumerate(spans):
            for cmd, keys, at in cites(text, a, b, matcher):
                line = text.count("\n", 0, at) + 1
                keys = macros.get(cmd, keys)
                if cmd in NON_CITING:
                    calls.update(k for k in keys if k != "*" and cmd == "nocite")
                    continue
                if cmd not in KEY_COMMANDS and cmd not in extra and cmd not in macros:
                    unknown.append(f"{tex}:{line}: \\{cmd}")
                    continue
                if not keys:
                    raise SystemExit(f"refusing: {tex}:{line}: \\{cmd} parses to zero keys")
                calls.update(keys)
                textcite.update(keys if cmd in TEXTCITE else [])
                for k in keys:
                    where.setdefault(k, []).append(f"{tex.name}:{line}")
                if any(a <= at < b for a, b in titles):
                    for k in keys:
                        titled.setdefault(k, []).append(f"{tex.name}:{line}")
                occ = {"file": tex.name, "line": line, "prev": flat[n - 1] if n else "",
                       "next": flat[n + 1] if n + 1 < len(flat) else ""}
                for c0, c1, t in tables:
                    if c0 <= at < c1:
                        occ["table"] = t
                if tex.resolve() in inlined:  # this whole file sits inside a table float
                    occ["table"] = inlined[tex.resolve()]
                for k in keys:  # a sentence repeated in the paper: one row, every place it stands
                    if (k, flat[n]) not in seen:
                        seen[(k, flat[n])] = {"sentence": flat[n], **occ, "occurrences": []}
                        contexts.setdefault(k, []).append(seen[(k, flat[n])])
                    seen[(k, flat[n])]["occurrences"].append(dict(occ))
    for row in seen.values():  # a table whose label the sentence or a neighbour names, wherever its float sits
        for occ in row["occurrences"]:
            named = [labels[lb.strip()] for t in (row["sentence"], occ["prev"], occ["next"])
                     for m in ARGUMENT.finditer(t) for lb in m.group(1).split(",") if lb.strip() in labels]
            if named:
                occ["table"] = " ".join(dict.fromkeys([occ.get("table", ""), *named])).strip()
        row.update({"table": row["occurrences"][0]["table"]} if "table" in row["occurrences"][0] else {})
    scan = {"calls": calls, "textcite": textcite, "read": done, "titled": titled, "where": where,
            "preamble": preamble}
    return contexts, unknown, scan


def aux_calls(aux: Path, seen: set[Path] | None = None) -> tuple[Counter, Counter]:
    """(\\citation keys, \\abx@aux@cite keys) per key as LaTeX expanded them, \\@input aux files included."""
    seen = seen if seen is not None else set()
    seen.add(aux.resolve())
    text = aux.read_text(errors="replace")
    cit, abx = Counter(), Counter()
    for m in AUX_CITE.finditer(text):
        (cit if m.group(1) is not None else abx).update(
            k.strip() for k in (m.group(1) or m.group(2)).split(",") if k.strip() not in ("", "*", "biblatex-control"))
    for m in AUX_INPUT.finditer(text):
        sub = aux.parent / m.group(1)
        if not sub.is_file():
            raise SystemExit(f"refusing: {aux} \\@input's {sub}, which does not exist: rebuild the paper")
        if sub.resolve() not in seen:
            c, b = aux_calls(sub, seen)
            cit, abx = cit + c, abx + b
    return cit, abx


def aux_guard(aux: Path, scan: dict) -> tuple[list[str], list[dict]]:
    """Compares the source scan's citation calls per key with LaTeX's own (.aux), in both directions. More in the
    .aux: hidden citations, refused; except calls the .toc/.lof/.lot print again (a note) and, failing that, a
    caption or heading cite measured twice (a CAPTION_SURPLUS finding). More in the scan: a cite LaTeX never
    expanded, refused. Returns (notes, findings)."""
    if (newer := [str(p) for p in scan["read"] if p.stat().st_mtime > aux.stat().st_mtime]):
        raise SystemExit(f"refusing: {aux} is older than {', '.join(sorted(newer))}: rebuild the paper first")
    cit, abx = aux_calls(aux)
    expanded = abx or cit  # biblatex writes both; its own lines are the ones written per citation
    scanned = scan["calls"] + Counter({k: n * (TEXTCITE_AUX_WRITES - 1) for k, n in scan["textcite"].items()}
                                      if abx else {})
    lists = {p.name: Counter(k.strip() for m in LIST_CITE.finditer(p.read_text(errors="replace"))
                             for k in m.group(1).split(","))
             for p in (aux.with_suffix(e) for e in AUX_LISTS) if p.is_file()}
    hidden, unexpanded, notes, findings = [], [], [], []
    for k in sorted(set(expanded) | set(scanned)):
        a, n = expanded[k], scanned[k]
        line = f"{k}: aux {a}, source scan {n}"
        if n > a:
            unexpanded.append(line)
            continue
        listed = min(a - n, sum(c[k] for c in lists.values()))
        if listed:
            notes.append(f"{line}: {listed} printed again by {', '.join(f for f, c in lists.items() if c[k])}")
        rest = a - n - listed
        if rest and not lists and rest == len(scan["titled"].get(k, [])):  # a caption measured, then set
            notes.append(f"CAPTION_TWICE: {k}: aux {a}, source scan {n}: one extra call per caption or heading cite "
                         f"at {', '.join(scan['titled'][k])}, with no .toc/.lof/.lot in the build (MINOR)")
        elif rest and rest <= len(scan["titled"].get(k, [])):
            findings.append({"label": "CAPTION_SURPLUS", "key": k, "detail": (
                f"{line}: {rest} expanded calls no .toc/.lof/.lot explains; the key is cited in a caption or heading "
                f"at {', '.join(scan['titled'][k])} (a caption measured twice is the usual cause); every source line "
                f"of the key: {', '.join(scan['where'].get(k, []))}. Confirm in the PDF that nothing else cites it")})
        elif rest:
            hidden.append(line)
    problems = []
    if hidden:
        problems.append("the .aux holds more citation calls than the source scan found, so a macro, an alias (\\let), "
                        "an \\import, an unknown command, or a cite in the preamble outside \\title, \\author and "
                        "\\thanks hides citations. Find each, then pass --also-cite=NAME (a command taking keys) or "
                        "--cite-macro=NAME=key1,key2 (a macro citing fixed keys):\n  "
                        + "\n  ".join(hidden))
    if unexpanded:
        problems.append("the scan counted a cite LaTeX never expanded (a false conditional, \\verb, an excluded "
                        "environment...): find each and make the source and the build agree:\n  "
                        + "\n  ".join(unexpanded))
    if problems:
        raise SystemExit("refusing: " + "\nrefusing: ".join(problems))
    return notes, findings


AUX_HELP = ("cite_contexts needs the build's main .aux (exactly one): build the paper first, e.g. `tectonic -X compile "
            "--keep-intermediates main.tex` or `latexmk -pdf main.tex`; then pass main.aux beside the .tex files")


def main() -> None:
    out_dir, bibs, others, opts = split_args(sys.argv[1:])
    texs, auxes = [p for p in others if p.suffix != ".aux"], [p for p in others if p.suffix == ".aux"]
    if not bibs or not texs:
        raise SystemExit("usage: cite_contexts.py OUT_DIR refs.bib [more.bib] main.tex [b.tex ...] main.aux "
                         "[--also-cite=cmd,cmd] [--cite-macro=NAME=key1,key2]")
    target = fresh_output(out_dir / "contexts.json")
    if len(auxes) != 1:
        raise SystemExit(f"refusing: {AUX_HELP}")
    if not auxes[0].is_file():
        raise SystemExit(f"refusing: {auxes[0]} does not exist. {AUX_HELP}")
    extra = {n for v in opts.get("also-cite", []) for n in v.split(",") if n}
    macros = {name: keys.split(",") for v in opts.get("cite-macro", []) for name, _, keys in [v.partition("=")]}
    keys = {e["key"] for e in load_bibs(bibs)}
    contexts, unknown, scan = collect(texs, extra, macros)
    if unknown:
        raise SystemExit("refusing: unrecognised cite-like commands (their keys would go unaudited); read each, and "
                         "pass --also-cite=NAME if it cites keys:\n  " + "\n  ".join(unknown))
    notes, findings = aux_guard(auxes[0], scan)
    for n in notes:
        print(f"NOTE {n}")
    for f in findings:
        print(f"{f['label']} {f['detail']}")
    unread = sorted(str(p) for p in texs[0].parent.rglob("*.tex")
                    if p.resolve() not in scan["read"] | scan["preamble"])
    report = {"contexts": contexts, "cited_not_in_bib": sorted(set(scan["calls"]) - keys),
              "in_bib_not_cited": sorted(keys - set(scan["calls"])), "tex_not_read": unread,
              "preamble_inputs": sorted(str(p) for p in scan["preamble"]),  # macro definitions, checked in the PDF
              "notes": notes, "findings": findings}
    target.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"{len(contexts)} keys cited in {sum(map(len, contexts.values()))} (key, sentence) pairs; "
          f"cited but missing from the bib: {report['cited_not_in_bib'] or 'none'}; "
          f"in the bib but never cited: {report['in_bib_not_cited'] or 'none'}; "
          f".tex files under {texs[0].parent} never read in: {unread or 'none'}")


if __name__ == "__main__":
    main()

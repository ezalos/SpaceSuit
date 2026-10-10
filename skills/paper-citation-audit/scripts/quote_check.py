# ABOUTME: Checks each verdict's quote and numbers against its source's fetched text, and marks each citing sentence of
# ABOUTME: contexts.json with no verdict UNCHECKED; writes verdicts-checked.json. Usage: quote_check.py OUT_DIR
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts, also under python -I
from bib_check import MIN_PAPER_CHARS, fresh_output, split_args, text_path
from cite_contexts import TABLE_UNREADABLE

MIN_QUOTE_CHARS = 20  # a shorter quote can match by accident and proves nothing
VERDICTS = {"SUPPORTED", "PARTIAL", "NOT_SUPPORTED", "CONTRADICTED", "NO_TEXT"}
MINUS_LIKE = "−‒–‐‑"  # PDF text prints a minus sign as any of these
# A number keeps its sign when the '-' follows no letter or digit; "10-20" is a range, "LIBERO-90" a name.
TOKEN = re.compile(r"(?<![a-z0-9.])-?\d+(?:\.\d+)?|[a-z0-9]+")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# LaTeX whose argument holds no claim: a label like tab:3 or a key like liu2023 is not a number of the sentence
LATEX_REF = re.compile(r"\\(?:[A-Za-z]*[Cc]ite[A-Za-z]*|[cC]?ref|eqref|autoref|label|url|href)\*?"
                       r"(?:\[[^\]]*\])*\{[^}]*\}")
STOPWORDS = set("""a an the and or but of in on at to for from by with as is are was were be been being this that these
those it its we our they their which who whom what when where how than then also can may might will would should could
not no such into over under between both each other more most less very only same using use used based show shows
shown propose proposes proposed paper work method model approach results result""".split())
# LaTeX commands whose output carries no claim of its own; any other command in a sentence is a user macro whose value
# (a number, a method name) the checker cannot see.
KNOWN_COMMANDS = set("""emph textbf textit texttt textsc textrm textsf textup mbox mathrm mathbf mathcal mathbb
ensuremath footnote ref cref Cref eqref autoref pageref label url href ldots dots cdots cdot times pm approx leq geq le
ge sim frac sqrt hspace vspace noindent xspace S etal ie eg item newline linebreak begin end section subsection
subsubsection paragraph caption captionof centering small footnotesize scriptsize tiny normalsize large Large
textsuperscript textsubscript mathit mathsf mathtt boldsymbol bm text quad qquad hfill vfill smallskip medskip bigskip
par cite citep citet nameref Autoref ensuremath percent textasciitilde textbackslash""".split())
HEDGED = re.compile(r"\b(?:suggests?|suggested|may|might|could|preliminary)\b")
STRONG = re.compile(r"\b(?:shows?|showed|shown|proves?|proved|proven|demonstrates?|demonstrated|always|"
                    r"establish(?:es|ed)?)\b")
NEGATIONS = [("not",), ("no",), ("never",), ("nor",), ("without",), ("fails", "to"), ("fail", "to"), ("failed", "to")]
PRONOUNS = {"it", "its", "they", "their", "them", "this", "these", "those", "such", "above", "both", "former",
            "latter", "aforementioned", "previous", "preceding", "same",
            "see", "cf", "source", "from"}  # refers to a neighbour sentence ("See \citet{x}", "Source: ...")
PRONOUN_WINDOW = 4  # "These numbers are reported by ...": the subject comes within the first few words
CITE_IN = re.compile(r"\\[A-Za-z]*[Cc]ite")
QUOTED_VERDICTS = {"SUPPORTED", "PARTIAL", "CONTRADICTED"}  # each rests on a quote, which must be in the text
CONTENT_MIN_CHARS = 3  # shorter words carry no claim of their own
STEM_CHARS = 5  # "evaluated" and "evaluation" share their first 5 letters; a crude stem, no dictionary needed


TEX_MINUS = re.compile(r"(?:\$-\$|\\\(-\\\)|\{-\}|\\textminus\b\s*|\$\\minus\$)(?=\s*\d)")  # $-$84.7 is -84.7


def tokens(s: str) -> list[str]:
    s = TEX_MINUS.sub("-", s)
    s = unicodedata.normalize("NFKC", s).replace("­", "")
    s = re.sub(f"[{MINUS_LIKE}]", "-", s)
    s = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", s)  # words hyphenated across a PDF line break
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)  # 10,030 is one number
    return TOKEN.findall(s.lower())


def squash(s: str) -> str:
    """Space-padded token string: a quote matches on word boundaries only."""
    return " " + " ".join(tokens(s)) + " "


def _numbers(toks: list[str]) -> list[str]:
    return [n for n in toks if NUMBER.fullmatch(n)]


def check_quote(v: dict, out_dir: Path, row: dict | None = None) -> None:
    """Quote found, then the claim checks; row is the contexts.json row (its next sentence and caption table)."""
    row = row or {}
    path = text_path(out_dir, str(v.get("key", "")))
    q, before = squash(str(v.get("quote") or "")), []
    if not path.exists():
        v["quote_check"] = "no fetched text"
    elif len(q.strip()) < MIN_QUOTE_CHARS:
        v["quote_check"] = "quote too short to prove anything"
    else:
        source = squash(path.read_text(errors="replace"))
        v["quote_check"] = "found" if q in source else "NOT FOUND in the source text"
        before = source[:max(0, source.find(q))].split()[-len(max(NEGATIONS, key=len)):] if q in source else []
    if v["verdict"] in QUOTED_VERDICTS and v["quote_check"] != "found":
        v["verdict"] = "QUOTE_NOT_FOUND"
    if v["verdict"] not in ("SUPPORTED", "PARTIAL"):
        return
    quoted = tokens(str(v.get("quote") or ""))
    claim = tokens(str(v.get("claim") or v.get("sentence") or ""))
    sentence = tokens(LATEX_REF.sub(" ", str(v.get("sentence") or "")))
    problems = []
    if (missing := [n for n in claim if NUMBER.fullmatch(n) and n not in quoted]):
        problems.append(f"numbers of the claim absent from the quote (sign included): {missing}")
    backward = bool(PRONOUNS & set(sentence[:PRONOUN_WINDOW]))  # "These numbers are reported by ..."
    neighbours = {"sentence": sentence}
    for occ in row.get("occurrences") or [row]:  # a sentence repeated in the paper: every place it stands
        prev, nxt, table = (str(occ.get(k) or "") for k in ("prev", "next", "table"))
        if table.startswith(TABLE_UNREADABLE):
            problems.append(f"the citing table is {table}")
        for what, toks in (("previous sentence (the citing one refers back to it)",
                            tokens(LATEX_REF.sub(" ", prev)) if backward and not CITE_IN.search(prev) else []),
                           ("next sentence (no cite of its own: it may continue the attribution)",
                            [] if CITE_IN.search(nxt) else tokens(LATEX_REF.sub(" ", nxt))),
                           ("table around or named by the cite",
                            [] if table.startswith(TABLE_UNREADABLE) else tokens(table))):
            neighbours[what] = neighbours.get(what, []) + toks
    for what, toks in neighbours.items():
        if (unclaimed := [n for n in _numbers(toks) if n not in quoted and n not in claim]):
            problems.append(f"numbers of the {what} in neither the claim nor the quote: {sorted(set(unclaimed))}")
    if any(tuple(before[-len(neg):]) == neg for neg in NEGATIONS):
        problems.append(f"a negation right before the quote in the source: {' '.join(before)!r}")
    if v["verdict"] == "SUPPORTED":
        if HEDGED.search(" ".join(quoted)) and STRONG.search(" ".join(sentence)):
            problems.append("a hedged quote (suggest, may, preliminary...) behind a strong sentence (shows, proves, "
                            "always...): that is PARTIAL at best")
        macros = sorted({c for c in re.findall(r"\\([A-Za-z]+)", LATEX_REF.sub(" ", str(v.get("sentence") or "")))
                         if c not in KNOWN_COMMANDS})
        if macros:
            problems.append(f"the sentence holds user macros {macros}: their values are only in the built PDF")
        content = {w[:STEM_CHARS] for w in claim if w.isalpha() and len(w) >= CONTENT_MIN_CHARS and w not in STOPWORDS}
        if content and not content & {w[:STEM_CHARS] for w in quoted}:
            problems.append("the quote shares no content word with the claim")
    if problems:
        v["verdict"] = "NEEDS_REVIEW"
        v["quote_check"] += "; " + "; ".join(problems)


def check_no_text(v: dict, out_dir: Path, fetched: dict) -> None:
    """NO_TEXT stands only when no text was fetched or fetched.json calls it a landing page; a long non-PDF text may
    be the paper itself (an HTML version), so NO_TEXT on it is UNVERIFIED."""
    got = fetched.get(v.get("key"), {})
    if text_path(out_dir, str(v.get("key", ""))).exists() and got.get("landing") is not True:
        v["verdict_given"], v["verdict"] = "NO_TEXT", "INVALID_VERDICT"
        v["note"] = (str(v.get("note") or "") + " [NO_TEXT given, but the paper's full text was fetched]").strip()
    elif got.get("pdf") is False and got.get("chars", 0) >= MIN_PAPER_CHARS:
        v["verdict_given"], v["verdict"] = "NO_TEXT", "UNVERIFIED"
        v["note"] = (str(v.get("note") or "") + " [NO_TEXT on a long non-PDF text, which may be the paper]").strip()


def load_verdicts(out_dir: Path) -> list[dict]:
    rows = []
    for f in sorted(out_dir.glob("verdicts-*.jsonl")):
        for n, ln in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if not ln.strip():
                continue
            try:
                v = json.loads(ln)
                if not isinstance(v, dict):
                    raise TypeError("not an object")
            except (ValueError, TypeError) as e:
                rows.append({"from": f"{f.name}:{n}", "verdict": "INVALID_VERDICT", "note": f"unparseable: {e}"})
                continue
            given = str(v.get("verdict", "")).strip()
            v["from"] = f"{f.name}:{n}"
            v["verdict"] = given.upper() if given.upper() in VERDICTS else "INVALID_VERDICT"
            if v["verdict"] != given:
                v["verdict_given"] = given
            rows.append(v)
    return rows


def main() -> None:
    out_dir, bibs, others, _ = split_args(sys.argv[1:])
    if bibs or others:
        raise SystemExit("usage: quote_check.py OUT_DIR")
    target = fresh_output(out_dir / "verdicts-checked.json")
    if not (out_dir / "contexts.json").exists():
        raise SystemExit(f"refusing: no {out_dir / 'contexts.json'}; run cite_contexts.py first")
    contexts = json.loads((out_dir / "contexts.json").read_text())["contexts"]
    fetched_path = out_dir / "fetched.json"
    fetched = json.loads(fetched_path.read_text()) if fetched_path.exists() else {}
    verdicts = load_verdicts(out_dir)
    pairs = {(k, row["sentence"]): row for k, rows in contexts.items() for row in rows}
    covered, unmatched = set(), 0
    for v in verdicts:
        if v["verdict"] == "NO_TEXT":
            check_no_text(v, out_dir, fetched)
        pair = (str(v.get("key", "")), " ".join(str(v.get("sentence", "")).split()))
        if v["verdict"] != "INVALID_VERDICT":
            check_quote(v, out_dir, pairs.get(pair))
        v["in_contexts"] = pair in pairs
        if v["in_contexts"] and v["verdict"] != "INVALID_VERDICT":
            covered.add(pair)
        unmatched += not v["in_contexts"]
    for (k, s), row in pairs.items():
        if (k, s) not in covered:
            verdicts.append({"key": k, "file": row["file"], "line": row.get("line"), "sentence": s,
                             "verdict": "UNCHECKED", "note": "no valid verdict for this (key, sentence) pair"})
    target.write_text(json.dumps(verdicts, indent=1, ensure_ascii=False))
    counts: dict[str, int] = {}
    for v in verdicts:
        if v.get("in_contexts", True):  # an orphan row judged a sentence that is not in the paper: not a total
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    print(", ".join(f"{k} {n}" for k, n in sorted(counts.items())), f"({len(pairs)} (key, sentence) pairs in contexts)")
    if unmatched:
        print(f"{unmatched} orphan verdict rows name a (key, sentence) not in contexts.json: they cover nothing and "
              "are left out of the totals above")


if __name__ == "__main__":
    main()

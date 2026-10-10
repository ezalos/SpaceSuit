# ABOUTME: Checks each bib entry against the record of EVERY id it carries (arXiv, Crossref; else the cited page, then
# ABOUTME: an OpenAlex title search): title, authors in order, year, venue. Usage: bib_check.py OUT_DIR refs.bib
import difflib
import html.parser
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

TITLE_VARIANT_MIN = 0.90  # below this the titles name different papers; at or above (but unequal) a human compares them
ARXIV_SPACING_S = 3.0  # arXiv API terms of use: one request every 3 seconds
HTTP_TIMEOUT_S = 30
RETRIES = 4  # 429 and 5xx are retried with doubling waits from BACKOFF_S
BACKOFF_S = 5.0
MIN_PAPER_CHARS = 5_000  # a paper's extracted text is far longer; less is an abstract or landing page
DETAIL_CHARS = 300  # the console cuts a finding's detail here; bib-check.json keeps it whole
UA = "paper-citation-audit/0.2"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
ARXIV_API = "https://export.arxiv.org/api/query?id_list="
SKIPPED_TYPES = {"comment", "string", "preamble"}
MONTHS = {m[:3]: m for m in ("january", "february", "march", "april", "may", "june", "july", "august", "september",
                             "october", "november", "december")}

_NEW_ID = r"\d{4}\.\d{4,5}(?:v\d+)?"
_OLD_ID = r"[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"  # pre-2007 ids such as cs/0601001 or math.GT/0309136
_ID_END = r"(?!\w|\.\d)"  # right boundary: 2106.012345 must never read as the real 2106.01234
ARXIV_ID = re.compile(rf"(?:{_NEW_ID}|{_OLD_ID}){_ID_END}")
ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(.+)", re.IGNORECASE)  # arXiv's own DOIs live at DataCite, not Crossref
_ID_CHARS = r"[^\s{}\"<>,]"
DOI = re.compile(rf"\b10\.\d{{4,9}}/{_ID_CHARS}+")
_ID_TOKEN = r"[^\s{}\"<>,~]+"
# An arXiv id named anywhere, its token parsed whole: an arxiv.org link (abs, pdf, html...), "arXiv:<token>", or
# "arXiv" then ~, space, { or / and something shaped like an id ("arXiv preprint" is not one).
ARXIV_MENTION = re.compile(rf"arxiv\.org/(?:abs|pdf|html|ps|format|src|e-print)/({_ID_TOKEN})|"
                           rf"arxiv\s*:[\s~{{]*({_ID_TOKEN})|"
                           rf"arxiv[\s~{{/]+(\d{_ID_TOKEN}|[a-z-]+(?:\.[A-Za-z]{{2}})?/\d{_ID_TOKEN})", re.IGNORECASE)
ID_IN_FORM = re.compile(r"\d{4}\.\d+(?:v\d+)?|\b(?!abs/)[a-z-]+(?:\.[A-Z]{2})?/\d+")  # CoRR abs/1706.03763
VOLUME_ABS = re.compile(r"abs/(\S+)")  # DBLP writes an arXiv paper as journal CoRR, volume abs/<id>
NO_ID_FIELDS = {"abstract", "eprint", "doi", "archiveprefix", "eprinttype", "eprintclass", "primaryclass"}


class BibError(ValueError):
    pass


# ---------------------------------------------------------------- BibTeX tokenizer

ENTRY_START = re.compile(r"@\s*([A-Za-z]\w*)\s*([{(])")
FIELD_NAME = re.compile(r"([A-Za-z][\w:.+/-]*)\s*=")
BARE_VALUE = re.compile(r"[^\s,#{}()\"=]+")
ENTRY_KEY = re.compile(r"\s*([^\s,{}()\"=#]*)\s*")


def _line(text: str, i: int) -> int:
    return text.count("\n", 0, i) + 1


def _skip(text: str, j: int, chars: str = " \t\r\n") -> int:
    while j < len(text) and text[j] in chars:
        j += 1
    return j


def brace_end(text: str, i: int) -> int | None:
    """Index just after the brace group opening at text[i]; None when it never closes. The one brace matcher of the
    skill: the .bib tokenizer, definition bodies, captions and headings all use it."""
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return j + 1
    return None


def _close_brace(text: str, i: int) -> int:
    if (j := brace_end(text, i)) is None:
        raise BibError(f"brace opened at line {_line(text, i)} is never closed")
    return j - 1


def _value_part(text: str, i: int, macros: dict) -> tuple[str, int]:
    if i >= len(text):
        raise BibError("value missing at end of file")
    if text[i] == "{":
        j = _close_brace(text, i)
        return text[i + 1:j], j + 1
    if text[i] == '"':
        depth, j = 0, i + 1
        while j < len(text):
            c = text[j]
            if c == "\\" and text[j + 1:j + 2] == '"':  # \" is an umlaut, never the end of the value
                j += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"' and depth == 0:
                return text[i + 1:j], j + 1
            j += 1
        raise BibError(f"quoted value opened at line {_line(text, i)} is never closed")
    m = BARE_VALUE.match(text, i)
    if not m:
        raise BibError(f"expected a value at line {_line(text, i)}, found {text[i:i + 20]!r}")
    w = m.group()
    return (w if w.isdigit() else macros.get(w.lower(), w)), m.end()


def _fields(text: str, j: int, close: str, macros: dict) -> tuple[int, dict]:
    """Parses `name = value # value, ...` up to the entry's closing delimiter; the scan always advances past a value."""
    fields: dict[str, str] = {}
    while True:
        j = _skip(text, j, " \t\r\n,")
        if j >= len(text):
            raise BibError("entry is never closed")
        if text[j] == close:
            return j + 1, fields
        fm = FIELD_NAME.match(text, j)
        if not fm:
            raise BibError(f"expected a field name at line {_line(text, j)}, found {text[j:j + 20]!r}")
        j, parts = _skip(text, fm.end()), []
        while True:
            part, j = _value_part(text, j, macros)
            parts.append(part)
            j = _skip(text, j)
            if text[j:j + 1] != "#":
                break
            j = _skip(text, j + 1)
        if fm.group(1).lower() in fields:
            raise BibError(f"duplicate field {fm.group(1)!r} at line {_line(text, fm.start())}")
        fields[fm.group(1).lower()] = "".join(parts)
        if text[j:j + 1] not in (",", close):
            raise BibError(f"expected ',' or {close!r} after field {fm.group(1)!r} at line {_line(text, j)}")


def parse_bib_report(text: str) -> tuple[list[dict], list[str]]:
    """Entries as {type, key, fields, line} plus parse errors; @string macros and # concatenation are resolved."""
    entries, errors, macros, seen = [], [], dict(MONTHS), set()
    i = 0
    while (m := ENTRY_START.search(text, i)):
        typ, close = m.group(1).lower(), ")" if m.group(2) == "(" else "}"
        try:
            if typ in ("comment", "preamble"):
                i = (_close_brace(text, m.end() - 1) if close == "}" else text.index(")", m.end())) + 1
                continue
            if typ == "string":
                i, defs = _fields(text, m.end(), close, macros)
                macros.update({k.lower(): v for k, v in defs.items()})
                continue
            km = ENTRY_KEY.match(text, m.end())
            key, j = km.group(1), km.end()
            if not key:
                raise BibError("entry has no key")
            if text[j:j + 1] not in (",", close):
                raise BibError(f"expected ',' after the key {key!r}")
            i, fields = _fields(text, j, close, macros)
            if key in seen:
                raise BibError(f"duplicate key {key!r}")
            seen.add(key)
            entries.append({"type": typ, "key": key, "fields": fields, "line": _line(text, m.start())})
        except ValueError as e:  # BibError, or str.index finding no ')'
            errors.append(f"line {_line(text, m.start())} @{typ}: {e}")
            i = m.end()
    return entries, errors


def load_bib(path: Path) -> list[dict]:
    """Parses a .bib and refuses (exit) unless the raw @-entry count equals the parsed count with no error."""
    text = path.read_text(errors="replace")
    entries, errors = parse_bib_report(text)
    raw = [t for t, _ in ENTRY_START.findall(text) if t.lower() not in SKIPPED_TYPES]
    print(f"{path}: {len(raw)} raw @-entries, {len(entries)} parsed")
    for e in errors:
        print(f"  BIB PARSE ERROR {e}")
    if errors or len(raw) != len(entries):
        raise SystemExit(f"refusing {path}: {len(raw)} raw @-entries, {len(entries)} parsed, {len(errors)} parse "
                         "errors (an @-entry inside a value or an @comment also counts; fix the .bib)")
    return entries


def load_bibs(paths: list[Path]) -> list[dict]:
    """Every entry of several .bib files; a key defined in two files is refused like a duplicate in one."""
    entries, where = [], {}
    for p in paths:
        for e in load_bib(p):
            if e["key"] in where:
                raise SystemExit(f"refusing: key {e['key']!r} is defined in {where[e['key']]} and in {p}")
            where[e["key"]] = p
            entries.append(e)
    return entries


def split_args(args: list[str]) -> tuple[Path, list[Path], list[str], dict[str, list[str]]]:
    """OUT_DIR, then .bib files, then other files; `--name value` options are collected separately."""
    opts: dict[str, list[str]] = {}
    rest = []
    it = iter(args)
    for a in it:
        if a.startswith("--"):
            name, _, val = a[2:].partition("=")
            opts.setdefault(name, []).append(val or next(it, ""))
        else:
            rest.append(a)
    if not rest:
        raise SystemExit("usage: <script> OUT_DIR refs.bib [more.bib ...] [files ...]")
    files = [Path(p) for p in rest[1:]]
    return Path(rest[0]), [p for p in files if p.suffix == ".bib"], [p for p in files if p.suffix != ".bib"], opts


def fresh_output(path: Path) -> Path:
    """Removes a script's own output before it runs, so a refusal can never leave a stale result behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return path


# ---------------------------------------------------------------- normalisation

_SPECIAL = {"ss": "ss", "ae": "ae", "AE": "ae", "oe": "oe", "OE": "oe", "aa": "a", "AA": "a", "o": "o", "O": "o",
            "l": "l", "L": "l", "i": "i", "j": "j"}
_SPECIAL_RE = re.compile(r"\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])(?:\{\})? ?")
_NO_DECOMPOSE = str.maketrans({"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe",
                               "Œ": "OE", "đ": "d", "ð": "d", "þ": "th", "ı": "i"})  # NFKD leaves these whole
_ACCENT_SYM = re.compile(r"\\[\"'`^~=.]\s*(?:\{\s*([A-Za-z])\s*\}|([A-Za-z]))")
_ACCENT_LET = re.compile(r"\\[Hcuvkrdbt](?:\s*\{\s*([A-Za-z])\s*\}|\s+([A-Za-z]))")  # \c c needs the space
_GREEK = ("alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|omicron|pi|rho|sigma|tau|upsilon|"
          "phi|chi|psi|omega")
_GREEK_CMD = re.compile(rf"\\(?:var)?({_GREEK})(?![A-Za-z])", re.IGNORECASE)


def _greek_name(c: str) -> str:
    m = re.search(r"LETTER (?:FINAL )?([A-Z]+)", unicodedata.name(c, ""))
    return f" {m.group(1).lower()} " if m else c


def norm(s: str) -> str:
    """Lower-case ASCII words: LaTeX accents folded into their letter, Greek letters (command or Unicode) by name."""
    s = _GREEK_CMD.sub(lambda m: f" {m.group(1).lower()} ", s)
    s = re.sub(r"[\u0370-\u03ff]", lambda m: _greek_name(m.group()), s)
    s = _SPECIAL_RE.sub(lambda m: _SPECIAL[m.group(1)], s)
    s = _ACCENT_SYM.sub(lambda m: m.group(1) or m.group(2), s)
    s = _ACCENT_LET.sub(lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    s = re.sub(r"[{}]", "", s)  # {\"u} must give u, not split the word around it
    s = re.sub(r"[$\\^_~]", " ", s).translate(_NO_DECOMPOSE)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def family(name: str) -> list[str]:
    """The family-name words, normalised: before the first comma ('van der Berg, Jan'), else from the first lower-case
    particle on ('Jan van der Berg'), else the last word ('Quoc V. Le'); Jr. and the like dropped."""
    if "," in name:
        return norm(name.split(",")[0]).split()
    raw = [w for w in name.split() if norm(w) not in SUFFIXES]
    start = next((i for i, w in enumerate(raw[1:], 1) if w[:1].islower()), len(raw) - 1)
    return norm(" ".join(raw[start:])).split() if raw else []


def initial(name: str) -> str:
    """First letter of the given name ('Smith, Ann', 'Ann Smith', 'King, Jr., Martin' -> a, a, m); '' when none."""
    parts = name.split(",")
    given = parts[-1] if len(parts) > 1 else " ".join(name.split()[:-1])
    return norm(given)[:1]


_AND = re.compile(r"\s+and\s+", re.IGNORECASE)


def split_authors(field: str) -> list[str]:
    """BibTeX author list split on ' and ' outside braces."""
    names, depth, start, i = [], 0, 0, 0
    while i < len(field):
        depth += {"{": 1, "}": -1}.get(field[i], 0)
        if depth == 0 and (m := _AND.match(field, i)):
            names.append(field[start:i].strip())
            start = i = m.end()
            continue
        i += 1
    names.append(field[start:].strip())
    return [n for n in names if n]


def parse_arxiv_id(s: str) -> str | None:
    s = re.sub(r"^arxiv:\s*", "", s.strip(), flags=re.IGNORECASE)
    return s if ARXIV_ID.fullmatch(s) else None


def _base(id_: str) -> str:
    return re.sub(r"v\d+$", "", id_)


def _clean(token: str) -> str:
    """An id token as found in prose or a URL, without a .pdf suffix or trailing punctuation."""
    return re.sub(r"(?:\.pdf)?[.;:)\]]*$", "", urllib.parse.unquote(token))


def _doi_id(field: str, doi: str, raw: str) -> tuple[str, str, str | None, str]:
    if (am := ARXIV_DOI.fullmatch(doi)):
        return field, "arxiv", parse_arxiv_id(am.group(1)), raw
    return field, "crossref", doi if DOI.fullmatch(doi) else None, raw


def identifiers(f: dict) -> list[tuple[str, str, str | None, str]]:
    """Every identifier the entry carries in ANY field (abstract aside), as (field, lookup kind, parsed id or None when
    malformed, raw value). An eprint of another archive (pubmed, hdl) is left to a note."""
    out = []
    ep = f.get("eprint", "").strip()
    if ep and (f.get("archiveprefix") or f.get("eprinttype") or "arxiv").strip().lower() == "arxiv":
        out.append(("eprint", "arxiv", parse_arxiv_id(ep), ep))
    if (doi := re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", f.get("doi", "").strip(), flags=re.IGNORECASE)):
        out.append(_doi_id("doi", _clean(doi), doi))
    for name, value in f.items():
        if name in NO_ID_FIELDS:
            continue
        if name == "volume" and (m := VOLUME_ABS.fullmatch(value.strip())):
            out.append((name, "arxiv", parse_arxiv_id(m.group(1)), value))
        if "arxiv" in name:  # Mendeley's arxivId
            out.append((name, "arxiv", parse_arxiv_id(value), value))
        if ARXIV_FORM.fullmatch(" ".join(value.split())) and (m := ID_IN_FORM.search(value)):
            out.append((name, "arxiv", parse_arxiv_id(m.group()), value))
        for m in ARXIV_MENTION.finditer(value):
            out.append((name, "arxiv", parse_arxiv_id(_clean(next(g for g in m.groups() if g))), value))
        for m in DOI.finditer(value):
            out.append(_doi_id(name, _clean(m.group()), value))
    seen, unique = set(), []
    for label, kind, value, raw in out:
        k = (kind, _base(value).lower() if value else raw)
        if k not in seen:
            seen.add(k)
            unique.append((label, kind, value, raw))
    return unique


def arxiv_id(f: dict) -> str | None:
    return next((v for _, kind, v, _ in identifiers(f) if kind == "arxiv" and v), None)


def text_path(out_dir: Path, key: str) -> Path:
    """OUT_DIR/texts/<key>.txt, the key percent-encoded so a key holding '/' stays one file inside texts/."""
    return out_dir / "texts" / (urllib.parse.quote(key, safe="") + ".txt")


def years_in(s: str) -> set[int]:
    return {int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", s or "")}


# ---------------------------------------------------------------- lookups

def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if attempt == RETRIES or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(BACKOFF_S * 2 ** attempt)
    raise AssertionError("unreachable")


def get_or_none(url: str) -> bytes | None:
    """The body, or None on a 404 (no such record); any other failure raises."""
    try:
        return get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _mailto(sep: str) -> str:
    """Crossref and OpenAlex put callers who name a contact address in their polite pool."""
    email = os.environ.get("CONTACT_EMAIL")
    return f"{sep}mailto={urllib.parse.quote(email)}" if email else ""


_last_arxiv = [0.0]


def record(source: str, title: str, authors, years, venue: str | None = None, notice: str | None = None) -> dict:
    """What every lookup returns: one shape, so every source is compared the same way. notice: the record is an
    erratum or correction, not the paper."""
    return {"source": source, "title": " ".join((title or "").split()), "authors": list(authors),
            "years": sorted({int(y) for y in years if y}), "venue": venue, "notice": notice}


def arxiv(id_: str) -> dict | None:
    """None when arXiv has no such paper: an unknown well-formed id gives an EMPTY feed, a malformed one an error.
    Its years are the v1 year and any year in the journal_ref, never a later version's year."""
    time.sleep(max(0.0, ARXIV_SPACING_S - (time.monotonic() - _last_arxiv[0])))
    _last_arxiv[0] = time.monotonic()
    query = re.sub(r"^([a-z-]+)\.[A-Z]{2}/", r"\1/", id_)  # math.DG/0303109 is known to the API as math/0303109
    root = ET.fromstring(get(ARXIV_API + urllib.parse.quote(query)))
    for e in root.findall(ATOM + "entry"):
        eid = e.findtext(ATOM + "id") or ""
        if "/api/errors" in eid or _base(eid.split("/abs/")[-1]) != _base(query):
            continue
        ref = " ".join((e.findtext(ARXIV_NS + "journal_ref") or "").split()) or None
        return record("arXiv " + id_, e.findtext(ATOM + "title"),
                      [a.findtext(ATOM + "name") or "" for a in e.findall(ATOM + "author")],
                      {int(e.findtext(ATOM + "published")[:4])} | years_in(ref or ""), ref)
    return None


def crossref(doi: str) -> dict | None:
    if (body := get_or_none("https://api.crossref.org/works/" + urllib.parse.quote(doi) + _mailto("?"))) is None:
        return None
    m = json.loads(body)["message"]
    years = {(m.get(k, {}).get("date-parts") or [[None]])[0][0] for k in ("issued", "published-print",
                                                                          "published-online")}
    return record("Crossref " + doi, (m.get("title") or [""])[0],
                  [f"{a['family']}, {a.get('given', '')}" if a.get("family") else a.get("name", "")
                   for a in m.get("author", [])], years, (m.get("container-title") or [None])[0],
                  "erratum" if m.get("update-to") or re.search(r"errat|correct", m.get("type", "")) else None)


def datacite(doi: str) -> dict | None:
    """Zenodo, figshare and other DataCite DOIs, which Crossref does not know."""
    if (body := get_or_none("https://api.datacite.org/dois/" + urllib.parse.quote(doi))) is None:
        return None
    a = json.loads(body)["data"]["attributes"]
    return record("DataCite " + doi, (a.get("titles") or [{}])[0].get("title"),
                  [f"{c['familyName']}, {c.get('givenName', '')}" if c.get("familyName") else c.get("name", "")
                   for c in a.get("creators", [])], [a.get("publicationYear")], (a.get("container") or {}).get("title"))


def doi_record(doi: str) -> dict | None:
    r = crossref(doi)
    return r if r is not None else datacite(doi)


class _Meta(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, list[str]] = {}
        self.title, self._in_title = "", False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "meta" and (name := (d.get("name") or d.get("property") or "").lower()) and d.get("content"):
            self.meta.setdefault(name, []).append(d["content"])
        self._in_title = self._in_title or tag == "title"

    def handle_endtag(self, tag):
        self._in_title = self._in_title and tag != "title"

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def url_page(url: str) -> dict | None:
    """The cited page's own metadata (citation_* meta tags, else <title>); None when it carries no title.
    Only parsed metadata is compared, never the raw HTML, which holds URLs and slugs."""
    body = get(url)
    if body[:5] == b"%PDF-":
        return None
    p = _Meta()
    p.feed(body.decode("utf-8", "replace"))

    def first(*names: str) -> str | None:
        return next((p.meta[n][0] for n in names if n in p.meta), None)

    title = first("citation_title", "dc.title", "og:title") or " ".join(p.title.split())
    if not title:
        return None
    date = first("citation_publication_date", "citation_date", "citation_online_date", "dc.date") or ""
    return record("cited page " + url, title, p.meta.get("citation_author", []), years_in(date),
                  first("citation_conference_title", "citation_journal_title"))


OPENALEX_HITS = 5  # a title search's best few; the rest are other papers


def _oa(path: str) -> dict | None:
    """An OpenAlex API answer (with mailto), None on a 404."""
    body = get_or_none("https://api.openalex.org/" + path + _mailto("&" if "?" in path else "?"))
    return None if body is None else json.loads(body)


def _venue(loc: dict | None) -> str | None:
    """A location's venue name; a repository (Zenodo, a university archive) is where a copy sits, not a venue."""
    src = (loc or {}).get("source") or {}
    return None if src.get("type") == "repository" else src.get("display_name")


def _oa_search(title: str) -> list[dict]:
    return (_oa(f"works?search={urllib.parse.quote(norm(title))}&per-page={OPENALEX_HITS}") or {}).get("results", [])


def openalex_title(title: str) -> dict | None:
    best = max(_oa_search(title), default=None,
               key=lambda w: difflib.SequenceMatcher(None, norm(w.get("title") or ""), norm(title)).ratio())
    if not best:
        return None
    return record("OpenAlex search " + best["id"], best.get("title"),
                  [a["author"]["display_name"] for a in best.get("authorships", [])], [best.get("publication_year")],
                  _venue(best.get("primary_location")))


def _not_arxiv(names) -> list[str]:
    return list(dict.fromkeys(n for n in names if n and not re.search(r"\barxiv\b|\bcorr\b", n, re.IGNORECASE)))


def openalex_venues(ids: list, title: str) -> list[str]:
    """Every venue OpenAlex lists for the work (primary location and all others), found by its DOI or arXiv DOI;
    when that names none, the venues of the works an OpenAlex title search returns under exactly this title (a
    published version usually has its own record)."""
    doi = next((v for _, k, v, _ in ids if k == "crossref" and v), None)
    if not doi and (a := next((v for _, k, v, _ in ids if k == "arxiv" and v), None)):
        doi = "10.48550/arXiv." + _base(a)
    w = (_oa(f"works/doi:{urllib.parse.quote(doi)}") if doi else None) or {}
    if (by_id := _not_arxiv(_venue(loc) for loc in [w.get("primary_location"), *(w.get("locations") or [])])):
        return by_id
    return _not_arxiv(_venue(h.get("primary_location")) for h in (_oa_search(title) if norm(title) else [])
                      if norm(h.get("title") or "") == norm(title))


DBLP_HITS = 5  # an exact-title search; more hits than this are other papers


def dblp_venues(title: str) -> list[str]:
    """Venues of the DBLP records whose title equals the entry's."""
    body = get(f"https://dblp.org/search/publ/api?q={urllib.parse.quote(norm(title))}&format=json&h={DBLP_HITS}")
    if body.lstrip()[:1] == b"<":
        raise RuntimeError("DBLP answered with an HTML page (a bot check), not JSON")
    venues = []
    for h in json.loads(body)["result"]["hits"].get("hit", []):
        info = h.get("info", {})
        if norm(info.get("title", "")) == norm(title):
            v = info.get("venue")
            venues += v if isinstance(v, list) else [v]
    return _not_arxiv(venues)


LOOKUPS = {"arxiv": arxiv, "crossref": doi_record}
VENUE_SOURCES = (("OpenAlex", openalex_venues), ("DBLP", lambda ids, title: dblp_venues(title)))


# ---------------------------------------------------------------- comparisons

def same_author(b: str, r: str) -> bool:
    """Whole family name (hyphenated and multi-word parts included) and first initial. A name written without a
    comma shows only its last family word for sure, so a shorter family must end the longer one, and the longer
    one's extra words must appear in the shorter one's full name: 'Garcia Perez, Ana' is 'Ana Garcia Perez', but
    'Smith-Fake, John' is not 'John Smith'."""
    if b.startswith("{") and b.endswith("}"):  # a corporate author: the whole name must match
        return norm(b) == norm(r)
    ib, ir = initial(b), initial(r)
    fb, fr = family(b), family(r)
    if not fb or not fr or (ib and ir and ib != ir):
        return False
    (short, short_name), (long_, _) = sorted([(fb, b), (fr, r)], key=lambda x: len(x[0]))
    extra = long_[:len(long_) - len(short)]
    return long_[len(extra):] == short and set(extra) <= set(norm(short_name).split())


def compare_authors(bib: list[str], rec: list[str]) -> tuple[str | None, str]:
    """(problem, state): the whole list in order, surname and first initial; a trailing 'and others' allows a longer
    record list. state is ok, mismatch, or none (the record lists no author: nothing was compared)."""
    truncated = bool(bib) and norm(bib[-1]) in ("others", "et al")
    names = bib[:-1] if truncated else bib
    if not rec:
        return None, "none"
    diffs = [f"#{i} bib {b!r} vs record {r!r}" for i, (b, r) in enumerate(zip(names, rec), 1) if not same_author(b, r)]
    if len(names) > len(rec):
        diffs.append(f"bib lists {len(names)} authors, the record {len(rec)}")
    elif len(rec) > len(names) and not truncated:
        diffs.append(f"the record lists {len(rec)} authors, the bib {len(names)} with no 'and others'")
    return ("; ".join(diffs), "mismatch") if diffs else (None, "ok")


VENUE_FILLER = {"proceedings", "proc", "of", "the", "on", "in", "and", "for", "a", "an", "at", "conference", "annual",
                "cvf", "pmlr", "volume", "vol", "pp"}
# Spelled-out ordinals name an edition ("The Twelfth International Conference ..."), as digits do.
ORDINALS = set("""first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth
fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth fortieth fiftieth twenty thirty
forty fifty""".split())
# Names one venue has had, which no word rule joins ("Advances in" only counts inside these).
VENUE_ALIASES = [("NeurIPS", "NIPS", "Advances in Neural Information Processing Systems",
                  "Neural Information Processing Systems", "Conference on Neural Information Processing Systems"),
                 ("IROS", "IEEE/RSJ International Conference on Intelligent Robots and Systems",
                  "International Conference on Intelligent Robots and Systems")]
# Never filler: workshop(s), symposium, track, magazine, letters, findings, companion, datasets, benchmarks, demo,
# journal, international. A publisher name is filler only once an acronym matched (IEEE ICRA is ICRA).
PUBLISHERS = {"ieee", "acm"}
ACRONYM_MIN_UPPER = 2  # CoRL, ICRA, NeurIPS; a capitalised word such as "Robot" is not an acronym
ACRONYM_MIN_WORDS = 2
ROMAN = re.compile(r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")


def _rest(words: list[str], drop: set[int], loose: bool = False) -> set[str]:
    """The distinguishing words: filler, numbers, a trailing edition numeral (XXI), dropped positions and, when loose,
    publisher names removed."""
    last = len(words) - 1
    return {w for i, w in enumerate(words) if i not in drop and w not in VENUE_FILLER
            and not (loose and w in PUBLISHERS)
            and not re.fullmatch(r"\d+(?:st|nd|rd|th)?", w) and w not in ORDINALS
            and not (i == last and ROMAN.fullmatch(w))}


def _alias(s: str) -> int | None:
    """The VENUE_ALIASES group a venue name belongs to, editions and years aside."""
    key = " ".join(sorted(_rest(norm(s).split(), set())))
    return next((i for i, group in enumerate(VENUE_ALIASES)
                 if key in {" ".join(sorted(_rest(norm(n).split(), set()))) for n in group}), None)


def _spellings(acr: str, words: list[str], skip: set[int], k: int = 0):
    """Every set of word positions whose in-order prefixes spell acr: corl = COnference on Robot Learning."""
    if not acr:
        yield frozenset()
        return
    for i in range(k, len(words)):
        if i in skip:
            continue
        for n in range(1, min(len(acr), len(words[i])) + 1):
            if acr[:n] == words[i][:n]:
                for rest in _spellings(acr[n:], words, skip, i + 1):
                    yield rest | {i}


def _acronyms(s: str) -> set[str]:
    return {a.lower() for a in re.findall(r"[A-Za-z]+", s) if sum(c.isupper() for c in a) >= ACRONYM_MIN_UPPER}


def venue_match(a: str, b: str) -> bool:
    """Equal distinguishing words; or, once an acronym is taken out of one side and out of the other (as the same
    acronym or as the words it spells), equal remaining words. CVPR is not CVPR Workshops, Nature is not Nature
    Communications, AI is not AI Magazine."""
    wa, wb = norm(a).split(), norm(b).split()
    if _rest(wa, set()) and _rest(wa, set()) == _rest(wb, set()):
        return True
    if (group := _alias(a)) is not None and group == _alias(b):
        return True
    for acr in (_acronyms(a) | _acronyms(b)) - VENUE_FILLER - PUBLISHERS:
        for x, y in ((wa, wb), (wb, wa)):
            dx, dy = {i for i, w in enumerate(x) if w == acr}, {i for i, w in enumerate(y) if w == acr}
            if not dx:
                continue
            if dy and _rest(x, dx, True) == _rest(y, dy, True):
                return True
            if any(len(used) >= ACRONYM_MIN_WORDS and _rest(x, dx, True) == _rest(y, dy | used, True)
                   for used in _spellings(acr, y, dy)):
                return True
    return False


VENUE_FIELDS = ("journal", "journaltitle", "booktitle", "eventtitle", "howpublished", "series", "institution", "school",
                "organization")
PRIMARY_VENUE = {"journal", "journaltitle", "booktitle"}  # a record naming another venue contradicts these
ARXIV_FORM = re.compile(r"(?:arxiv(?:\s+preprint)?(?:\s+arxiv)?(?:\s*:?\s*[\w./-]*\d[\w./-]*)?|arxiv e-prints|"
                        r"corr(?:\s*,?\s*abs/\S+)?)\.?", re.IGNORECASE)
NOTE_FIELDS = ("note", "addendum", "type")  # free text that often claims a venue
NOTE_NOISE = re.compile(r"\b(?:accepted|to appear|published|presented|presentation|spotlight|oral|poster|paper|at|in|"
                        r"to|for|by|also|version|full|extended|short)\b|[,;.()]", re.IGNORECASE)
NOTE_LEAD = re.compile(r"\b(?:accepted|to appear|published|presented)\b|^in\s", re.IGNORECASE)  # "Accepted at Nature"
# a note that is only a name, such as "Nature Machine Intelligence", claims a venue
_NAME_WORD = r"(?:[A-Z][\w&-]*|of|and|the|on|in|for)"
NAME_ONLY = re.compile(rf"{_NAME_WORD}(?:\s+{_NAME_WORD})*")
LINK = re.compile(r"\\url\{[^}]*\}|https?://\S+")


def bib_venues(f: dict, notes: list[str]) -> list[tuple[str, str]]:
    """Every printed venue field as (field, value), plus a venue a note, addendum or type claims ("ICML 2017", "Oral
    at ICML"); a field that is only an arXiv/CoRR form is a preprint and left out, with a note."""
    out = []
    for k in VENUE_FIELDS:
        v = " ".join(f.get(k, "").split())
        if k == "howpublished":  # the text beside a link is a venue claim; the link itself is not
            v = LINK.sub(" ", v).strip(" ,;.")
        if not v:
            continue
        if ARXIV_FORM.fullmatch(v):
            notes.append(f"{k} {v!r} is an arXiv preprint form: no venue check")
            continue
        out.append((k, v))
    for k in NOTE_FIELDS:  # a lead phrase, an acronym or a year left once links and ids are gone: a venue claim
        rest = DOI.sub(" ", ARXIV_MENTION.sub(" ", LINK.sub(" ", " ".join(f.get(k, "").split())))).strip()
        if rest and not ARXIV_FORM.fullmatch(rest) and (_acronyms(rest) or years_in(rest) or NOTE_LEAD.search(rest)
                                                        or NAME_ONLY.fullmatch(rest)):
            out.append((k, " ".join(NOTE_NOISE.sub(" ", rest).split()) or rest.strip()))
    return out


def compare(title: str, authors: list[str], year: str, r: dict) -> tuple[list, str]:
    out, src = [], r["source"]
    if norm(title) and norm(title) != norm(r["title"]):
        ratio = difflib.SequenceMatcher(None, norm(title), norm(r["title"])).ratio()
        label = "TITLE_VARIANT" if ratio >= TITLE_VARIANT_MIN else "MISMATCH: title"
        out.append((label, f"{src} (similarity {ratio:.3f}): bib {title!r} vs record {r['title']!r}"))
    problem, state = compare_authors(authors, r["authors"]) if authors else (None, "ok")
    if problem:
        out.append(("MISMATCH: authors", f"{src}: {problem}"))
    if year and r["years"] and not (year.strip().isdigit() and int(year) in r["years"]):
        out.append(("MISMATCH: year", f"{src}: bib {year!r} vs record {r['years']}"))
    return out, state


def check_venue(venues: list, records: list, ids: list, title: str, errors: list) -> list:
    """Each venue field confirmed by a record, else by OpenAlex or DBLP (one request each, only when a field is still
    unconfirmed). Unconfirmed: MISMATCH when a main venue field meets another venue, else VENUE_UNCHECKED."""
    found = [(r["source"], r["venue"]) for r in records if r.get("venue")]
    own = [(s, v) for s, v in found if s.startswith(("Crossref", "DataCite"))]  # the DOI's own container

    def open_fields() -> list:
        return [(k, v) for k, v in venues if not any(venue_match(v, rv) for _, rv in found)]

    for name, fn in VENUE_SOURCES:
        if not open_fields():
            break
        try:
            found += [(name, v) for v in fn(ids, title)]
        except Exception as e:  # noqa: BLE001 - recorded; the venue then stays unchecked
            errors.append(f"{name} venue lookup: {e!r}")
    out, done = [], set()
    for k, v in venues:
        if k in PRIMARY_VENUE and own and not any(venue_match(v, rv) for _, rv in own):
            done.add(k)
            out.append(("MISMATCH: venue", f"bib {k} {v!r} vs the DOI's own record " + "; ".join(
                f"{s}: {rv!r}" for s, rv in own) + " (another version? a title-search hit elsewhere does not count)"))
    for k, v in open_fields():
        if k in done:
            continue
        if found and k in PRIMARY_VENUE:
            out.append(("MISMATCH: venue", f"bib {k} {v!r} vs " + "; ".join(f"{s}: {rv!r}" for s, rv in found)))
        else:
            out.append(("VENUE_UNCHECKED", f"no record, OpenAlex or DBLP entry confirms bib {k} {v!r}"))
    return out


def check(entry: dict) -> dict:
    f = entry["fields"]
    title = " ".join(f[k] for k in ("title", "subtitle", "titleaddon") if f.get(k))
    dated = m.group() if (m := re.match(r"\d{4}", f.get("date", "").strip())) else ""
    year = f.get("year", "").strip() or dated
    authors = split_authors(f.get("author", ""))
    notes, errors, records = ["volume, pages, publisher, address, month, editor and edition are not checked"], [], []
    venues = bib_venues(f, notes)
    out = {"key": entry["key"], "bib_title": title, "bib_year": year, "bib_authors": authors, "bib_venues": venues}
    findings: list[tuple[str, str]] = [(f"MISSING_FIELD: {k}", "the entry has no " + k)
                                       for k, v in (("title", title), ("author", f.get("author", "")), ("year", year))
                                       if not v.strip()]
    if dated and dated != year:
        findings.append(("MISMATCH: year (date)", f"year {year!r} but date {f['date']!r}"))
    if f.get("crossref"):
        findings.append(("VENUE_UNCHECKED", f"fields inherited through crossref={f['crossref']!r} are not checked"))
    if title.strip() and not norm(title):
        findings.append(("MISSING_FIELD: title", f"{title!r} is empty once LaTeX is stripped"))
    if authors and all(norm(a) in ("others", "et al") for a in authors):
        findings.append(("MISSING_FIELD: author", "the author list is only 'others'"))
    def lookup(label: str, fn, arg):
        """fn(arg); a failure is recorded as LOOKUP_ERROR (never fatal, never VERIFIED) and gives False."""
        try:
            return fn(arg)
        except Exception as e:  # noqa: BLE001 - recorded, blocks VERIFIED, never fatal
            errors.append(f"{label} {arg}: {e!r}")
            findings.append((f"LOOKUP_ERROR: {label}", f"{arg}: {e!r} (re-run before reporting)"))
            return False

    ids, mode = identifiers(f), "id"
    if f.get("eprint") and not any(label == "eprint" for label, *_ in ids):
        notes.append(f"eprint {f['eprint']!r} ({f.get('eprinttype') or f.get('archiveprefix')}) not checked")
    for label, kind, value, raw in ids:
        if value is None:
            findings.append((f"ID_NOT_FOUND: {label}", f"{raw!r} holds no well-formed {kind} id"))
            continue
        if (r := lookup(label, LOOKUPS[kind], value)) is False:
            continue
        if r is None:
            findings.append((f"ID_NOT_FOUND: {label}", f"no record exists for {value}"))
        else:
            records.append(r)
    if ids and f.get("url") and not any(label == "url" for label, *_ in ids):
        site = "openreview forum id " + m.group(1) if (m := re.search(r"openreview\.net/\w+\?id=([\w-]+)", f["url"])) \
            else f["url"]
        notes.append(f"url not checked: {site} (it names no arXiv id or DOI)")
    if not ids:
        if f.get("url"):
            mode = "page"
            if (r := lookup("url", url_page, f["url"])):
                records.append(r)
            elif r is None:
                notes.append("the cited page carries no title metadata (or is a PDF)")
        if not records and not errors:
            mode = "title search"
            if title and (r := lookup("openalex", openalex_title, title)):
                records.append(r)
            elif r is not False:
                findings.append(("NOT_FOUND", "no identifier, no usable cited page, no title-search hit"))
    states = []
    for r in records:
        found, state = compare(title, authors, year, r)
        findings += found
        states.append(state)
    if authors and states and "ok" not in states and "mismatch" not in states:
        findings.append(("AUTHORS_UNCHECKED", "no record lists the authors"))
    if year and records and not any(r["years"] for r in records):
        findings.append(("YEAR_UNCHECKED", f"no record carries a year to confirm {year!r}"))
    findings += [("MISMATCH: record type (erratum)", f"{r['source']} is an erratum or correction, not the paper")
                 for r in records if r.get("notice")]
    findings += [("MISMATCH: year (venue)", f"bib {k} {v!r} names {y}, the entry's year is {year!r} and the records "
                                            f"say {sorted({x for r in records for x in r['years']})}")
                 for k, v in venues for y in sorted(years_in(v))
                 if str(y) != year and not any(y in r["years"] for r in records)]
    if records:
        findings += check_venue(venues, records, ids, title, errors)
    weak = {"page": ["PAGE_MATCH"], "title search": ["WEAK_TITLE_SEARCH"]}.get(mode, []) if records else []
    labels = list(dict.fromkeys(weak + [lbl for lbl, _ in findings]))
    verified = mode == "id" and bool(records) and not findings
    out.update(records=records, findings=[{"label": lbl, "detail": d} for lbl, d in findings], notes=notes,
               errors=errors, status="VERIFIED" if verified else "; ".join(labels) or "NOT_FOUND")
    return out


def main() -> None:
    out_dir, bibs, others, opts = split_args(sys.argv[1:])
    if not bibs or others:
        raise SystemExit("usage: bib_check.py OUT_DIR refs.bib [more.bib ...] [--only KEY ...]")
    target = fresh_output(out_dir / "bib-check.json")
    entries = load_bibs(bibs)
    if (only := set(opts.get("only", []))):
        if (unknown := only - {e["key"] for e in entries}):
            raise SystemExit(f"--only names keys not in the bib: {sorted(unknown)}")
        entries = [e for e in entries if e["key"] in only]
    results = []
    for e in entries:
        r = check(e)
        results.append(r)
        print(f"{r['status'][:70]:70} {r['key']}")
        for x in r["findings"]:
            print(f"    {x['label']}: {x['detail'][:DETAIL_CHARS]}")
        for n in r["notes"]:
            print(f"    note: {n}")
        for err in r["errors"]:
            print(f"    ERROR {err}")
    by_title: dict[str, list[str]] = {}
    for r in results:
        if norm(r["bib_title"]):
            by_title.setdefault(norm(r["bib_title"]), []).append(r["key"])
    for r in results:
        if len(twins := by_title.get(norm(r["bib_title"]), [])) > 1:
            r["notes"].append(f"DUPLICATE_ENTRY: same title as {', '.join(k for k in twins if k != r['key'])}")
            print(f"    note on {r['key']}: {r['notes'][-1]}")
    target.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    bad = sum(r["status"] != "VERIFIED" for r in results)
    print(f"{len(results)} entries, {len(results) - bad} VERIFIED, {bad} not verified")


if __name__ == "__main__":
    main()

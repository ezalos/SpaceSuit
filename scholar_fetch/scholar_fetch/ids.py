# ABOUTME: Paper identifiers: arXiv ids, DOIs and PMIDs found in text or in a cited URL, in normal form.
# ABOUTME: identify() is what the chain keys on; no network here.
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})"
ARXIV_STAMP = re.compile(r"arXiv:\s?" + ARXIV_ID + r"(?:v\d+)?\s*\[[\w.\-]+\]\s*\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", re.I)
ARXIV_ANY = re.compile(r"(?:arXiv:\s?|arxiv\.org/(?:abs|pdf)/)" + ARXIV_ID + r"(?:v\d+)?", re.I)
DOI_ANY = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
DOI_GLUED_TAIL = re.compile(r"[.,](?=[A-Z][a-z])")  # a missing space glues the next sentence's Capitalized word on
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(\d{4}\.\d{4,5})$", re.I)

ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/" + ARXIV_ID + r"(?:v\d+)?", re.I)
PMID_URL = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d+)", re.I)
RESEARCHGATE_URL = re.compile(r"researchgate\.net/publication/\d+_([^/]+)", re.I)
PUBLISHER_VIEW_WORDS = {"full", "abstract", "pdf", "epdf", "fulltext", "html", "citation", "references", "figures", "summary", "meta", "supplementary"}


def normalize_doi(doi: str) -> str:
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi.strip(), flags=re.I)
    return d.rstrip(".,;)]").lower()


def trim_doi_tail(raw: str) -> str:
    """Cut at a '.'/',' immediately followed by a Capitalized word: extraction dropping a space glues the
    next sentence onto the DOI (10.1038/nature14539.This -> ...539.This). A real DOI's internal dots and
    dashes (10.1016/j.cell.2015.05.001, the SICI 3.0.CO;2-C suffix) never have a lowercase letter right
    after the capital, so they survive."""
    m = DOI_GLUED_TAIL.search(raw)
    return raw[: m.start()] if m else raw


def candidates(text: str) -> list[tuple[str, str]]:
    """De-duplicated ('arxiv'|'doi', value) pairs: arXiv margin stamps first, then everything in text order."""
    out: list[tuple[str, str]] = []

    def add(pair):
        if pair not in out:
            out.append(pair)

    for m in ARXIV_STAMP.finditer(text):
        add(("arxiv", m.group(1)))
    hits = [(m.start(), ("arxiv", m.group(1))) for m in ARXIV_ANY.finditer(text)]
    for m in DOI_ANY.finditer(text):
        doi = normalize_doi(trim_doi_tail(m.group(1)))
        arxiv = ARXIV_DOI.match(doi)
        hits.append((m.start(), ("arxiv", arxiv.group(1)) if arxiv else ("doi", doi)))
    for _, pair in sorted(hits):
        add(pair)
    return out


@dataclass(frozen=True)
class Ids:
    arxiv: str | None = None
    doi: str | None = None
    pmid: str | None = None

    def any(self) -> bool:
        return bool(self.arxiv or self.doi or self.pmid)


def _strip_url_view_suffix(doi: str) -> str:
    """Strip trailing '/' and known publisher view words (full, abstract, pdf, etc.) from a DOI
    extracted from a URL path. Only used in identify(); does not affect DOI_ANY or trim_doi_tail."""
    # Strip trailing slash first
    doi = doi.rstrip("/")
    # Repeatedly strip trailing path segments that are known view words (case-insensitive)
    while doi:
        parts = doi.rsplit("/", 1)
        if len(parts) == 2 and parts[1].lower() in PUBLISHER_VIEW_WORDS:
            doi = parts[0]
        else:
            break
    return doi


def identify(url: str) -> Ids:
    """What paper a cited URL points at, from the URL alone. An arXiv DataCite DOI counts as its arXiv id."""
    parts = urlsplit(url)
    bare = parts.netloc + parts.path  # query and fragment dropped: they are never part of an id
    m = ARXIV_URL.search(bare)
    if m:
        return Ids(arxiv=m.group(1))
    m = PMID_URL.search(bare)
    if m:
        return Ids(pmid=m.group(1))
    for value in [*parse_qs(parts.query).get("doi", []), unquote(parts.path)]:
        hit = DOI_ANY.search(value)
        if hit:
            raw_doi = hit.group(1)
            # Strip URL view suffixes (full, abstract, pdf, etc.) before normalization
            raw_doi = _strip_url_view_suffix(raw_doi)
            return from_doi(trim_doi_tail(raw_doi))
    return Ids()


def from_doi(doi: str) -> Ids:
    """The Ids a DOI stands for: normalised, and an arXiv DataCite DOI counted as its arXiv id."""
    doi = normalize_doi(doi)
    as_arxiv = ARXIV_DOI.match(doi)
    return Ids(arxiv=as_arxiv.group(1)) if as_arxiv else Ids(doi=doi)


def researchgate_title(url: str) -> str | None:
    """The title a ResearchGate publication URL spells in its slug (words joined by '_', punctuation
    dropped), or None for any other URL. ResearchGate never puts a DOI in the URL."""
    parts = urlsplit(url)
    m = RESEARCHGATE_URL.search(parts.netloc + parts.path)
    return unquote(m.group(1)).replace("_", " ").strip() if m else None


def same_title(a: str, b: str) -> bool:
    """Titles equal once markup, case and punctuation are dropped: a slug and a catalogue title of the
    same paper compare equal; a near-duplicate record (another word, a missing article) does not."""
    def words(s):
        return re.findall(r"[a-z0-9]+", re.sub(r"<[^>]+>", " ", s).lower())
    return bool(words(a)) and words(a) == words(b)

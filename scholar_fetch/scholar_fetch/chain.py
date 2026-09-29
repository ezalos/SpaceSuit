# ABOUTME: resolve(url): identify the paper, then arXiv, Unpaywall, Semantic Scholar, OpenAlex, the cited URL, first text wins.
# ABOUTME: Every step, taken or skipped, is logged in tried; open-access full text outranks a publisher landing page.
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import quote_plus

import requests

from .extract import is_pdf, to_text
from .http import Client
from .ids import ARXIV_DOI, Ids, from_doi, identify, normalize_doi, researchgate_title, same_title

STEPS = ("arxiv", "unpaywall", "semantic-scholar", "openalex", "direct")
ENDPOINTS = {
    "arxiv": "https://arxiv.org/pdf/{id}",
    "unpaywall": "https://api.unpaywall.org/v2/{doi}?email={email}",
    "s2": "https://api.semanticscholar.org/graph/v1/paper/{pid}?fields=externalIds,openAccessPdf",
    "openalex": "https://api.openalex.org/works/doi:{doi}",
    "openalex_pmid": "https://api.openalex.org/works/pmid:{pmid}",
    "openalex_search": "https://api.openalex.org/works?search={title}&per-page=5&select=doi,title",
}


@dataclass
class Fetched:
    url: str
    text: str | None
    served_by: str | None
    kind: str | None
    ids: Ids
    tried: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"url": self.url, "served_by": self.served_by, "kind": self.kind,
                "ids": asdict(self.ids), "tried": self.tried}


def _text_cache(client: Client, url: str):
    return client.cache_dir / "text" / (hashlib.sha256(url.encode()).hexdigest() + ".json")


def _fetch_text(client: Client, url: str, max_bytes: int) -> tuple[str | None, str, bool]:
    """Download and extract one document: (text, outcome, whether the body was a PDF).
    A success is cached by URL, a failure is not (it may be transient)."""
    path = _text_cache(client, url)
    if path.exists():
        cached = json.loads(path.read_text())
        if "pdf" in cached:  # an entry from before the content kind was recorded is a miss
            return cached["text"], "ok", cached["pdf"]
    d = client.download(url, max_bytes=max_bytes)
    if d.outcome != "ok":
        return None, d.outcome, False
    pdf = is_pdf(d.body, d.content_type)
    text, outcome = to_text(d.body, d.content_type)
    if text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "text": text, "pdf": pdf}, ensure_ascii=False))
        os.replace(tmp, path)  # atomic: a crash mid-write never leaves a corrupt cache file behind
    return text, outcome, pdf


def _s2(client: Client, ep: dict, ids: Ids) -> dict | None:
    pid = f"DOI:{ids.doi}" if ids.doi else f"ARXIV:{ids.arxiv}" if ids.arxiv else f"PMID:{ids.pmid}"
    key = os.environ.get("S2_API_KEY")
    return client.json("GET", ep["s2"].format(pid=pid), headers={"x-api-key": key} if key else None)


def _get(x, *keys):
    """Walk a chain of dict keys; anything that isn't a dict along the way (None, a list, a bare
    string, any successful-but-unexpected API answer) is simply "no answer", never an AttributeError."""
    for k in keys:
        x = x.get(k) if isinstance(x, dict) else None
    return x


def resolve(url: str, client: Client, endpoints: dict | None = None, max_bytes: int = 50_000_000) -> Fetched:
    ep = {**ENDPOINTS, **(endpoints or {})}
    ids = identify(url)
    f = Fetched(url, None, None, None, ids)
    s2 = None
    landings: list[tuple[str, str]] = []  # (step, text): HTML an open-access step served instead of the paper

    def log(step, target, outcome):
        f.tried.append({"step": step, "target": target, "outcome": outcome})

    def attempt(step, target) -> bool:
        """True when the chain is done. The kind comes from the content, not the step: an open-access
        location may be an HTML landing page, which is only kept as a fallback while the chain goes on."""
        text, outcome, pdf = _fetch_text(client, target, max_bytes)
        if text is None:
            log(step, target, outcome)
            return False
        if pdf or (step == "direct" and not ids.any()):
            log(step, target, outcome)
            f.text, f.served_by, f.kind = text, step, "fulltext"
            return True
        log(step, target, "landing")
        landings.append((step, text))
        return False

    def api(step, call):
        try:
            return call()
        # requests.RequestException: a connection/timeout/HTTP error out of Client.json.
        # RuntimeError: Client.json's own "gave up after N retries".
        # ValueError: a malformed JSON body (json.JSONDecodeError is a ValueError).
        # One broken API never stops the chain; the log keeps the reason.
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            log(step, "", f"error: {type(exc).__name__}")
            return False

    # PMID-only: OpenAlex maps it to a DOI first (unauthenticated S2 429s even on one call); only when
    # OpenAlex has no answer does S2 get asked, and step 3 then reuses that same S2 answer.
    if ids.pmid and not ids.doi:
        o = api("openalex", lambda: client.json("GET", ep["openalex_pmid"].format(pmid=ids.pmid)))
        doi = _get(o, "doi")
        if doi:
            log("openalex", f"pmid:{ids.pmid}", "ok")
            ids = f.ids = Ids(arxiv=ids.arxiv, doi=normalize_doi(doi), pmid=ids.pmid)
        else:
            if o is not False:  # False means api() already logged the error; don't log it twice
                log("openalex", f"pmid:{ids.pmid}", "no-doi")
            s2 = api("semantic-scholar", lambda: _s2(client, ep, ids))
            doi = _get(s2, "externalIds", "DOI")
            if doi:
                ids = f.ids = Ids(arxiv=ids.arxiv, doi=normalize_doi(doi), pmid=ids.pmid)

    # A ResearchGate link carries only the title. OpenAlex's search gives a DOI when one of its top five
    # hits has exactly that title (an arXiv DataCite DOI becomes the arXiv id); a near match is never
    # taken, since a wrong DOI would serve a different paper as this one.
    title = None if ids.any() else researchgate_title(url)
    if title:
        o = api("openalex", lambda: client.json("GET", ep["openalex_search"].format(title=quote_plus(title))))
        hits = _get(o, "results")
        match = next((w for w in hits if isinstance(w, dict) and isinstance(w.get("doi"), str)
                      and same_title(str(w.get("title") or ""), title)), None) if isinstance(hits, list) else None
        if match:
            ids = f.ids = from_doi(match["doi"])
            log("openalex", f"title:{title}", "ok")
        elif o is not False:
            log("openalex", f"title:{title}", "no-title-match")

    if ids.arxiv:
        if attempt("arxiv", ep["arxiv"].format(id=ids.arxiv)):
            return f
    else:
        log("arxiv", "", "skipped: no arxiv id")

    email = os.environ.get("CONTACT_EMAIL")
    if not ids.doi:
        log("unpaywall", "", "skipped: no doi")
    elif not email:
        log("unpaywall", ids.doi, "skipped: no-contact-email")
    else:
        u = api("unpaywall", lambda: client.json("GET", ep["unpaywall"].format(doi=ids.doi, email=email)))
        target = _get(u, "best_oa_location", "url_for_pdf") or _get(u, "best_oa_location", "url")
        if target and attempt("unpaywall", target):
            return f
        if u is not False and not target:
            log("unpaywall", ids.doi, "no-oa-location")

    if not ids.any():
        log("semantic-scholar", "", "skipped: no paper id")
    else:
        if s2 is None:
            s2 = api("semantic-scholar", lambda: _s2(client, ep, ids))
        target = _get(s2, "openAccessPdf", "url")
        if target and attempt("semantic-scholar", target):
            return f
        if s2 is not False and not target:
            log("semantic-scholar", "", "no-oa-location")

    if not ids.doi or ARXIV_DOI.match(ids.doi):
        log("openalex", "", "skipped: no doi")
    else:
        o = api("openalex", lambda: client.json("GET", ep["openalex"].format(doi=ids.doi)))
        target = _get(o, "best_oa_location", "pdf_url") or _get(o, "open_access", "oa_url")
        if target and attempt("openalex", target):
            return f
        if o is not False and not target:
            log("openalex", ids.doi, "no-oa-location")

    if not attempt("direct", url) and landings:
        f.text, f.served_by = landings[0][1], landings[0][0]  # no full text anywhere: the first landing page
        f.kind = "landing"
    return f

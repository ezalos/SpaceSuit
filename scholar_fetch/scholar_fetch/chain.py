# ABOUTME: resolve(url): identify the paper, then arXiv, Unpaywall, Semantic Scholar, OpenAlex, the cited URL, first text wins.
# ABOUTME: Every step, taken or skipped, is logged in tried; open-access full text outranks a publisher landing page.
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field

import requests

from .extract import to_text
from .http import Client
from .ids import ARXIV_DOI, Ids, identify, normalize_doi

STEPS = ("arxiv", "unpaywall", "semantic-scholar", "openalex", "direct")
ENDPOINTS = {
    "arxiv": "https://arxiv.org/pdf/{id}",
    "unpaywall": "https://api.unpaywall.org/v2/{doi}?email={email}",
    "s2": "https://api.semanticscholar.org/graph/v1/paper/{pid}?fields=externalIds,openAccessPdf",
    "openalex": "https://api.openalex.org/works/doi:{doi}",
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


def _fetch_text(client: Client, url: str, max_bytes: int) -> tuple[str | None, str]:
    """Download and extract one document; a success is cached by URL, a failure is not (it may be transient)."""
    path = _text_cache(client, url)
    if path.exists():
        return json.loads(path.read_text())["text"], "ok"
    d = client.download(url, max_bytes=max_bytes)
    if d.outcome != "ok":
        return None, d.outcome
    text, outcome = to_text(d.body, d.content_type)
    if text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "text": text}, ensure_ascii=False))
        os.replace(tmp, path)  # atomic: a crash mid-write never leaves a corrupt cache file behind
    return text, outcome


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

    def log(step, target, outcome):
        f.tried.append({"step": step, "target": target, "outcome": outcome})

    def attempt(step, target) -> bool:
        text, outcome = _fetch_text(client, target, max_bytes)
        log(step, target, outcome)
        if text is not None:
            f.text, f.served_by = text, step
            f.kind = "landing" if step == "direct" and ids.any() else "fulltext"
        return text is not None

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

    # PMID-only: S2 maps it to a DOI first, and step 3 reuses the same answer.
    if ids.pmid and not ids.doi:
        s2 = api("semantic-scholar", lambda: _s2(client, ep, ids))
        doi = _get(s2, "externalIds", "DOI")
        if doi:
            ids = f.ids = Ids(arxiv=ids.arxiv, doi=normalize_doi(doi), pmid=ids.pmid)

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

    attempt("direct", url)
    return f

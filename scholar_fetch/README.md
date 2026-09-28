# scholar_fetch

Open-access failover chain: identify a cited URL, walk a deterministic chain of open-access
locations, and return extracted text — for paper downloads and for checking cited claims. No
cookies, no logins, no shadow libraries: only open-access locations count.

## The chain

First success wins:

| # | Step | Runs when | Success means |
|---|---|---|---|
| 1 | arXiv PDF (`arxiv.org/pdf/<id>`) | arXiv id known | PDF downloaded, text extracted |
| 2 | Unpaywall `best_oa_location` (`url_for_pdf`, else `url`) | DOI known | OA copy downloaded, text extracted |
| 3 | Semantic Scholar `openAccessPdf.url` | any id known | same |
| 4 | OpenAlex `open_access.oa_url` | DOI known and not an arXiv DataCite DOI | same |
| 5 | The cited URL, direct | always | HTTP 200 with extractable text |
| 6 | Fail | — | `text = None`, `tried[]` says why each step failed |

## Env vars

- `CONTACT_EMAIL` — required for the Unpaywall step and for the polite `mailto` User-Agent sent
  with every request. Without it, Unpaywall is skipped and the User-Agent carries no contact.
- `S2_API_KEY` — optional. Sent only to `api.semanticscholar.org`; unauthenticated Semantic
  Scholar access is used when it is absent.

## Tests

    uv run pytest
    uv run pytest -m live

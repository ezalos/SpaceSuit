# ABOUTME: Fetches each entry's text through scholar_fetch's open-access chain into OUT_DIR/texts/; fetched.json says
# ABOUTME: what served it and if it was a PDF. Usage: uv run --project <scholar_fetch> fetch_texts.py OUT_DIR refs.bib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts, also under python -I
from bib_check import MIN_PAPER_CHARS, arxiv_id, fresh_output, identifiers, load_bibs, split_args, text_path
from scholar_fetch.chain import resolve
from scholar_fetch.http import Client

try:  # private: the only place that records whether the served body was a PDF
    from scholar_fetch.chain import _text_cache
except ImportError as e:
    raise SystemExit("fetch_texts needs scholar_fetch.chain._text_cache (a private helper that tells whether a "
                     "fetched body was a PDF); this scholar_fetch has none: pin a version that does, or add a public "
                     "one") from e


def source_url(f: dict) -> str | None:
    if (a := arxiv_id(f)):
        return f"https://arxiv.org/abs/{a}"
    if (doi := next((v for _, kind, v, _ in identifiers(f) if kind == "crossref" and v), None)):
        return f"https://doi.org/{doi}"
    return f.get("url")


def pdf_flag(got, client: Client) -> bool | None:
    """Whether the served body was a PDF, read from scholar_fetch's text cache; None when it cannot be told."""
    if got.kind != "fulltext":
        return False
    target = next((t["target"] for t in reversed(got.tried) if t["step"] == got.served_by and t["outcome"] == "ok"),
                  None)
    try:
        return bool(json.loads(_text_cache(client, target).read_text())["pdf"]) if target else None
    except (OSError, ValueError, KeyError):
        return None


def clear_stale(out_dir: Path) -> int:
    """A re-run starts clean: old texts would survive a failed re-fetch, and old verdicts were judged on old texts."""
    stale = [*(out_dir / "texts").glob("*.txt"), *out_dir.glob("verdicts-*.jsonl"), out_dir / "verdicts-checked.json"]
    gone = [p for p in stale if p.exists()]
    for p in gone:
        p.unlink()
    return len(gone)


def main() -> None:
    out_dir, bibs, others, _ = split_args(sys.argv[1:])
    if not bibs or others:
        raise SystemExit("usage: fetch_texts.py OUT_DIR refs.bib [more.bib ...]")
    target = fresh_output(out_dir / "fetched.json")
    entries = load_bibs(bibs)
    (out_dir / "texts").mkdir(parents=True, exist_ok=True)
    print(f"cleared {clear_stale(out_dir)} files from a previous run (texts, verdicts)")
    client = Client(cache_dir=out_dir / "http-cache")
    record = {}
    for e in entries:
        url = source_url(e["fields"])
        if not url:
            record[e["key"]] = {"url": None, "served_by": None, "why": "no arXiv id, DOI or URL in the entry"}
            continue
        try:
            got = resolve(url, client)
        except Exception as ex:  # noqa: BLE001 - one unreachable source must not stop the others
            record[e["key"]] = {"url": url, "served_by": None, "why": repr(ex)}
            print(f"{'ERROR':24} {repr(ex)[:80]}  {e['key']}")
            continue
        chars, pdf = len(got.text or ""), pdf_flag(got, client)
        if got.text:
            text_path(out_dir, e["key"]).write_text(got.text)
        record[e["key"]] = got.to_dict() | {"chars": chars, "pdf": pdf,
                                            "text_file": str(text_path(out_dir, e["key"])) if got.text else None,
                                            "landing": not (pdf is True and chars >= MIN_PAPER_CHARS)}
        kind = "paper PDF" if not record[e["key"]]["landing"] else "LANDING/abstract"
        print(f"{(got.served_by or 'FAILED'):24} {chars:>9} chars  {kind:16}  {e['key']}")
    target.write_text(json.dumps(record, indent=1, ensure_ascii=False))
    print(f"{sum(bool(r.get('served_by')) for r in record.values())} of {len(record)} fetched, "
          f"{sum(r.get('landing') is False for r in record.values())} as a paper PDF")


if __name__ == "__main__":
    main()

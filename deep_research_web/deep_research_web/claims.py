# ABOUTME: The opt-in claims check: a headless Claude Code verifier reads the report, checks its load-bearing claims against primary pages, answers structured JSON.
# ABOUTME: The process is injected; the engine validates the answer against a schema and writes verification.json and verification.md into the run.
from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from scholar_fetch.chain import Fetched

from .prefetch import FETCHED_DIR, default_resolver, prefetch
from .report import REPORT_NAME, SOURCES_NAME
from .runs import RunRecord

# NOT_FOUND: the source was fetched and the claim is not on it. UNREACHABLE: nothing could fetch the source.
VERDICTS = ("CONFIRMED", "PARTIALLY", "REFUTED", "NOT_FOUND", "UNREACHABLE")
SERVED_BY = ("arxiv", "unpaywall", "semantic-scholar", "openalex", "direct", "webfetch", "none")
CHAINS = ("served", "failed", "unlisted")
MIN_CLAIMS = 10
VERIFICATION_JSON = "verification.json"
VERIFICATION_MD = "verification.md"
PROMPT_FILE = Path(__file__).with_name("check_claims_prompt.md")
TOOLS = ("Read", "WebFetch", "WebSearch")
METHOD = ("sources pre-fetched by scholar_fetch (arXiv, Unpaywall, Semantic Scholar, OpenAlex, direct); "
          "headless Claude Code, Read/WebFetch/WebSearch, structured output validated by deep-research-web")
REQUIRED_CLAIM_FIELDS = ("claim", "url", "verdict", "supporting_text", "date_seen", "served_by")

VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "url": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "supporting_text": {"type": "string"},
                    "note": {"type": "string"},
                    "date_seen": {"type": "string"},
                    "served_by": {"type": "string"},
                },
                "required": list(REQUIRED_CLAIM_FIELDS),
            },
        },
        "summary": {
            "type": "object",
            "properties": {
                "refuted_or_materially_different": {"type": "array", "items": {"type": "string"}},
                "unreachable": {"type": "array", "items": {"type": "string"}},
                "most_consequential": {"type": "string"},
            },
            "required": ["refuted_or_materially_different", "unreachable", "most_consequential"],
        },
    },
    "required": ["claims", "summary"],
}


class ClaimsError(Exception):
    pass


def build_prompt(question: str, report: str, sources: str, today: str, fetched: str) -> str:
    # str.replace, not str.format: a report is full of braces the template must not interpret.
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return (template.replace("{today}", today).replace("{question}", question)
            .replace("{sources}", sources.strip() or "(none listed)").replace("{report}", report)
            .replace("{fetched}", fetched))


def fetched_listing(index: dict[int, dict]) -> str:
    """One line per pre-fetched source, for the prompt: what served it, or what was tried and failed.
    A failed attempt's target (a PMC URL from OpenAlex, a DOI landing page...) is shown too, so the
    verifier has somewhere else to try besides the cited URL."""
    if not index:
        return "(no sources listed)"
    lines = []
    for n in sorted(index):
        f = index[n]
        if f.get("served_by"):
            lines.append(f"{n}. {f['url']} → {FETCHED_DIR}/{n}.txt ({f['served_by']}, {f.get('kind')})")
        else:
            tried = "; ".join(f"{t['step']}: {t['outcome']}" + (f" ({t['target']})" if t.get("target") else "")
                              for t in f.get("tried") or [])
            lines.append(f"{n}. {f['url']} → not fetched; tried: {tried}")
    return "\n".join(lines)


def validate_verification(data) -> list[str]:
    if not isinstance(data, dict):
        return ["result is not a JSON object"]
    errors: list[str] = []
    claims = data.get("claims")
    if not isinstance(claims, list):
        errors.append("no claims list")
        claims = []
    if len(claims) < MIN_CLAIMS:
        errors.append(f"only {len(claims)} claims; at least {MIN_CLAIMS} expected")
    for i, c in enumerate(claims, 1):
        if not isinstance(c, dict):
            errors.append(f"claim {i}: not an object")
            continue
        for k in REQUIRED_CLAIM_FIELDS:
            if not isinstance(c.get(k), str) or (k in ("claim", "url", "verdict") and not c[k].strip()):
                errors.append(f"claim {i}: missing {k}")
        if c.get("verdict") not in VERDICTS:
            errors.append(f"claim {i}: verdict {c.get('verdict')!r} is not one of {', '.join(VERDICTS)}")
    summary = data.get("summary")
    if not isinstance(summary, dict):
        errors.append("no summary")
    else:
        for k in ("refuted_or_materially_different", "unreachable"):
            if not isinstance(summary.get(k), list):
                errors.append(f"summary: {k} missing")
        if not isinstance(summary.get("most_consequential"), str):
            errors.append("summary: most_consequential missing")
    return errors


def verdict_counts(claims: list[dict]) -> str:
    counts = Counter(c.get("verdict") for c in claims)
    return ", ".join(f"{counts[v]} {v}" for v in VERDICTS if counts[v]) or "no claims"


def cross_check(claims: list[dict], index: dict[int, dict]) -> list[dict]:
    """What the pre-fetch did for each claim's URL, recorded by us rather than reported by the model:
    chain is served / failed (the chain found no text) / unlisted (not a source in sources.md, exact match).
    A served_by outside the known steps is not trusted; it becomes "unrecorded"."""
    by_url = {f.get("url"): f for f in index.values()}
    checked = []
    for c in claims:
        f = by_url.get(c.get("url"))
        chain = "unlisted" if f is None else "served" if f.get("served_by") else "failed"
        served_by = c.get("served_by") if c.get("served_by") in SERVED_BY else "unrecorded"
        checked.append({**c, "served_by": served_by, "chain": chain})
    return checked


def chain_counts(claims: list[dict]) -> str:
    """Verdicts per chain value, e.g. "served: 2 CONFIRMED; failed: 1 UNREACHABLE"; "" when no claim has one."""
    if not any("chain" in c for c in claims):
        return ""
    parts = []
    for chain in CHAINS:
        subset = [c for c in claims if c.get("chain") == chain]
        if subset:
            parts.append(f"{chain}: {verdict_counts(subset)}")
    return "; ".join(parts)


def _cell(text) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def render_verification_md(data: dict, run_id: str, model: str, today: str) -> str:
    claims, summary = data["claims"], data["summary"]
    rows = [
        f"| {i} | {c['verdict']} | {_cell(c['claim'])} | {_cell(c['url'])} | {_cell(c.get('served_by', ''))} | "
        f"{_cell(c.get('chain'))} | {_cell(c['supporting_text'])} | {_cell(c.get('note'))} |"
        for i, c in enumerate(claims, 1)
    ]
    bullets = lambda items: "\n".join(f"- {x}" for x in items) or "- (none)"
    served_counts = Counter(c.get("served_by") or "unrecorded" for c in claims)
    served_str = ", ".join(f"{n} {k}" for k, n in served_counts.items()) or "no claims"
    chains = chain_counts(claims)  # absent from a verification written before the cross-check existed
    chain_line = f"By pre-fetch chain: {chains}.\n" if chains else ""
    return (
        f"# Claims check of {run_id}\n\n"
        f"Checked on {today} by {model} ({METHOD}). Verdicts: {verdict_counts(claims)}.\n"
        f"Served by: {served_str}.\n{chain_line}\n"
        "A CONFIRMED claim was found verbatim on a primary page; NOT_FOUND means the source was fetched but does "
        "not carry the claim; UNREACHABLE means nothing could fetch it. Chain is what the pre-fetch did for the "
        "claim's URL (served, failed, or unlisted in sources.md). Nothing here reproduces a result.\n\n"
        "| # | Verdict | Claim | Source | Served by | Chain | Supporting text | Note |\n"
        "|---|---|---|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n\n"
        f"## Refuted or materially different\n\n{bullets(summary['refuted_or_materially_different'])}\n\n"
        f"## Unreachable\n\n{bullets(summary['unreachable'])}\n\n"
        f"## Most consequential\n\n{summary['most_consequential'].strip() or '(none)'}\n"
    )


def _write_atomic(target: Path, text: str) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def check_claims(
    rec: RunRecord, model: str, runner: Callable = subprocess.run, claude_bin: str = "claude",
    timeout_s: int = 3600, today: Callable[[], date] = date.today,
    resolver: Callable[[str], Fetched] | None = None, log: Callable[[str], None] | None = None,
) -> Path:
    """Run the headless verifier on a collected run; write verification.json and .md; return the JSON path."""
    out = Path(rec.out_dir)
    report = (out / REPORT_NAME).read_text(encoding="utf-8")
    sources = (out / SOURCES_NAME).read_text(encoding="utf-8") if (out / SOURCES_NAME).exists() else ""
    try:
        index = prefetch(out, sources, resolver or default_resolver(), log=log)
    except Exception as exc:
        # A resolver reads and writes the filesystem and the network; an unexpected failure there
        # (a full disk, a broken resolver) is reported like every other verifier failure, not a traceback.
        raise ClaimsError(f"pre-fetch failed: {exc}") from exc
    day = today().isoformat()
    prompt = build_prompt(rec.question, report, sources, day, fetched=fetched_listing(index))
    # --restricted confines Read to the run dir (cwd) and ignores user settings, including
    # defaultMode: auto: the verifier reads untrusted third-party text and must not be able to
    # read a local secret and exfiltrate it through WebFetch (R13). The variadic tool lists go
    # last so they cannot swallow another flag; Write and Bash are never offered.
    # --strict-mcp-config with no --mcp-config loads no MCP server at all: the account's mail, chat
    # and task servers can send things, and nothing that reads untrusted text may hold a way to send.
    argv = [
        claude_bin, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(VERIFICATION_SCHEMA),
        "--model", model, "--no-session-persistence", "--restricted", "--permission-mode", "dontAsk",
        "--strict-mcp-config", "--allowedTools", *TOOLS, "--tools", *TOOLS,
    ]
    try:
        proc = runner(argv, cwd=str(out), capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise ClaimsError(f"timed out after {timeout_s // 60} min") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise ClaimsError(f"claude finished with exit {proc.returncode}: {tail or 'no output'}")
    try:
        envelope = json.loads(proc.stdout)
    except ValueError as exc:
        raise ClaimsError(f"no JSON envelope on stdout: {proc.stdout[:200]!r}") from exc
    if not isinstance(envelope, dict):
        raise ClaimsError("the envelope is not a JSON object")
    if envelope.get("is_error"):
        raise ClaimsError(f"claude reported is_error: {str(envelope.get('result'))[:300]}")
    structured = envelope.get("structured_output")
    if structured is None:
        raise ClaimsError("no structured_output in the envelope; the verifier did not answer the schema")
    errors = validate_verification(structured)
    if errors:
        raise ClaimsError("invalid verification: " + "; ".join(errors))
    data = {
        "meta": {"run_id": rec.run_id, "verified_on": day, "model": model, "method": METHOD},
        "claims": cross_check(structured["claims"], index), "summary": structured["summary"],
    }
    _write_atomic(out / VERIFICATION_JSON, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    _write_atomic(out / VERIFICATION_MD, render_verification_md(data, rec.run_id, model, day))
    return out / VERIFICATION_JSON

# ABOUTME: Tests for the opt-in claims check: prompt content, structured-output validation, the files it writes, the CLI wiring.
# ABOUTME: The headless Claude process is a stand-in runner returning a canned envelope; everything around it is real.
import json
import subprocess
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from scholar_fetch.chain import Fetched
from scholar_fetch.ids import Ids

from deep_research_web import __main__ as cli
from deep_research_web.__main__ import EXIT_OK, EXIT_PROBLEM
from deep_research_web.claims import (
    VERDICTS, VERIFICATION_SCHEMA, ClaimsError, build_prompt, check_claims, render_verification_md,
    validate_verification,
)
from deep_research_web.config import Config
from deep_research_web.runs import RunRecord, write_run

GOOD = {
    "claims": [
        {"claim": f"claim {i}", "url": f"https://example.org/{i}", "verdict": "CONFIRMED",
         "supporting_text": "verbatim text", "note": "", "date_seen": "2026-09-19", "served_by": "arxiv"}
        for i in range(12)
    ] + [{"claim": "SRPO 82.1 is zero-shot", "url": "https://arxiv.org/abs/2511.15605", "verdict": "REFUTED",
          "supporting_text": "With Augmented Data", "note": "augmented, not zero-shot", "date_seen": "2026-09-19",
          "served_by": "arxiv"}],
    "summary": {"refuted_or_materially_different": ["SRPO 82.1 is augmented-data"], "unreachable": [],
                "most_consequential": "the headline number is not zero-shot"},
}


def _resolver(url):
    return Fetched(url, "SRPO reaches 82.1 With Augmented Data", "arxiv", "fulltext", Ids(arxiv="2511.15605"), [])


def _envelope(structured, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "structured_output": structured,
                       "result": json.dumps(structured), "total_cost_usd": 0.5, "num_turns": 7})


def _collected_run(tmp_path, run_id="2026-09-17-211003-what-changed") -> RunRecord:
    out = tmp_path / "runs" / run_id
    out.mkdir(parents=True)
    (out / "charter.md").write_text("# What changed?\n")
    (out / "report.md").write_text("# Report\n\nSRPO reaches 82.1 on LIBERO-Plus zero-shot [1].\n")
    (out / "sources.md").write_text("# Sources\n\n1. https://arxiv.org/abs/2511.15605\n")
    (out / "run-result.json").write_text(json.dumps({"status": "complete", "sources": []}))
    rec = RunRecord(run_id=run_id, question="What changed?", status="done", org_uuid="o", conversation_uuid="c",
                    chat_url="https://claude.ai/chat/c", model="claude-fable-5-1", charter=str(out / "charter.md"),
                    out_dir=str(out), started_at="2026-09-17T21:10:03", collected_at="2026-09-17T21:40:00", name="changelog")
    write_run(out, rec)
    return rec


class Runner:
    """Stand-in for subprocess.run: records the argv and cwd, answers with a canned envelope."""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.returncode, self.calls = stdout, returncode, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="")


def test_prompt_carries_the_report_the_rules_and_the_verdicts():
    prompt = build_prompt("What changed?", "# Report\n\nSRPO reaches 82.1 [1].", "1. https://a\n", "2026-09-19",
                          fetched="1. https://a → fetched/1.txt (arxiv, fulltext)")
    assert "SRPO reaches 82.1 [1]." in prompt and "https://a" in prompt and "What changed?" in prompt and "2026-09-19" in prompt
    assert "fetched/1.txt" in prompt
    for verdict in VERDICTS:
        assert verdict in prompt
    for rule in ("primary", "never download", "weights", "datasets", "read-only", "did not write"):
        assert rule in prompt, rule


def test_schema_pins_the_verdicts_and_required_fields():
    item = VERIFICATION_SCHEMA["properties"]["claims"]["items"]
    assert item["properties"]["verdict"]["enum"] == list(VERDICTS)
    assert set(item["required"]) >= {"claim", "url", "verdict", "supporting_text", "date_seen"}
    assert set(VERIFICATION_SCHEMA["required"]) == {"claims", "summary"}


def test_validate_accepts_a_good_result_and_names_each_problem():
    assert validate_verification(GOOD) == []
    assert any("claims" in e for e in validate_verification({"summary": GOOD["summary"]}))
    bad_verdict = json.loads(json.dumps(GOOD))
    bad_verdict["claims"][0]["verdict"] = "MAYBE"
    assert any("MAYBE" in e for e in validate_verification(bad_verdict))
    few = {"claims": GOOD["claims"][:3], "summary": GOOD["summary"]}
    assert any("3 claims" in e for e in validate_verification(few))


def test_render_md_has_one_row_per_claim_and_the_summary():
    md = render_verification_md(GOOD, "2026-09-17-211003-what-changed", "claude-fable-5-1", "2026-09-19")
    assert md.count("| CONFIRMED |") == 12 and "| REFUTED |" in md
    assert "12 CONFIRMED, 1 REFUTED" in md and "SRPO 82.1 is augmented-data" in md and "the headline number is not zero-shot" in md


def test_check_claims_runs_headless_claude_in_the_run_dir_and_writes_both_files(tmp_path):
    rec = _collected_run(tmp_path)
    runner = Runner(_envelope(GOOD))
    written = check_claims(rec, "claude-fable-5-1", runner=runner, claude_bin="/bin/claude", resolver=_resolver,
                           today=lambda: date(2026, 9, 19))
    out = Path(rec.out_dir)
    assert written == out / "verification.json"
    data = json.loads(written.read_text())
    assert data["meta"] == {
        "run_id": rec.run_id, "verified_on": "2026-09-19", "model": "claude-fable-5-1",
        "method": "sources pre-fetched by scholar_fetch (arXiv, Unpaywall, Semantic Scholar, OpenAlex, direct); "
                   "headless Claude Code, Read/WebFetch/WebSearch, structured output validated by deep-research-web",
    }
    assert [{k: v for k, v in c.items() if k != "chain"} for c in data["claims"]] == GOOD["claims"]
    assert [c["chain"] for c in data["claims"]] == ["unlisted"] * 12 + ["served"]  # example.org is not a source
    assert data["summary"] == GOOD["summary"]
    assert (out / "verification.md").read_text().count("| CONFIRMED |") == 12
    [(argv, kw)] = runner.calls
    assert argv[0] == "/bin/claude" and "-p" in argv and "--json-schema" in argv and "--model" in argv
    assert kw["cwd"] == str(out) and kw["timeout"] > 0
    prompt = argv[argv.index("-p") + 1]
    assert "SRPO reaches 82.1 on LIBERO-Plus zero-shot [1]." in prompt
    tools = argv[argv.index("--tools") + 1:argv.index("--tools") + 4]
    assert tools == ["Read", "WebFetch", "WebSearch"]
    assert "Write" not in argv and "Bash" not in argv


def test_check_claims_restricts_permissions_since_it_reads_untrusted_sources(tmp_path):
    rec = _collected_run(tmp_path)
    runner = Runner(_envelope(GOOD))
    check_claims(rec, "m", runner=runner, resolver=_resolver)
    [(argv, kw)] = runner.calls
    assert "--restricted" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    # no MCP server (mail, chat, tasks) is loaded into a process that reads untrusted text; the flag
    # sits before the variadic tool lists, which would otherwise swallow it
    assert "--strict-mcp-config" in argv and argv.index("--strict-mcp-config") < argv.index("--allowedTools")


def test_check_claims_wraps_an_unexpected_prefetch_error(tmp_path):
    rec = _collected_run(tmp_path)
    (Path(rec.out_dir) / "fetched").write_text("a file where the fetched/ folder goes")  # the run dir itself is broken

    with pytest.raises(ClaimsError, match="pre-fetch failed"):
        check_claims(rec, "m", runner=Runner(_envelope(GOOD)), resolver=_resolver)
    assert not (Path(rec.out_dir) / "verification.json").exists()


def test_a_resolver_raising_for_a_source_still_runs_the_verifier(tmp_path):
    rec = _collected_run(tmp_path)

    def broken(url):
        raise OSError("disk full")

    runner = Runner(_envelope(GOOD))
    check_claims(rec, "m", runner=runner, resolver=broken)
    prompt = runner.calls[0][0][runner.calls[0][0].index("-p") + 1]
    assert "not fetched" in prompt and "prefetch: error: OSError" in prompt


def test_check_claims_refuses_an_error_envelope_and_writes_nothing(tmp_path):
    rec = _collected_run(tmp_path)
    with pytest.raises(ClaimsError, match="is_error"):
        check_claims(rec, "m", runner=Runner(_envelope(None, is_error=True)), claude_bin="c", resolver=_resolver)
    assert not (Path(rec.out_dir) / "verification.json").exists()


def test_check_claims_refuses_an_invalid_result(tmp_path):
    rec = _collected_run(tmp_path)
    few = {"claims": GOOD["claims"][:2], "summary": GOOD["summary"]}
    with pytest.raises(ClaimsError, match="2 claims"):
        check_claims(rec, "m", runner=Runner(_envelope(few)), claude_bin="c", resolver=_resolver)


def test_check_claims_refuses_a_failed_process(tmp_path):
    rec = _collected_run(tmp_path)
    with pytest.raises(ClaimsError, match="exit 1"):
        check_claims(rec, "m", runner=Runner("", returncode=1), claude_bin="c", resolver=_resolver)


def test_check_claims_command_re_archives_so_the_library_carries_the_verification(tmp_path, capsys):
    rec = _collected_run(tmp_path)
    lib = tmp_path / "lib"
    cfg = Config("claude-fable-5-1", None, tmp_path / "runs", tmp_path / "p", tmp_path / "ps", archive_root=lib)
    code = cli.cmd_check_claims(Namespace(run_id=rec.run_id, model=None, timeout=30), cfg,
                                runner=Runner(_envelope(GOOD)), resolver=_resolver)
    assert code == EXIT_OK
    dest = lib / "2026-09-17-changelog"
    assert (dest / "verification.json").exists() and json.loads((dest / "archive.json").read_text())["checked"] is True
    assert (dest / "fetched.json").exists()
    out = capsys.readouterr().out
    assert "12 CONFIRMED, 1 REFUTED" in out and "SRPO 82.1 is augmented-data" in out
    assert "sources served by:" in out and "arxiv" in out


def test_check_claims_command_reports_a_bad_result_as_a_problem(tmp_path, capsys):
    rec = _collected_run(tmp_path)
    cfg = Config("claude-fable-5-1", None, tmp_path / "runs", tmp_path / "p", tmp_path / "ps")
    code = cli.cmd_check_claims(Namespace(run_id=rec.run_id, model=None, timeout=30), cfg,
                                runner=Runner("", returncode=1), resolver=_resolver)
    assert code == EXIT_PROBLEM and "exit 1" in capsys.readouterr().out


def test_check_claims_parser():
    args = cli.build_parser().parse_args(["check-claims", "r1", "--model", "claude-opus-5", "--timeout", "90"])
    assert args.run_id == "r1" and args.model == "claude-opus-5" and args.timeout == 90
    assert cli.build_parser().parse_args(["check-claims", "r1"]).timeout == 60


def test_prefetch_runs_before_the_verifier_and_the_prompt_points_at_it(tmp_path):
    rec = _collected_run(tmp_path)
    seen = {}

    def runner(argv, **kw):
        seen["argv"], seen["cwd"] = argv, kw["cwd"]
        assert (Path(kw["cwd"]) / "fetched" / "1.txt").exists()  # written before claude starts
        return subprocess.CompletedProcess(argv, 0, _envelope(GOOD), "")

    check_claims(rec, "m", runner=runner, resolver=_resolver, today=lambda: date(2026, 9, 27))
    prompt = seen["argv"][seen["argv"].index("-p") + 1]
    assert "fetched/1.txt" in prompt and "arxiv" in prompt
    assert "Read" in seen["argv"][seen["argv"].index("--tools") + 1:]


def test_served_by_is_required_and_shown():
    bad = json.loads(json.dumps(GOOD))
    del bad["claims"][0]["served_by"]
    assert any("served_by" in e for e in validate_verification(bad))
    md = render_verification_md({**GOOD, "meta": {}}, "r", "m", "2026-09-27")
    assert "| Served by |" in md and "arxiv" in md


def test_an_old_verification_without_served_by_still_renders():
    old = json.loads(json.dumps(GOOD))
    for c in old["claims"]:
        del c["served_by"]
    assert "CONFIRMED" in render_verification_md(old, "r", "m", "2026-09-19")


def test_a_failed_prefetch_source_is_listed_as_not_fetched_in_the_prompt(tmp_path):
    rec = _collected_run(tmp_path)
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, _envelope(GOOD), "")

    dead = lambda u: Fetched(u, None, None, None, Ids(), [{"step": "direct", "target": u, "outcome": "http 403"}])
    check_claims(rec, "m", runner=runner, resolver=dead)
    prompt = seen["argv"][seen["argv"].index("-p") + 1]
    assert "not fetched" in prompt and "http 403" in prompt


def _claim(url, verdict="CONFIRMED", served_by="arxiv"):
    return {"claim": f"about {url}", "url": url, "verdict": verdict, "supporting_text": "t", "note": "",
            "date_seen": "2026-09-27", "served_by": served_by}


def test_not_found_is_a_verdict_distinct_from_unreachable():
    assert "NOT_FOUND" in VERDICTS and "UNREACHABLE" in VERDICTS
    prompt = build_prompt("q", "r", "s", "2026-09-27", fetched="f")
    assert "NOT_FOUND" in prompt
    ok = {**GOOD, "claims": GOOD["claims"] + [_claim("https://a", "NOT_FOUND")]}
    assert validate_verification(ok) == []
    assert "1 NOT_FOUND" in render_verification_md(ok, "r", "m", "2026-09-27")


def test_prompt_sends_a_failed_source_to_one_webfetch():
    prompt = build_prompt("q", "r", "s", "2026-09-27", fetched="f")
    assert "not fetched" in prompt and "ONE WebFetch" in prompt and "webfetch" in prompt
    assert "Secondary summaries" in prompt and "do not confirm anything" in prompt


def test_each_claim_is_cross_checked_against_the_prefetch(tmp_path):
    rec = _collected_run(tmp_path)
    out = Path(rec.out_dir)
    (out / "sources.md").write_text("# Sources\n\n1. https://arxiv.org/abs/2511.15605\n2. https://www.npmjs.com/package/x\n")
    claims = [_claim(f"https://example.org/{i}") for i in range(10)] + [
        _claim("https://arxiv.org/abs/2511.15605"),
        _claim("https://www.npmjs.com/package/x", "UNREACHABLE", "none"),
        _claim("https://arxiv.org/abs/2511.15605", served_by="the-moon"),
    ]

    def resolver(url):
        if "npmjs" in url:
            return Fetched(url, None, None, None, Ids(), [{"step": "direct", "target": url, "outcome": "http 403"}])
        return _resolver(url)

    written = check_claims(rec, "m", runner=Runner(_envelope({**GOOD, "claims": claims})), resolver=resolver)
    got = json.loads(written.read_text())["claims"]
    assert [c["chain"] for c in got[9:]] == ["unlisted", "served", "failed", "served"]
    assert got[-1]["served_by"] == "unrecorded" and got[-2]["served_by"] == "none"
    md = (out / "verification.md").read_text()
    assert "| Chain |" in md and "| failed |" in md
    assert "By pre-fetch chain: served: 2 CONFIRMED; failed: 1 UNREACHABLE; unlisted: 10 CONFIRMED." in md
    assert "1 unrecorded" in md


def test_an_old_verification_without_chain_still_renders():
    md = render_verification_md(GOOD, "r", "m", "2026-09-19")
    assert md.count("| CONFIRMED |") == 12 and "By pre-fetch chain" not in md


def test_check_claims_command_prints_one_line_per_prefetched_source(tmp_path, capsys):
    rec = _collected_run(tmp_path)
    cfg = Config("claude-fable-5-1", None, tmp_path / "runs", tmp_path / "p", tmp_path / "ps")
    cli.cmd_check_claims(Namespace(run_id=rec.run_id, model=None, timeout=30), cfg,
                         runner=Runner(_envelope(GOOD)), resolver=_resolver)
    assert "  [1/1] arxiv https://arxiv.org/abs/2511.15605" in capsys.readouterr().out

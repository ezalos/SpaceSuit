# ABOUTME: Tests for the archive: a collected run copied under an explicit name into a library directory, with an index.
# ABOUTME: Real files in tmp_path throughout; nothing here touches git, the network or the real runs root.
import json
from pathlib import Path

import pytest

from deep_research_web.archive import (
    ARCHIVE_FILE, ArchiveError, archive_dirname, archive_run, default_name, valid_name, write_index,
)
from deep_research_web.runs import RunRecord, write_run


def _run(root: Path, run_id="2026-09-17-165925-libero-plus-vs-libero-pro-which-robustne", name=None, **over) -> RunRecord:
    out = root / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "charter.md").write_text("# Which robustness benchmark?\n\n## Must answer\n- a\n")
    (out / "report.md").write_text("# LIBERO-Plus vs LIBERO-PRO\n\nTarget Plus first [1].\n")
    (out / "sources.md").write_text("# Sources\n\n1. https://example.org/a\n")
    (out / "run-result.json").write_text(json.dumps({
        "status": "complete", "sources_total": 3, "sources_verified": 1, "unanswered": [], "unverified": [],
        "sources": [
            {"n": 1, "url": "https://example.org/a", "quote": "x", "grade": "quoted", "detail": "quote found on the page"},
            {"n": 2, "url": "https://example.org/b", "quote": "", "grade": "live", "detail": "page resolves"},
            {"n": 3, "url": "https://example.org/c", "quote": "", "grade": "dead", "detail": "HTTP 404"},
        ],
    }))
    (out / "conversation.json").write_text('{"uuid": "c", "settings": {}, "chat_messages": []}')
    fields = dict(
        run_id=run_id, question="LIBERO-Plus vs LIBERO-PRO: which robustness benchmark should a pi0.5-based program target?",
        status="done", org_uuid="org-secret", conversation_uuid="c", chat_url="https://claude.ai/chat/c",
        model="claude-fable-5-1", charter=str(out / "charter.md"), out_dir=str(out),
        started_at="2026-09-17T16:59:25", collected_at="2026-09-17T17:12:00", account="work2", name=name,
    )
    fields.update(over)
    rec = RunRecord(**fields)
    write_run(out, rec)
    return rec


def test_valid_name_is_lowercase_kebab_at_most_60_chars():
    assert valid_name("libero-plus-vs-pro-requirements")
    assert valid_name("yam2")
    assert not valid_name("Libero Plus")
    assert not valid_name("-leading")
    assert not valid_name("a--b")
    assert not valid_name("x" * 61)
    assert not valid_name("")


def test_default_name_slugs_the_question_at_60_chars():
    q = "Best methods on LIBERO-Plus and LIBERO-PRO today, and newer robustness ideas from other benchmarks"
    name = default_name(q)
    assert name == "best-methods-on-libero-plus-and-libero-pro-today-and-newer-r"
    assert len(name) <= 60 and valid_name(name)


def test_archive_dirname_is_the_launch_day_of_the_run_id_plus_the_name(tmp_path):
    rec = _run(tmp_path, name="libero-plus-vs-pro-requirements")
    assert archive_dirname(rec) == "2026-09-17-libero-plus-vs-pro-requirements"
    assert archive_dirname(rec, "renamed") == "2026-09-17-renamed"
    assert archive_dirname(_run(tmp_path, run_id="2026-09-17-165926-r2")) == "2026-09-17-libero-plus-vs-libero-pro-which-robustness-benchmark-should"
    # The run id is stamped in local time at launch; started_at is UTC and can already be the next day.
    late = _run(tmp_path, run_id="2026-09-17-171317-best-methods", name="methods", started_at="2026-09-18T00:13:17")
    assert archive_dirname(late) == "2026-09-17-methods"
    # A run id without a date prefix (a foreign or hand-made record) falls back to started_at.
    assert archive_dirname(_run(tmp_path, run_id="r3", name="n", started_at="2026-09-18T00:13:17")) == "2026-09-18-n"


def test_archive_run_copies_the_report_files_and_nothing_private(tmp_path):
    rec = _run(tmp_path, name="libero-plus-vs-pro-requirements")
    dest = archive_run(rec, tmp_path / "lib")
    assert dest == tmp_path / "lib" / "2026-09-17-libero-plus-vs-pro-requirements"
    assert sorted(p.name for p in dest.iterdir()) == ["README.md", "archive.json", "charter.md", "report.md", "run-result.json", "sources.md"]
    assert (dest / "report.md").read_text() == (Path(rec.out_dir) / "report.md").read_text()
    meta = json.loads((dest / ARCHIVE_FILE).read_text())
    assert meta["run_id"] == rec.run_id and meta["name"] == "libero-plus-vs-pro-requirements"
    assert meta["question"] == rec.question and meta["account"] == "work2" and meta["chat_url"] == rec.chat_url
    assert meta["grades"] == {"quoted": 1, "live": 1, "dead": 1}
    assert meta["checked"] is False and meta["engine"] == "claude-web"
    assert "org-secret" not in json.dumps(meta) and str(tmp_path) not in json.dumps(meta)


def test_archive_run_writes_a_readme_once_and_keeps_human_edits(tmp_path):
    rec = _run(tmp_path, name="libero-plus-vs-pro-requirements")
    dest = archive_run(rec, tmp_path / "lib")
    readme = (dest / "README.md").read_text()
    assert readme.startswith("# LIBERO-Plus vs LIBERO-PRO: which robustness benchmark")
    assert "## Used in" in readme and "## Corrections" in readme
    (dest / "README.md").write_text(readme + "\nImported into Alakazam tasks/research.\n")
    (Path(rec.out_dir) / "report.md").write_text("# revised\n")
    archive_run(rec, tmp_path / "lib")
    assert "Imported into Alakazam" in (dest / "README.md").read_text()
    assert (dest / "report.md").read_text() == "# revised\n"


def test_archive_run_carries_a_verification_when_present(tmp_path):
    rec = _run(tmp_path, name="n")
    (Path(rec.out_dir) / "verification.json").write_text('{"claims": []}')
    (Path(rec.out_dir) / "verification.md").write_text("# checked\n")
    dest = archive_run(rec, tmp_path / "lib")
    assert (dest / "verification.json").exists() and (dest / "verification.md").exists()
    assert json.loads((dest / ARCHIVE_FILE).read_text())["checked"] is True


def test_archive_run_refuses_a_directory_owned_by_another_run(tmp_path):
    a = _run(tmp_path, run_id="r-a", name="same")
    b = _run(tmp_path, run_id="r-b", name="same")
    archive_run(a, tmp_path / "lib")
    with pytest.raises(ArchiveError, match="r-a"):
        archive_run(b, tmp_path / "lib")


def test_archive_run_renames_an_existing_archive_of_the_same_run(tmp_path):
    rec = _run(tmp_path, name="first-name")
    old = archive_run(rec, tmp_path / "lib")
    (old / "README.md").write_text("# kept\n")
    new = archive_run(rec, tmp_path / "lib", name="second-name")
    assert not old.exists() and new.name == "2026-09-17-second-name"
    assert (new / "README.md").read_text() == "# kept\n"
    assert json.loads((new / ARCHIVE_FILE).read_text())["name"] == "second-name"


def test_archive_run_rejects_a_bad_name(tmp_path):
    rec = _run(tmp_path)
    with pytest.raises(ArchiveError, match="name"):
        archive_run(rec, tmp_path / "lib", name="Bad Name")


def test_index_lists_every_archive_newest_first_with_grades_and_papers(tmp_path):
    lib = tmp_path / "lib"
    a = archive_run(_run(tmp_path, run_id="2026-09-17-100000-a", name="alpha", started_at="2026-09-17T17:00:00"), lib)
    b = archive_run(_run(tmp_path, run_id="2026-09-18-100000-b", name="beta", started_at="2026-09-18T17:00:00"), lib)
    (b / "papers").symlink_to("../../Drive/backups/Research/Alakazam/Some folder")
    index = write_index(lib).read_text()
    lines = [l for l in index.splitlines() if l.startswith("| 2026")]
    assert lines[0].startswith("| 2026-09-18 | [beta](2026-09-18-beta/)") and lines[1].startswith("| 2026-09-17 | [alpha](2026-09-17-alpha/)")
    assert "1 quoted, 1 live, 1 dead" in lines[0]
    assert "Some folder" in lines[0] and "Some folder" not in lines[1]


def test_index_skips_a_directory_without_archive_json(tmp_path):
    lib = tmp_path / "lib"
    (lib / "2026-01-01-stray").mkdir(parents=True)
    archive_run(_run(tmp_path, name="alpha"), lib)
    assert "stray" not in (lib / "README.md").read_text()

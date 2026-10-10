# ABOUTME: llm_tells reads every shipped text file under the paper dir (.sty, .tikz, .bbl, .md ...) and every \input,
# ABOUTME: with % comments of TeX-like files reported as public source.
import json

import llm_tells as lt


def test_sty_and_tikz_comments_are_scanned(tmp_path):
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\usepackage{mymacros}\n\\begin{document}\n"
                                       "Clean.\n\\input{figs/fig.weird}\n\\end{document}\n")
    (tmp_path / "mymacros.sty").write_text("% Here is the revised macro file with the changes you asked for.\n")
    (tmp_path / "fig.tikz").write_text("% I have rewritten this figure as requested.\n\\fbox{x}\n")
    (tmp_path / "notes.md").write_text("TODO: fill in\n")
    (tmp_path / "figs").mkdir()
    (tmp_path / "figs" / "fig.weird").write_text("Lorem ipsum dolor.\n")
    hits = lt.scan_paths([tmp_path])
    files = {h["file"] for h in hits if h["level"] == "BLOCKER"}
    assert files == {"mymacros.sty", "fig.tikz", "notes.md", "fig.weird"}
    assert all("public" in h["where"] for h in hits if h["file"] in ("mymacros.sty", "fig.tikz"))


def test_markdown_rules_skip_markdown_files(tmp_path):
    (tmp_path / "refs-check.md").write_text("## Checked references\n\n**liu2023** is fine.\n")
    (tmp_path / "paper.tex").write_text("Clean.\n% **Key point** for later\n")
    hits = lt.scan_paths([tmp_path])
    assert not [h for h in hits if h["file"] == "refs-check.md"]
    assert [h for h in hits if h["file"] == "paper.tex" and h["level"] == "BLOCKER"]
    assert lt.scan("## Revised abstract", "x.pdf", "pdf text")


def test_bundle_limits_the_scan_to_shipped_files(tmp_path, monkeypatch):
    src, bundle, out = tmp_path / "src", tmp_path / "bundle", tmp_path / "out"
    src.mkdir()
    bundle.mkdir()
    (src / "notes.txt").write_text("TODO: ask the co-authors\n")
    (bundle / "paper.tex").write_text("We report [NUMBER] percent.\n")
    monkeypatch.setattr("sys.argv", ["llm_tells.py", str(out), str(src), "--bundle", str(bundle)])
    lt.main()
    hits = json.loads((out / "tells.json").read_text())
    assert {h["file"] for h in hits} == {"paper.tex"}


def test_a_bundle_tarball_is_unpacked_and_scanned(tmp_path, monkeypatch):
    import tarfile
    (tmp_path / "paper.tex").write_text("We report [NUMBER] percent.\n")
    with tarfile.open(tmp_path / "sub.tar.gz", "w:gz") as tar:
        tar.add(tmp_path / "paper.tex", arcname="paper.tex")
    monkeypatch.setattr("sys.argv", ["llm_tells.py", str(tmp_path / "out"), str(tmp_path),
                                     f"--bundle={tmp_path / 'sub.tar.gz'}"])
    lt.main()
    assert {h["file"] for h in json.loads((tmp_path / "out" / "tells.json").read_text())} == {"paper.tex"}

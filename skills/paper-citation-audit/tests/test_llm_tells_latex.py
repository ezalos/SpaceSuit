# ABOUTME: llm_tells: tells wrapped in LaTeX (\emph, ~, \\, zero-width spaces, markdown) and the scan of
# ABOUTME: every .tex and .bib under a directory.
import llm_tells as lt
import pytest

TELLS = ["\\emph{Here} is the revised paragraph with tighter wording.",
         "\\textbf{Sure}, here is the updated introduction.", "Here~is the revised abstract.",
         "Here is the\\\\ revised section.", "**Key changes:** I tightened the wording.",
         "I've made the following changes to the related work.", "Note: this paragraph was rewritten for clarity.",
         "In this version, I removed the redundant sentence.", "Success rate improves to 9\\textbf{X}.4 on average.",
         "Accuracy is X.X on the held-out split.", "We report [Your Institution] and [Insert citation].",
         "Here\u200bis the revised paragraph."]


@pytest.mark.parametrize("line", TELLS)
def test_latex_wrapped_tells_are_blockers(tmp_path, line):
    p = tmp_path / "t.tex"
    p.write_text("Clean first line.\n" + line + "\n")
    hits = [h for h in lt.scan_file(p) if h["level"] == "BLOCKER"]
    assert hits and hits[0]["line"] == 2, line


def test_directory_scan_reads_every_tex_and_bib(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "x.tex").write_text("TODO fill in.\n")
    (tmp_path / "refs.bib").write_text("@misc{k, note={TBD}}\n")
    files = {h["file"] for h in lt.scan_paths([tmp_path])}
    assert files == {"x.tex", "refs.bib"}

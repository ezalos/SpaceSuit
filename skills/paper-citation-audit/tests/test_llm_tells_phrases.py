# ABOUTME: llm_tells: the reviewer's assistant phrases and placeholders, REVIEW for ambiguous ones, and
# ABOUTME: the .bib file scanned.
import llm_tells as lt
import pytest


def write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.mark.parametrize("phrase", ["Below is the revised introduction with the changes applied.",
                                    "I have revised the paragraph to improve clarity.",
                                    "Author: [Your Name], [Your Institution]", "Our method achieves [X]% accuracy.",
                                    "This rewritten version emphasizes the contribution.",
                                    "Of course! Here is an improved abstract.", "Hope this helps!",
                                    "Results are reported in Table 2 (values: XX).", "Feel free to adjust the wording.",
                                    "The accuracy is 9X.4\\% on LIBERO.", "Remember to cite the original paper here.",
                                    "In summary, the revised text above addresses the reviewer comments."])
def test_reviewer_phrases_are_caught(phrase):
    assert lt.scan(phrase, "f", "typeset text"), phrase


def test_ambiguous_phrases_are_review():
    assert {h["level"] for h in lt.scan("Of course, the robot moves. Feel free to use our code.", "f", "t")} == {
        "REVIEW"}


def test_bib_file_is_scanned(tmp_path):
    p = write(tmp_path, "refs.bib", "@misc{k, title={T}, note={TODO: find the venue}}\n")
    assert any(h["level"] == "BLOCKER" for h in lt.scan_file(p))

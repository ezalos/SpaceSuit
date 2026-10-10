# ABOUTME: llm_tells must catch a meta-comment wrapped over lines, a tell hidden in a % comment (shipped in the arXiv
# ABOUTME: source), the broadened phrases, and grade ambiguous words REVIEW rather than BLOCKER.
from pathlib import Path

import llm_tells as lt
import pytest

FIXTURES_T1 = Path(__file__).resolve().parent / "fixtures" / "t1-main.tex"


def tells(tmp_path, tex, name="paper.tex"):
    p = tmp_path / name
    p.write_text(tex)
    return lt.scan_file(p)


def test_wrapped_meta_comment_is_found_with_its_line(tmp_path):
    hits = tells(tmp_path, "First line.\nWould you\n  like me to expand this section?\n")
    h = [h for h in hits if h["kind"] == "assistant meta-comment"]
    assert h and h[0]["level"] == "BLOCKER" and h[0]["line"] == 2


def test_comment_only_tell_is_reported_as_public_source(tmp_path):
    hits = tells(tmp_path, "Clean text.\n% Here is the revised paragraph you asked for.\n")
    assert len(hits) >= 1
    h = next(h for h in hits if h["kind"] == "assistant meta-comment")
    assert "public in the arXiv source" in h["where"] and h["line"] == 2 and h["level"] == "BLOCKER"


def test_hidden_iffalse_block_is_scanned(tmp_path):
    hits = tells(tmp_path, "Text.\n\\iffalse\nTODO rewrite\n\\fi\n")
    assert any(h["kind"] == "todo left in text" and "public" in h["where"] for h in hits)


@pytest.mark.parametrize("phrase", ["As a large language model, I", "Here are the key points of the draft",
                                    "Sure! Below is the section", "as of my last knowledge update",
                                    "I apologize for the confusion", "[INSERT RESULT HERE]", "reaches XX.X% success",
                                    "reaches X.X\\% success", "todo: check", "tbd", "Fixme",
                                    "as shown by (author?)", "as shown in (?)"])
def test_broadened_patterns_are_blockers(tmp_path, phrase):
    hits = tells(tmp_path, f"Text {phrase} end.\n")
    assert any(h["level"] == "BLOCKER" for h in hits), phrase


@pytest.mark.parametrize("word", ["an illustrative example", "a placeholder token", "a hypothetical robot"])
def test_ambiguous_words_are_review_not_blocker(tmp_path, word):
    hits = tells(tmp_path, f"We give {word}.\n")
    assert hits and all(h["level"] == "REVIEW" for h in hits)


def test_clean_text_has_no_hits(tmp_path):
    assert tells(tmp_path, "We train a policy on 50 demonstrations \\citep{k}. It reaches 79.6\\% success.\n") == []


@pytest.mark.parametrize("line", ["As shown by Smith et al. (2023), diffusion policies win.",
                                  "Prior work reports 79.6\\% success (Liu et al., 2023).",
                                  "This holds (Brown and Lee, 2021b) in general.", "Smith et al.~2023 show it."])
def test_a_citation_written_as_plain_text_is_a_blocker(tmp_path, line):
    hits = [h for h in tells(tmp_path, line + "\n") if h["kind"] == "citation as plain text: never audited"]
    assert hits and hits[0]["level"] == "BLOCKER"


def test_a_bracket_number_is_review_outside_math_only(tmp_path):
    hits = tells(tmp_path, "Prior work [12] reports it. The set $[3]$ and \\[ [4] \\] are math.\n")
    assert [h["match"] for h in hits if h["kind"] == "numbered citation as plain text"] == ["[12]"]


def test_plain_text_citation_rules_skip_the_pdf_and_comments(tmp_path):
    hits = lt.scan("Smith et al. (2023) say so.", "p.pdf", "pdf text")
    hits += tells(tmp_path, "Clean.\n% Smith et al. (2023) say so.\n")
    assert not [h for h in hits if "plain text" in h["kind"]]




def plain_hits(line):
    rules = [(lv, k, p) for k, (lv, p) in lt.PLAIN_CITATION.items()]
    return lt.scan(line, "x.tex", "typeset text", rules) + lt.et_al_without_cite(line, "x.tex")


@pytest.mark.parametrize("line", ["Chi and Song (2023) report 70.1.", "Chi \\& Song (2023) report it.",
                                  "Smith (2023) shows it.", "This was shown (Smith 2023).",
                                  "This was shown (Smith and Jones 2023).", "Brown et al.'s (2020) GPT-3 is large.",
                                  "This holds, as in Zhao et al., RSS 2023.",
                                  "The benchmark (Liu et al. NeurIPS 2023).",
                                  "As reported by OpenAI (2023), it works.", "Smith et al. [2023] show it.",
                                  "Smith et al.\\ (2023) show it."])
def test_each_plain_text_citation_form_is_a_blocker(line):
    assert any(h["level"] == "BLOCKER" for h in plain_hits(line)), line


def test_et_al_without_a_cite_command_is_review():
    hits = plain_hits("Vaswani et al. show that attention suffices.")
    assert [h["level"] for h in hits] == ["REVIEW"]
    assert not plain_hits("Vaswani et al.~\\citep{v} show that attention suffices.")


def test_plain_text_forms_in_a_tex_file_report_their_line(tmp_path):
    hits = [h for h in tells(tmp_path, "\\begin{document}\nClean.\nSmith (2023) shows it.\n\\end{document}\n")
            if "plain text" in h["kind"]]
    assert hits and hits[0]["line"] == 3


@pytest.mark.parametrize("line", ["Published: arXiv 2510.13626v3, Table 2.", "as reported in arXiv:2306.03310.",
                                  "see https://arxiv.org/abs/2306.03310 for details.",
                                  "(doi:10.1038/s41586-021-03819-2)", "at https://doi.org/10.1038/x."])
def test_an_identifier_in_text_is_review(line):
    hits = [h for h in plain_hits(line) if h["kind"] == "identifier in text, not a \\cite: not looked up"]
    assert hits and hits[0]["level"] == "REVIEW"


@pytest.mark.parametrize("line", ["Smith et al.~\\citeyear{x} show it.", "In their 2023 paper, Smith et al. show it."])
def test_author_names_beside_citeyear_or_no_cite_are_review(line):
    assert any(h["level"] == "REVIEW" and "et al" in h["kind"] for h in plain_hits(line)), line


def test_name_and_colleagues_with_a_year_is_a_blocker():
    assert any(h["level"] == "BLOCKER" for h in plain_hits("Liu and colleagues (2023) report 90.1."))


TELLS8 = ["See [citation] for details.", "as shown in [CITATION].", "prior work [Reference] shows", "[ADD REF]",
          "[ref needed]", "[NEEDS CITATION]", "(add citation)", "INSERT CITATION HERE", "<citation>",
          "Jane Doe, [Affiliation]", "[Institution Name]", "Contact: [Email]", "accuracy of [NUMBER]%",
          "reaches [N]\\% on", "reaches [value] percent", "reaches XX.X\\%", "reaches 8X\\%", "[Author 1]",
          "Okay, here's a tighter version:", "Changes made: shortened the intro.", "Summary of changes",
          "As requested, the abstract is now shorter.", "Note: I kept the numbers unchanged.",
          "I've kept the original numbers.", "Here's the updated paragraph", "Let me know if you need",
          "This version tightens the argument.", "[Add figure here]", "[Figure 3 placeholder]",
          "(numbers to be confirmed)", "results pending", "[TODO: verify]", "Anonymous Authors",
          "\\textbf{Key changes:}", "**Key changes**", "## Revised abstract", "Revised version:",
          "Great question!", "Absolutely! Here is", "Sure, here is the revised abstract"]


@pytest.mark.parametrize("line", TELLS8)
def test_every_review8_form_is_caught_in_pdf_text(line):
    assert lt.scan(line, "x.pdf", "pdf text"), line


def test_a_bracketed_phrase_is_review_in_pdf_text_only():
    assert [h["level"] for h in lt.scan("see [Some Words Here] now", "x.pdf", "pdf text")] == ["REVIEW"]
    assert not lt.scan("see [Some Words Here] now", "x.tex", "typeset text")


@pytest.mark.parametrize("line", ["tightened the wording (citation needed) and kept it",
                                  "We report <insert number> runs.",
                                  "Revised abstract: we study robots.", "Rewritten introduction: robots.",
                                  "If you'd like, I can also shorten it.", "If you want, I can tighten this.",
                                  "I don't have access to the full text.", "Want me to also revise the conclusion?",
                                  "Let me know whether this works.", "The following is a revised draft."])
def test_review9_meta_comments_are_blockers(line):
    assert any(h["level"] == "BLOCKER" for h in lt.scan(line, "x.tex", "typeset text")), line


@pytest.mark.parametrize("line", ["I made the following changes:", "I've also added a sentence on limitations.",
                                  "I tightened the wording and removed redundancy.",
                                  "Note that I have not changed any numbers."])
def test_a_first_person_sentence_is_review_in_typeset_text_and_pdf(line):
    for where in ("typeset text", "pdf text"):
        assert lt.scan(line, "x", where), (line, where)
    assert not [h for h in lt.scan(line, "x.md", "text") if "first person" in h["kind"]]


def test_the_t1_tree_fails_closed(tmp_path):
    p = tmp_path / "main.tex"
    p.write_text((FIXTURES_T1).read_text())
    assert sum(h["level"] == "BLOCKER" for h in lt.scan_file(p)) >= 3


@pytest.mark.parametrize("line", ["Please verify these numbers against the original paper.", "(verify this number)"])
def test_verify_requests_are_review(line):
    assert any(h["level"] == "REVIEW" for h in lt.scan(line, "x.tex", "typeset text")), line

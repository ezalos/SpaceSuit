# ABOUTME: cite_contexts: cite macros taking #1, \input read in, a missing \input refused, and a %
# ABOUTME: inside \url not taken for a comment.
import cite_contexts as cc
import pytest


def write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_cite_macro_with_parameter_is_refused_then_declared(tmp_path):
    p = write(tmp_path, "main.tex", "\\newcommand{\\pc}[1]{\\citep{#1}}\n\nX reaches 99.9\\% \\pc{a}.\n")
    with pytest.raises(SystemExit) as e:
        cc.collect([p], set())[:2]
    assert "pc" in str(e.value)
    contexts, _ = cc.collect([p], {"pc"})[:2]
    assert set(contexts) == {"a"}


def test_input_files_are_read_in(tmp_path):
    p = write(tmp_path, "main.tex", "Intro \\citep{a}.\n\\input{sections/related}\n")
    write(tmp_path, "sections/related.tex", "Related \\citet{b} work.\n")
    contexts, _ = cc.collect([p], set())[:2]
    assert set(contexts) == {"a", "b"}


def test_missing_input_is_refused(tmp_path):
    p = write(tmp_path, "main.tex", "\\include{nothere}\n")
    with pytest.raises(SystemExit):
        cc.collect([p], set())[:2]


def test_percent_in_url_does_not_hide_the_line(tmp_path):
    p = write(tmp_path, "main.tex", "See \\url{https://example.com/a%20b} and the claim in \\citet{b}.\n")
    assert set(cc.collect([p], set())[0]) == {"b"}

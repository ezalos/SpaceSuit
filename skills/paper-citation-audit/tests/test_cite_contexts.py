# ABOUTME: cite_contexts must find every cite command (natbib, biblatex), refuse an unknown cite-like one, keep the
# ABOUTME: claim with its citation across abbreviations, and report line numbers without comments or hidden blocks.
import json

import cite_contexts as cc
import pytest


def run(tmp_path, tex, extra=frozenset()):
    p = tmp_path / "paper.tex"
    p.write_text(tex)
    return cc.collect([p], set(extra))[:2]


@pytest.mark.parametrize("cmd", ["cite", "citet", "citep", "citealt", "citealp", "citeyearpar", "citeauthor",
                                 "parencite", "textcite", "Citet", "Citep", "autocite", "footcite", "citep*"])
def test_each_cite_command_is_found(tmp_path, cmd):
    contexts, unknown = run(tmp_path, f"Robots learn from demonstrations \\{cmd}[see][p.~3]{{a, b}}.\n")
    assert set(contexts) == {"a", "b"} and not unknown


def test_multicite_takes_every_key_list(tmp_path):
    contexts, _ = run(tmp_path, "Shown twice \\parencites[p.~1]{a}[p.~2]{b,c}.\n")
    assert set(contexts) == {"a", "b", "c"}


def test_unknown_cite_command_is_refused(tmp_path, monkeypatch):
    contexts, unknown = run(tmp_path, "A claim \\fancycite{a}.\n")
    assert unknown and "fancycite" in unknown[0]
    bib = tmp_path / "r.bib"
    bib.write_text("@misc{a, title={A}}")
    (tmp_path / "paper.aux").write_text("\\citation{a}\n")
    monkeypatch.setattr("sys.argv", ["cite_contexts.py", str(tmp_path / "o"), str(bib), str(tmp_path / "paper.tex"),
                                     str(tmp_path / "paper.aux")])
    with pytest.raises(SystemExit) as e:
        cc.main()
    assert "fancycite" in str(e.value)
    contexts, unknown = run(tmp_path, "A claim \\fancycite{a}.\n", extra={"fancycite"})
    assert set(contexts) == {"a"} and not unknown


def test_nocite_is_not_a_citing_sentence(tmp_path):
    contexts, unknown = run(tmp_path, "\\nocite{*}\nText.\n")
    assert contexts == {} and not unknown


def test_et_al_before_citep_keeps_the_claim(tmp_path):
    tex = "Earlier work exists. Liu et al. \\citep{libero} propose a benchmark of 130 tasks. Next one.\n"
    contexts, _ = run(tmp_path, tex)
    row = contexts["libero"][0]
    assert "130 tasks" in row["sentence"] and "Liu et al." in row["sentence"]
    assert row["prev"] == "Earlier work exists." and row["next"] == "Next one."


@pytest.mark.parametrize("abbr", ["e.g.", "i.e.", "cf.", "Fig.", "Eq.", "vs."])
def test_abbreviations_do_not_split(tmp_path, abbr):
    contexts, _ = run(tmp_path, f"Policies fail under shifts, {abbr} Camera moves \\citep{{k}}.\n")
    assert contexts["k"][0]["sentence"].startswith("Policies fail")


def test_never_split_right_before_a_cite(tmp_path):
    contexts, _ = run(tmp_path, "Models overfit to the training scenes. \\citet{k} show this.\n")
    assert contexts["k"][0]["sentence"].startswith("Models overfit")


def test_line_numbers_hidden_blocks_empty_keys_and_dedupe(tmp_path):
    tex = ("Intro.\n% a comment \\cite{commented}\n\\iffalse Hidden \\cite{hidden}. \\fi\n"
           "\\begin{comment}\nGone \\cite{gone}.\n\\end{comment}\n"
           "A claim \\cite{a,,b} and again \\cite{a}.\n")
    contexts, _ = run(tmp_path, tex)
    assert set(contexts) == {"a", "b"}
    assert len(contexts["a"]) == 1 and contexts["a"][0]["line"] == 7


def test_main_writes_contexts_and_never_cited(tmp_path, monkeypatch):
    (tmp_path / "paper.tex").write_text("Claim \\citep{a}.\n")
    bib = tmp_path / "r.bib"
    bib.write_text("@misc{a, title={A}}\n@misc{b, title={B}}\n")
    (tmp_path / "paper.aux").write_text("\\citation{a}\n")
    monkeypatch.setattr("sys.argv", ["cite_contexts.py", str(tmp_path / "o"), str(bib), str(tmp_path / "paper.tex"),
                                     str(tmp_path / "paper.aux")])
    cc.main()
    report = json.loads((tmp_path / "o" / "contexts.json").read_text())
    assert report["in_bib_not_cited"] == ["b"] and report["contexts"]["a"][0]["line"] == 1

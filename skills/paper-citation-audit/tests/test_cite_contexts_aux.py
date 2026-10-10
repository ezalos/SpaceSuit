# ABOUTME: cite_contexts: the .aux count guard on hand-written .aux files: required, hidden citations,
# ABOUTME: declared macros, zero keys, a stale .aux, input forms, caption and heading surplus.
import json
import os
import time

import cite_contexts as cc
import pytest

MAIN = r"""\documentclass{article}
\newcommand{\libero}{LIBERO~\cite{liu2023libero}}
\newcommand{\pc}{\citep}
\begin{document}
\libero{} is a benchmark of 130 tasks.

Prior work made this claim with 12 robots~\pc{wrapkey}.

\cites(see)()[p.~3]{multikey}{liu2023libero} say something.

\import{sections/}{intro}
\input sections/prim
\nocite{quiet}
\end{document}
"""


AUX = ["\\relax", "\\citation{liu2023libero}", "\\citation{wrapkey}", "\\citation{multikey,liu2023libero}",
       "\\citation{primkey}", "\\citation{quiet}", "\\@input{sub.aux}"]


def paper(tmp_path, main=MAIN, aux=None, sub_aux=("\\citation{importkey}",)):
    d = tmp_path / "paper"
    (d / "sections").mkdir(parents=True)
    (d / "main.tex").write_text(main)
    (d / "sections" / "intro.tex").write_text("Intro \\citet{importkey} shows it.\n")
    (d / "sections" / "prim.tex").write_text("Prim \\citep{primkey} too.\n")
    (d / "sections" / "unused.tex").write_text("Never read.\n")
    (d / "main.aux").write_text("\n".join(aux or AUX) + "\n")
    (d / "sub.aux").write_text("\n".join(sub_aux) + "\n")
    bib = d / "refs.bib"
    bib.write_text("".join(f"@misc{{{k}, title={{{k}}}}}\n" for k in
                           ("liu2023libero", "wrapkey", "multikey", "importkey", "primkey", "quiet")))
    return d


def run_cc(monkeypatch, d, *extra):
    monkeypatch.setattr("sys.argv", ["cite_contexts.py", str(d / "out"), str(d / "refs.bib"), str(d / "main.tex"),
                                     *[str(d / "main.aux")] * ("--no-aux" not in extra),
                                     *[e for e in extra if e != "--no-aux"]])
    cc.main()
    return json.loads((d / "out" / "contexts.json").read_text())


def titled_paper(tmp_path, aux_lines):
    d = tmp_path / "t"
    d.mkdir()
    (d / "main.tex").write_text("\\begin{document}\n\\section{Background after \\citet{h}}\nBody \\citep{b}.\n"
                                "\\begin{figure}\\caption[Short]{Results from the code~\\cite{c} with\n"
                                "our settings.\\boardstatus{}}\\end{figure}\n\\end{document}\n")
    (d / "refs.bib").write_text("@misc{h, title={H}}\n@misc{b, title={B}}\n@misc{c, title={C}}\n")
    (d / "main.aux").write_text("\n".join(aux_lines) + "\n")
    return d


def test_aux_is_required(tmp_path, monkeypatch):
    d = paper(tmp_path)
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d, "--no-aux")
    assert ".aux" in str(e.value) and "build" in str(e.value)


def test_hidden_citations_are_refused_with_counts(tmp_path, monkeypatch):
    d = paper(tmp_path)
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d)
    assert "--cite-macro=libero=liu2023libero" in str(e.value)
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d, "--cite-macro=libero=liu2023libero")
    assert "wrapkey: aux 1, source scan 0" in str(e.value)


def test_declared_macros_account_for_every_call(tmp_path, monkeypatch, capsys):
    d = paper(tmp_path)
    report = run_cc(monkeypatch, d, "--also-cite=pc", "--cite-macro=libero=liu2023libero")
    assert {"wrapkey", "multikey", "importkey", "primkey"} <= set(report["contexts"])
    assert "quiet" not in report["contexts"] and "quiet" not in report["in_bib_not_cited"]
    assert any("is a benchmark" in r["sentence"] for r in report["contexts"]["liu2023libero"])
    assert any(p.endswith("unused.tex") for p in report["tex_not_read"])


def test_zero_key_cite_is_refused(tmp_path, monkeypatch):
    d = paper(tmp_path, main="\\begin{document}\nA claim \\citep.\n\\end{document}\n", aux=["\\relax"])
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d)
    assert "zero keys" in str(e.value)


def test_stale_aux_is_refused(tmp_path, monkeypatch):
    d = paper(tmp_path)
    old = time.time() - 3600
    os.utime(d / "main.aux", (old, old))
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d, "--also-cite=pc", "--cite-macro=libero=liu2023libero")
    assert "older" in str(e.value)


@pytest.mark.parametrize("line", ["\\subimport{sections/}{intro}", "\\inputfrom{sections}{intro}",
                                  "\\includefrom{sections/}{intro}", "\\input sections/intro"])
def test_input_forms_are_followed(tmp_path, line):
    d = paper(tmp_path, main=f"\\begin{{document}}\n{line}\n\\end{{document}}\n")
    contexts, _ = cc.collect([d / "main.tex"], set())[:2]
    assert "importkey" in contexts


def test_caption_and_heading_surplus_without_lists_is_a_minor_note(tmp_path, monkeypatch):
    d = titled_paper(tmp_path, ["\\citation{h}", "\\citation{h}", "\\citation{b}", "\\citation{c}", "\\citation{c}"])
    report = run_cc(monkeypatch, d)
    assert not report["findings"] and {n.split(":")[1].strip() for n in report["notes"]} >= {"c", "h"}


def test_surplus_beyond_the_captions_still_refuses(tmp_path, monkeypatch):
    d = titled_paper(tmp_path, ["\\citation{h}", "\\citation{b}", "\\citation{c}", "\\citation{c}",
                                "\\citation{c}"])
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d)
    assert "c: aux 3, source scan 1" in str(e.value)


def test_body_cite_surplus_is_not_excused(tmp_path, monkeypatch):
    d = titled_paper(tmp_path, ["\\citation{h}", "\\citation{b}", "\\citation{b}", "\\citation{c}"])
    with pytest.raises(SystemExit) as e:
        run_cc(monkeypatch, d)
    assert "b: aux 2, source scan 1" in str(e.value)


def test_more_than_one_extra_per_caption_is_still_a_finding(tmp_path, monkeypatch):
    d = titled_paper(tmp_path, ["\\citation{h}", "\\citation{b}", "\\citation{c}", "\\citation{c}",
                                "\\citation{c}"])
    with pytest.raises(SystemExit):
        run_cc(monkeypatch, d)

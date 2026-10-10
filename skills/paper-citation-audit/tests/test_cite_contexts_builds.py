# ABOUTME: cite_contexts' .aux guard against real tectonic builds (tests/fixtures, rebuilt by fixtures/build.sh): both
# ABOUTME: count directions, definitions, caption and heading surplus, biblatex, and a missing .aux.
import json
import os
import shutil
import time
from pathlib import Path

import cite_contexts as cc
import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def build(tmp_path, name):
    """A copy of a fixture whose build outputs are newer than its sources, as after a fresh build."""
    d = tmp_path / name
    shutil.copytree(FIXTURES / name, d)
    later = max(p.stat().st_mtime for p in d.rglob("*")) + 1
    for p in d.glob("main.*"):
        if p.suffix != ".tex":
            os.utime(p, (later, later))
    return d


def run(monkeypatch, d, *flags, aux=True):
    monkeypatch.setattr("sys.argv", ["cite_contexts.py", str(d / "out"), str(d / "refs.bib"), str(d / "main.tex"),
                                     *([str(d / "main.aux")] if aux else []), *flags])
    cc.main()
    return json.loads((d / "out" / "contexts.json").read_text())


def refused(monkeypatch, d, *flags) -> str:
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, d, *flags)
    return str(e.value)


# B1: a cite the source scan counts but LaTeX never expanded

@pytest.mark.parametrize("name", ["ifdraft", "verb"])
def test_scan_count_above_aux_refuses(tmp_path, monkeypatch, name):
    msg = refused(monkeypatch, build(tmp_path, name), "--cite-macro=libero=ka")
    assert "ka: aux 1, source scan 2" in msg and "never expanded" in msg


# B2: definitions, in the preamble or the body

@pytest.mark.parametrize("name", ["bodydef", "bodydef2", "heading2"])
def test_fixed_key_cite_in_a_definition_refuses_until_declared(tmp_path, monkeypatch, name):
    d = build(tmp_path, name)
    assert "--cite-macro=libero=ka" in refused(monkeypatch, d)
    report = run(monkeypatch, d, "--cite-macro=libero=ka")
    assert set(report["contexts"]) == {"ka", "kb"} and not report["findings"]


def test_alias_and_subimport_build(tmp_path, monkeypatch):
    d = build(tmp_path, "good")
    assert "ka: aux 2, source scan 1" in refused(monkeypatch, d, "--cite-macro=libero=ka")
    report = run(monkeypatch, d, "--cite-macro=libero=ka", "--also-cite=pc")
    assert set(report["contexts"]) == {"ka", "kb"}


def test_natbib_commands_count_exactly(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "natcmds"))
    assert set(report["contexts"]) == {"ka", "kb"}


# B3: caption and heading surplus

def test_heading_cite_in_the_toc_is_excused_by_the_toc(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "capstd"))
    assert not report["findings"] and any("main.toc" in n for n in report["notes"])


def test_caption_cite_in_the_lof_is_excused_by_the_lof(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "lof"))
    assert not report["findings"] and any("main.lof" in n for n in report["notes"])


def test_one_extra_typesetting_per_caption_without_lists_is_a_minor_note(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "caption"), "--cite-macro=libero=ka")
    assert not report["findings"]
    assert any("CAPTION_TWICE" in n and "main.tex:5" in n for n in report["notes"])


def test_caption_typeset_once_has_no_surplus(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "capshort"), "--cite-macro=libero=ka")
    assert not report["findings"]


# N3, N4

def test_missing_aux_is_a_clean_refusal(tmp_path, monkeypatch):
    d = build(tmp_path, "good")
    (d / "main.aux").rename(d / "gone.aux")
    msg = refused(monkeypatch, d)
    assert "main.aux" in msg and "does not exist" in msg


def test_biblatex_bibtex_backend_counts_abx_lines(tmp_path, monkeypatch):
    d = build(tmp_path, "blx")
    assert "--cite-macro=libero=ka" in refused(monkeypatch, d)
    report = run(monkeypatch, d, "--cite-macro=libero=ka")
    assert set(report["contexts"]) == {"ka", "kb"} and "biblatex-control" not in report["cited_not_in_bib"]


# robust and etoolbox definitions, and cites in \title / \thanks

@pytest.mark.parametrize("name,macro", [("robust", "priorwork"), ("etoolbox", "priorwork")])
def test_robust_definitions_are_definitions(tmp_path, monkeypatch, name, macro):
    d = build(tmp_path, name)
    assert f"--cite-macro={macro}=a" in refused(monkeypatch, d)
    assert set(run(monkeypatch, d, f"--cite-macro={macro}=a")["contexts"]) == {"a", "b"}


def test_a_cite_in_a_thanks_in_the_preamble_is_scanned(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "title"))
    assert any("Extends" in r["sentence"] for r in report["contexts"]["a"])


def test_a_caption_cite_carries_its_table(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "tab"))
    (row,) = report["contexts"]["lib"]
    assert "79.6" in row["table"] and "70.1" in row["table"]
    assert all("table" not in r for r in report["contexts"]["act"])


# tables: the whole float, \input inlined, every cite in it

def test_a_caption_cite_carries_the_inputted_table(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "tabin"))
    (row,) = report["contexts"]["pub"]
    assert "96.8" in row["table"]
    assert "table" not in report["contexts"]["oth"][0]


def test_a_tablenote_cite_carries_the_table(tmp_path, monkeypatch):
    (row,) = run(monkeypatch, build(tmp_path, "tabnote"))["contexts"]["pub"]
    assert "96.8" in row["table"] and "93.7" in row["table"]


@pytest.mark.parametrize("env", ["table*", "sidewaystable"])
def test_any_table_float_and_unreadable_content(tmp_path, env):
    p = tmp_path / "main.tex"
    p.write_text(f"\\begin{{document}}\n\\begin{{{env}}}\\caption{{From \\citet{{a}}.}}\n"
                 "\\includegraphics{t.png}\n\\end{" + env + "}\n"
                 "\\begin{minipage}{3cm}\\captionof{table}{Also \\citet{b}.}\\begin{tblr}{ll} x & 4.2 \\end{tblr}"
                 "\\end{minipage}\n\\end{document}\n")
    contexts = cc.collect([p], set())[0]
    assert contexts["a"][0]["table"].startswith(cc.TABLE_UNREADABLE) and "4.2" in contexts["b"][0]["table"]


def test_preamble_inputs_are_listed_apart(tmp_path, monkeypatch):
    d = build(tmp_path, "tabnote")
    (d / "macros.tex").write_text("\\newcommand{\\x}{y}\n")
    (d / "main.tex").write_text((d / "main.tex").read_text().replace("\\begin{document}",
                                                                      "\\input{macros}\n\\begin{document}"))
    os.utime(d / "main.aux", (time.time() + 5, time.time() + 5))
    report = run(monkeypatch, d)
    assert any(p.endswith("macros.tex") for p in report["preamble_inputs"])
    assert not any(p.endswith("macros.tex") for p in report["tex_not_read"])


def test_a_repeated_sentence_keeps_every_occurrence(tmp_path, monkeypatch):
    (row,) = run(monkeypatch, build(tmp_path, "repeated"))["contexts"]["act"]
    assert [o["line"] for o in row["occurrences"]] == [5, 8]
    assert "91.2" in row["occurrences"][1]["next"] and "91.2" not in row["occurrences"][0]["next"]


def test_a_table_named_by_ref_is_attached(tmp_path, monkeypatch):
    (row,) = run(monkeypatch, build(tmp_path, "tableref"))["contexts"]["kim"]
    assert "96.8" in row["table"]


def test_captionof_in_center_is_a_table_and_its_double_typesetting_a_minor_note(tmp_path, monkeypatch):
    report = run(monkeypatch, build(tmp_path, "captionof"))
    (row,) = report["contexts"]["kim"]
    assert "96.8" in row["table"] and not report["findings"]
    assert any("CAPTION_TWICE" in n and "kim" in n for n in report["notes"])


@pytest.mark.parametrize("abbr", ["App.", "Alg.", "Thm.", "Lem.", "Prop.", "Def.", "Cor."])
def test_more_abbreviations_do_not_split(tmp_path, abbr):
    p = tmp_path / "main.tex"
    p.write_text(f"As in {abbr} 3 of the paper \\citep{{k}}, it holds.\n")
    assert cc.collect([p], set())[0]["k"][0]["sentence"].startswith("As in")


def test_a_label_inside_an_inputted_table_links_the_table(tmp_path, monkeypatch):
    (row,) = run(monkeypatch, build(tmp_path, "tablabel-input"))["contexts"]["kim"]
    assert "96.8" in row["table"]


def test_a_label_named_by_any_command_links_the_table(tmp_path, monkeypatch):
    (row,) = run(monkeypatch, build(tmp_path, "tablabel-macro"))["contexts"]["kim"]
    assert "96.8" in row["table"]


def test_a_figure_holding_a_tabular_is_a_table_float(tmp_path, monkeypatch):
    (row,) = run(monkeypatch, build(tmp_path, "figtable"))["contexts"]["kim"]
    assert "84.7" in row["table"] and "98.8" in row["table"]


@pytest.mark.parametrize("name", ["minus-sentence", "minus-table"])
def test_a_typeset_minus_keeps_its_sign(tmp_path, monkeypatch, name):
    import quote_check as qc
    d = build(tmp_path, name)
    report = run(monkeypatch, d)
    text = ("OpenVLA is evaluated on the LIBERO benchmark. On LIBERO-Spatial OpenVLA reaches a success rate of 84.7 "
            "here.")
    (d / "out" / "texts").mkdir()
    import bib_check as bc
    bc.text_path(d / "out", "kim").write_text(text)
    (d / "out" / "fetched.json").write_text(json.dumps({"kim": {"landing": False, "pdf": True, "chars": 9000}}))
    rows = [json.dumps({"key": "kim", "sentence": r["sentence"], "verdict": "SUPPORTED",
                        "claim": "OpenVLA reaches 84.7 on LIBERO-Spatial",
                        "quote": "On LIBERO-Spatial OpenVLA reaches a success rate of 84.7"})
            for r in report["contexts"]["kim"]]
    (d / "out" / "verdicts-1.jsonl").write_text("\n".join(rows) + "\n")
    monkeypatch.setattr("sys.argv", ["quote_check.py", str(d / "out")])
    qc.main()
    (v,) = json.loads((d / "out" / "verdicts-checked.json").read_text())
    assert v["verdict"] == "NEEDS_REVIEW" and "-84.7" in v["quote_check"]

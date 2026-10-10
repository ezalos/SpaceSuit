# ABOUTME: quote_check: numbers of the sentence, NO_TEXT against fetched.json, content words, and
# ABOUTME: orphan rows left out of the totals.
import json

import bib_check as bc
import quote_check as qc

SRC = "In this paper we propose a method. Our model reaches 79.6 percent success on LIBERO with -0.5 drift."


def qsetup(tmp_path, rows, fetched=None, text=SRC, ctx=None):
    ctx = ctx or {"k": [{"file": "m.tex", "line": 1, "sentence": "K reaches 85.0\\% on LIBERO \\cite{k}."}]}
    (tmp_path / "contexts.json").write_text(json.dumps({"contexts": ctx}))
    (tmp_path / "verdicts-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (tmp_path / "texts").mkdir(exist_ok=True)
    bc.text_path(tmp_path, "k").write_text(text)
    if fetched is not None:
        (tmp_path / "fetched.json").write_text(json.dumps(fetched))


def qrun(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["quote_check.py", str(tmp_path)])
    qc.main()
    return json.loads((tmp_path / "verdicts-checked.json").read_text())


S = "K reaches 85.0\\% on LIBERO \\cite{k}."


def test_number_in_the_sentence_must_be_in_claim_or_quote(tmp_path, monkeypatch):
    qsetup(tmp_path, [{"key": "k", "sentence": S, "claim": "K is evaluated on LIBERO", "verdict": "SUPPORTED",
                       "quote": "reaches 79.6 percent success on LIBERO"}])
    assert qrun(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"


def test_no_text_on_a_full_text_is_invalid(tmp_path, monkeypatch):
    qsetup(tmp_path, [{"key": "k", "sentence": S, "verdict": "NO_TEXT"}], fetched={"k": {"landing": False}})
    assert qrun(tmp_path, monkeypatch)[0]["verdict"] == "INVALID_VERDICT"
    qsetup(tmp_path, [{"key": "k", "sentence": S, "verdict": "NO_TEXT"}], fetched={"k": {"landing": True}})
    assert qrun(tmp_path, monkeypatch)[0]["verdict"] == "NO_TEXT"


def test_quote_sharing_no_content_word_with_the_claim_needs_review(tmp_path, monkeypatch):
    qsetup(tmp_path, [{"key": "k", "sentence": "K exists \\cite{k}.", "claim": "transformers dominate vision",
                       "verdict": "SUPPORTED", "quote": "In this paper we propose a method"}],
           ctx={"k": [{"file": "m.tex", "line": 1, "sentence": "K exists \\cite{k}."}]})
    assert qrun(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"


def test_orphan_rows_are_not_in_the_totals(tmp_path, monkeypatch, capsys):
    qsetup(tmp_path, [{"key": "k", "sentence": "Paraphrase.", "verdict": "CONTRADICTED"}])
    qrun(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "CONTRADICTED" not in out.splitlines()[0] and "UNCHECKED 1" in out

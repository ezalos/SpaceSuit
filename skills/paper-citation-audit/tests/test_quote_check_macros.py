# ABOUTME: quote_check: user macros in a SUPPORTED sentence, a PARTIAL quote not in the text, and
# ABOUTME: NO_TEXT on a long non-PDF text.
import json

import bib_check as bc
import quote_check as qc


def qrun(tmp_path, monkeypatch, row, fetched, text="OpenVLA reaches 76.5% average success on LIBERO in our tests."):
    (tmp_path / "contexts.json").write_text(json.dumps({"contexts": {"k": [
        {"file": "m.tex", "line": 1, "sentence": row["sentence"]}]}}))
    (tmp_path / "verdicts-a.jsonl").write_text(json.dumps({"key": "k", **row}) + "\n")
    (tmp_path / "fetched.json").write_text(json.dumps({"k": fetched}))
    bc.text_path(tmp_path, "k").parent.mkdir(exist_ok=True)
    bc.text_path(tmp_path, "k").write_text(text)
    monkeypatch.setattr("sys.argv", ["quote_check.py", str(tmp_path)])
    qc.main()
    return json.loads((tmp_path / "verdicts-checked.json").read_text())[0]


def test_supported_sentence_with_a_user_macro_needs_review(tmp_path, monkeypatch):
    v = qrun(tmp_path, monkeypatch, {"sentence": "Our policy reaches \\liberoavg\\% on LIBERO, beating OpenVLA "
                                                 "\\citep{k}.", "claim": "OpenVLA is evaluated on LIBERO",
                                     "verdict": "SUPPORTED",
                                     "quote": "OpenVLA reaches 76.5% average success on LIBERO"},
             {"landing": False, "pdf": True})
    assert v["verdict"] == "NEEDS_REVIEW" and "liberoavg" in v["quote_check"]


def test_partial_with_a_quote_not_in_the_text_is_quote_not_found(tmp_path, monkeypatch):
    v = qrun(tmp_path, monkeypatch, {"sentence": "OpenVLA was trained on episodes \\citep{k}.", "claim": "episodes",
                                     "verdict": "PARTIAL", "quote": "OpenVLA is trained on 2 million robot episodes"},
             {"landing": False, "pdf": True})
    assert v["verdict"] == "QUOTE_NOT_FOUND"


def test_no_text_on_a_long_html_text_is_unverified(tmp_path, monkeypatch):
    v = qrun(tmp_path, monkeypatch, {"sentence": "S \\citep{k}.", "verdict": "NO_TEXT"},
             {"landing": True, "pdf": False, "chars": 50_000})
    assert v["verdict"] == "UNVERIFIED"

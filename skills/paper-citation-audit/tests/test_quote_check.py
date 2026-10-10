# ABOUTME: quote_check must mark every unjudged (key, sentence) UNCHECKED, normalise or reject verdict values, match
# ABOUTME: quotes on word boundaries with minus signs kept, and send a SUPPORTED claim missing a number to review.
import json

import bib_check
import pytest
import quote_check as qc

SOURCE = ("We evaluate on the LIBERO benchmark. Under camera shifts the success rate changes by −2.3 points, "
          "while the provision of the model is unchanged across all 130 tasks in the suite.")


def setup(tmp_path, contexts, verdicts, key="k", text=SOURCE):
    (tmp_path / "contexts.json").write_text(json.dumps({"contexts": contexts}))
    (tmp_path / "verdicts-a.jsonl").write_text("\n".join(json.dumps(v) if isinstance(v, dict) else v
                                                         for v in verdicts) + "\n")
    p = bib_check.text_path(tmp_path, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def run(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["quote_check.py", str(tmp_path)])
    qc.main()
    return json.loads((tmp_path / "verdicts-checked.json").read_text())


def ctx(*sentences, key="k"):
    return {key: [{"file": "p.tex", "line": i + 1, "sentence": s} for i, s in enumerate(sentences)]}


def test_missing_verdict_is_unchecked(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S one.", "S two."), [{"key": "k", "sentence": "S one.", "verdict": "PARTIAL"}])
    rows = run(tmp_path, monkeypatch)
    assert [r["sentence"] for r in rows if r["verdict"] == "UNCHECKED"] == ["S two."]


def test_lowercase_verdict_is_normalised_and_unknown_is_invalid(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S one.", "S two."),
          [{"key": "k", "sentence": "S one.", "verdict": "supported", "claim": "camera shifts",
            "quote": "Under camera shifts the success rate changes"},
           {"key": "k", "sentence": "S two.", "verdict": "MAYBE"}, "not json"])
    rows = run(tmp_path, monkeypatch)
    by = {r.get("sentence"): r["verdict"] for r in rows}
    assert by["S one."] == "SUPPORTED"
    assert sorted(r["verdict"] for r in rows) == ["INVALID_VERDICT", "INVALID_VERDICT", "SUPPORTED", "UNCHECKED"]


def test_quote_matches_on_word_boundaries_only(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "SUPPORTED",
                                 "quote": "vision of the model is unchanged across all"}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "QUOTE_NOT_FOUND"


def test_lost_minus_sign_needs_review(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "SUPPORTED",
                                 "claim": "success rises by 2.3 points under camera shifts",
                                 "quote": "the success rate changes by -2.3 points"}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"


def test_minus_sign_kept_in_the_quote_matches(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "SUPPORTED",
                                 "claim": "success changes by -2.3 points",
                                 "quote": "the success rate changes by -2.3 points"}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "SUPPORTED"


def test_quote_that_drops_the_sign_is_not_found(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "SUPPORTED",
                                 "claim": "changes by 2.3 points", "quote": "the success rate changes by 2.3 points"}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "QUOTE_NOT_FOUND"


@pytest.mark.parametrize("a,b", [("LIBERO-90", "LIBERO 90"), ("10–20", "10-20")])
def test_hyphens_in_names_and_ranges_are_not_signs(a, b):
    assert qc.squash(a) == qc.squash(b)


def test_verdict_for_a_sentence_not_in_contexts_covers_nothing(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S one."), [{"key": "k", "sentence": "A paraphrase.", "verdict": "PARTIAL",
                                     "claim": "camera shifts",
                                     "quote": "Under camera shifts the success rate changes"}])
    rows = run(tmp_path, monkeypatch)
    assert {r["verdict"] for r in rows} == {"PARTIAL", "UNCHECKED"}
    assert next(r for r in rows if r["verdict"] == "PARTIAL")["in_contexts"] is False


@pytest.mark.parametrize("quote", ["", None])
def test_partial_without_a_quote_is_quote_not_found(tmp_path, monkeypatch, quote):
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "partial", "claim": "camera shifts",
                                 "quote": quote}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "QUOTE_NOT_FOUND"


def test_partial_claim_numbers_are_checked(tmp_path, monkeypatch):
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "PARTIAL",
                                 "claim": "the success rate changes by 9.9 points under camera shifts",
                                 "quote": "Under camera shifts the success rate changes by"}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"


def test_partial_sentence_numbers_are_checked(tmp_path, monkeypatch):
    setup(tmp_path, ctx("X reaches 79.6 points under camera shifts."),
          [{"key": "k", "sentence": "X reaches 79.6 points under camera shifts.", "verdict": "PARTIAL",
            "claim": "the success rate changes under camera shifts",
            "quote": "Under camera shifts the success rate changes by"}])
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"


def test_a_hedged_quote_against_a_strong_sentence_needs_review(tmp_path, monkeypatch):
    text = "We suggest that scaling may help robots in many settings."
    setup(tmp_path, ctx("X shows that scaling always helps."),
          [{"key": "k", "sentence": "X shows that scaling always helps.", "verdict": "SUPPORTED",
            "claim": "scaling helps", "quote": "We suggest that scaling may help robots"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "NEEDS_REVIEW" and "hedge" in v["quote_check"]


def ctx_rows(rows):
    return {"k": [{"file": "p.tex", "line": i + 1, **r} for i, r in enumerate(rows)]}


def test_a_continuing_next_sentence_holds_numbers_to_the_claim(tmp_path, monkeypatch):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    rows = [{"sentence": "We compare with ACT \\citep{k}.", "next": "On LIBERO-90 it reaches 79.6\\% success."}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": rows[0]["sentence"], "verdict": "SUPPORTED",
                                      "claim": "ACT predicts action chunks",
                                      "quote": "ACT predicts action chunks with a transformer"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "NEEDS_REVIEW" and "next sentence" in v["quote_check"]


def test_a_next_sentence_with_its_own_cite_is_not_the_claim(tmp_path, monkeypatch):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    for nxt in ["On LIBERO-90 it reaches 79.6\\% success \\citep{x}."]:
        rows = [{"sentence": "We compare with ACT \\citep{k}.", "next": nxt}]
        setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": rows[0]["sentence"], "verdict": "SUPPORTED",
                                          "claim": "ACT predicts action chunks",
                                          "quote": "ACT predicts action chunks with a transformer"}], text=text)
        assert run(tmp_path, monkeypatch)[0]["verdict"] == "SUPPORTED", nxt


def test_a_caption_holds_its_table_numbers_to_the_claim(tmp_path, monkeypatch):
    text = "Baseline methods are evaluated on LIBERO-90 with our protocol in this paper."
    rows = [{"sentence": "Success rates. Baseline numbers are taken from \\citet{k}.",
             "table": "Method & LIBERO-90 ACT & 79.6 Diffusion Policy & 70.1"}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": rows[0]["sentence"], "verdict": "SUPPORTED",
                                      "claim": "baseline methods evaluated on LIBERO-90",
                                      "quote": "Baseline methods are evaluated on LIBERO-90"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "NEEDS_REVIEW" and "table" in v["quote_check"]


def test_a_negated_quote_needs_review(tmp_path, monkeypatch):
    text = "Our results show that the policy does not generalise to new camera viewpoints at all."
    setup(tmp_path, ctx("S."), [{"key": "k", "sentence": "S.", "verdict": "SUPPORTED",
                                 "claim": "the policy generalises",
                                 "quote": "generalise to new camera viewpoints"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "NEEDS_REVIEW" and "negation" in v["quote_check"]


@pytest.mark.parametrize("prev,sentence,nxt,flagged", [
    ("ACT reaches 79.6\\% on LIBERO-90.", "These numbers are reported by \\citet{k}.", "", True),
    ("ACT reaches 79.6\\% on LIBERO-90 \\citep{x}.", "These numbers are reported by \\citet{k}.", "", False),
    ("", "We compare with ACT \\citep{k}.", "The authors report 79.6\\% on LIBERO-90.", True),
    ("", "We compare with ACT \\citep{k}.", "Reported success: 79.6\\% on LIBERO-90.", True)])
def test_attribution_continues_both_ways(tmp_path, monkeypatch, prev, sentence, nxt, flagged):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    rows = [{"sentence": sentence, "prev": prev, "next": nxt}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": sentence, "verdict": "SUPPORTED",
                                      "claim": "ACT predicts action chunks",
                                      "quote": "ACT predicts action chunks with a transformer"}], text=text)
    assert (run(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW") is flagged


def test_an_unreadable_table_needs_review(tmp_path, monkeypatch):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    rows = [{"sentence": "Scores from \\citet{k}.", "table": "unreadable, check the PDF: \\includegraphics"}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": rows[0]["sentence"], "verdict": "SUPPORTED",
                                      "claim": "ACT predicts action chunks",
                                      "quote": "ACT predicts action chunks with a transformer"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "NEEDS_REVIEW" and "unreadable" in v["quote_check"]


def test_every_occurrence_of_a_repeated_sentence_is_checked(tmp_path, monkeypatch):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    s1 = "We follow \\citet{k}."
    rows = [{"sentence": s1, "next": "The encoder is a ResNet.",
             "occurrences": [{"file": "p.tex", "line": 1, "prev": "", "next": "The encoder is a ResNet."},
                             {"file": "p.tex", "line": 9, "prev": "",
                              "next": "It reaches 91.2\\\\% on LIBERO-Spatial."}]}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": s1, "verdict": "SUPPORTED",
                                      "claim": "ACT predicts chunks",
                                      "quote": "ACT predicts action chunks with a transformer"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "NEEDS_REVIEW" and "91.2" in v["quote_check"]


@pytest.mark.parametrize("opener", ["The numbers above are", "Both results are", "The former is", "The same rates are"])
def test_more_backward_references(tmp_path, monkeypatch, opener):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    sentence = f"{opener} reported by \\\\citet{{k}}."
    rows = [{"sentence": sentence, "prev": "ACT reaches 79.6\\\\% on LIBERO-90."}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": sentence, "verdict": "SUPPORTED",
                                      "claim": "ACT predicts action chunks",
                                      "quote": "ACT predicts action chunks with a transformer"}], text=text)
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"


def test_standard_latex_is_not_a_user_macro_and_thousands_are_one_number(tmp_path, monkeypatch):
    text = "We evaluate on 10,030 cases of the LIBERO-Plus benchmark in total."
    sentence = "\\\\textbf{Setup.} The benchmark holds 10,030 cases \\\\citep{k} (Section~\\\\ref{s}, \\\\emph{all})."
    setup(tmp_path, ctx_rows([{"sentence": sentence}]),
          [{"key": "k", "sentence": sentence, "verdict": "SUPPORTED", "claim": "the benchmark holds 10,030 cases",
            "quote": "We evaluate on 10,030 cases of the LIBERO-Plus benchmark"}], text=text)
    v = run(tmp_path, monkeypatch)[0]
    assert v["verdict"] == "SUPPORTED", v["quote_check"]


@pytest.mark.parametrize("raw", ["$-$84.7", "\\(-\\)84.7", "{-}84.7", "\\textminus 84.7", "$\\minus$84.7"])
def test_typeset_minus_forms_are_a_sign(raw):
    assert qc.tokens(f"by {raw} points") == ["by", "-84.7", "points"]


@pytest.mark.parametrize("lead", ["See", "Cf.", "Source:", "From"])
def test_see_cf_source_from_lead_back_to_the_previous_sentence(tmp_path, monkeypatch, lead):
    text = "ACT predicts action chunks with a transformer and is evaluated on many tasks."
    sentence = f"{lead} \\\\citet{{k}} for these."
    rows = [{"sentence": sentence, "prev": "ACT reaches 79.6\\\\% on LIBERO-90."}]
    setup(tmp_path, ctx_rows(rows), [{"key": "k", "sentence": sentence, "verdict": "SUPPORTED",
                                      "claim": "ACT predicts action chunks",
                                      "quote": "ACT predicts action chunks with a transformer"}], text=text)
    assert run(tmp_path, monkeypatch)[0]["verdict"] == "NEEDS_REVIEW"

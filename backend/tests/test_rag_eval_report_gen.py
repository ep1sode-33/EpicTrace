from scripts.rag_eval.report import GEN_CORE, format_report


def test_gen_core_report():
    summary = {"config_hash": "g", "n": 1,
               "overall": {"faithfulness": 0.9, "citation_accuracy": 0.8},
               "by_slice": {"lang=zh": {"faithfulness": 0.7, "citation_accuracy": 0.6}}}
    out = format_report(summary, metrics=["faithfulness", "citation_accuracy"])
    assert "faithfulness" in out and "lang=zh" in out
    assert "faithfulness" in GEN_CORE

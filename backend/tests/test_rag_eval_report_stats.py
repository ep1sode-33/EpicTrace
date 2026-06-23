from scripts.rag_eval.report import (
    aggregate_multirun, diff_runs_paired, format_multirun, format_report_ci,
)


def _pq(ids_metrics, slices=None):
    return [{"id": i, "slices": slices or {}, "metrics": m} for i, m in ids_metrics]


def test_report_ci_has_brackets():
    per_q = _pq([(f"g{i}", {"faithfulness": float(i % 2)}) for i in range(10)], {"lang": "zh"})
    out = format_report_ci(per_q, metrics=["faithfulness"])
    assert "overall (10)" in out and "[" in out and "lang=zh" in out


def test_diff_paired_marks_only_significant():
    # recall(二值)全 0→1 → McNemar 极显著 → ▲;faithfulness 不变 → ~(不显著)
    a = _pq([(f"g{i}", {"agent_recall_any@5": 0.0, "faithfulness": 0.9}) for i in range(12)])
    b = _pq([(f"g{i}", {"agent_recall_any@5": 1.0, "faithfulness": 0.9}) for i in range(12)])
    out = diff_runs_paired(a, b, metrics=["agent_recall_any@5", "faithfulness"])
    assert "+1.00▲" in out      # recall 显著提升
    assert "+0.00~" in out      # faithfulness 无变化 → 标不显著,不打箭头


def test_multirun_mean_std():
    sums = [{"overall": {"faithfulness": 0.9}},
            {"overall": {"faithfulness": 0.7}},
            {"overall": {"faithfulness": 0.8}}]
    out = format_multirun(sums, metrics=["faithfulness"])
    assert "0.800" in out       # mean(.9,.7,.8)=0.8
    agg = aggregate_multirun(sums, metrics=["faithfulness"])
    assert abs(agg["faithfulness"][0] - 0.8) < 1e-9 and agg["faithfulness"][1] > 0

from scripts.rag_eval.report import diff_runs, format_report

SUM_A = {"config_hash": "aaa", "n": 2,
         "overall": {"recall_any@5": 0.50, "mrr": 0.40},
         "by_slice": {"lang=zh": {"recall_any@5": 0.40, "mrr": 0.30}}}
SUM_B = {"config_hash": "bbb", "n": 2,
         "overall": {"recall_any@5": 0.70, "mrr": 0.45},
         "by_slice": {"lang=zh": {"recall_any@5": 0.40, "mrr": 0.50}}}


def test_format_report_has_overall_and_slice():
    out = format_report(SUM_A, metrics=["recall_any@5", "mrr"])
    assert "overall" in out and "lang=zh" in out
    assert "0.50" in out and "0.40" in out


def test_diff_marks_direction():
    out = diff_runs(SUM_A, SUM_B, metrics=["recall_any@5", "mrr"])
    assert "+0.20" in out or "0.20" in out      # overall recall_any@5 升
    assert "▲" in out and "=" in out            # 升 + 持平(zh recall_any@5 不变)


def test_diff_missing_metric_renders_nan_not_zero():
    # mrr 只在 A 的 overall 有,B 的 overall 缺 —— 不能渲染成误导性的 "+0.00=",应为 "nan"。
    sum_a = {"config_hash": "aaa", "n": 1, "overall": {"recall_any@5": 0.50, "mrr": 0.40}}
    sum_b = {"config_hash": "bbb", "n": 1, "overall": {"recall_any@5": 0.70}}
    out = diff_runs(sum_a, sum_b, metrics=["recall_any@5", "mrr"])
    assert "nan" in out                          # 缺失指标渲染为 nan
    assert "+0.00" not in out                    # 绝不把缺失伪装成持平

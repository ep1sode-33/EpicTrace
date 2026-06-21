import math
from scripts.rag_eval.aggregate import aggregate, mean_skipnan


def test_mean_skipnan():
    assert mean_skipnan([1.0, math.nan, 0.0]) == 0.5
    assert math.isnan(mean_skipnan([math.nan, math.nan]))
    assert math.isnan(mean_skipnan([]))


def test_aggregate_by_slice():
    per_q = [{"slices": {"lang": "zh"}, "metrics": {"m": 1.0}},
             {"slices": {"lang": "zh"}, "metrics": {"m": 0.0}},
             {"slices": {"lang": "en"}, "metrics": {"m": math.nan}}]
    agg = aggregate(per_q, dims=("lang",))
    assert agg["overall"]["m"] == 0.5            # nan skipped from en; (1+0)/2
    assert agg["by_slice"]["lang=zh"]["m"] == 0.5
    assert math.isnan(agg["by_slice"]["lang=en"]["m"])

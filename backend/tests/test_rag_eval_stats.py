import math

from scripts.rag_eval.stats import (
    bootstrap_ci, is_binary, mcnemar_p, mean_std, paired_permutation_p, paired_significance,
)


def test_mean_std_skips_nan():
    m, s = mean_std([1.0, math.nan, 3.0])          # [1,3] → mean 2, 样本 std sqrt(2)
    assert m == 2.0 and math.isclose(s, math.sqrt(2.0), rel_tol=1e-9)
    m1, s1 = mean_std([5.0])
    assert m1 == 5.0 and math.isnan(s1)             # n<2 → std nan
    assert all(math.isnan(x) for x in mean_std([math.nan]))


def test_bootstrap_ci():
    lo, hi = bootstrap_ci([0.0] * 5 + [1.0] * 5, n_boot=2000, seed=1)   # mean 0.5
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
    lo2, hi2 = bootstrap_ci([0.7] * 8, seed=1)      # 全同 → 退化到该点
    assert math.isclose(lo2, 0.7) and math.isclose(hi2, 0.7)
    assert all(math.isnan(x) for x in bootstrap_ci([1.0]))


def test_mcnemar():
    assert mcnemar_p([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0           # 无 discordant
    assert mcnemar_p([0] * 10, [1] * 10) < 0.01                   # 10 个 0→1,极显著
    assert mcnemar_p([0, 1, 0, 1], [1, 0, 1, 0]) == 1.0           # 对称 discordant 不显著


def test_permutation():
    assert paired_permutation_p([0.1] * 12, [0.9] * 12, seed=1) < 0.01   # 一致大涨 → 显著
    assert paired_permutation_p([0.5] * 8, [0.5] * 8) == 1.0             # 无差异


def test_auto_select():
    assert is_binary([0.0, 1.0, math.nan, 1.0]) is True
    assert is_binary([0.5, 1.0]) is False
    assert paired_significance([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0        # 二值→McNemar

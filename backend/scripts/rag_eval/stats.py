"""统计严谨原语:N-run mean±std、逐项 bootstrap CI、配对显著性检验。全部忽略 nan、可种子复现。

用途:① 治随机指标(judge/agent)单点估计——N 次 run 取 mean±std;② 单 run 内对 22~38 题的均值给
bootstrap 95% CI(逐项重采样);③ diff 两个 run 时,二值指标用 McNemar 精确检验、连续指标用配对置换检验,
箭头只在显著(p<0.05)时打——杜绝把噪声当信号(0.68→0.95 这类判断须经检验)。
"""
from __future__ import annotations

import math
import random
from math import comb


def _clean(values) -> list[float]:
    return [float(v) for v in values if not (isinstance(v, float) and math.isnan(v))]


def mean_std(values) -> tuple[float, float]:
    """忽略 nan;返回 (mean, 样本标准差)。空→(nan,nan);n<2→(mean,nan)。"""
    vals = _clean(values)
    if not vals:
        return (math.nan, math.nan)
    m = sum(vals) / len(vals)
    if len(vals) < 2:
        return (m, math.nan)
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return (m, math.sqrt(var))


def bootstrap_ci(values, *, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """对逐项 values bootstrap 重采样**均值**的 (lo, hi) 置信区间(percentile 法)。忽略 nan;n<2→(nan,nan)。"""
    vals = _clean(values)
    n = len(vals)
    if n < 2:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[max(0, int((alpha / 2) * n_boot))]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def _paired(a, b) -> list[tuple[float, float]]:
    out = []
    for x, y in zip(a, b):
        if not ((isinstance(x, float) and math.isnan(x)) or (isinstance(y, float) and math.isnan(y))):
            out.append((float(x), float(y)))
    return out


def is_binary(values) -> bool:
    """非 nan 值是否全 ∈ {0,1}(决定用 McNemar 还是置换检验)。"""
    return all(v in (0.0, 1.0) for v in _clean(values))


def mcnemar_p(a, b) -> float:
    """配对二值(0/1)的 McNemar 精确检验(双尾二项,p=0.5)。只看 discordant pairs。
    无配对/无 discordant→1.0。"""
    pairs = _paired(a, b)
    b01 = sum(1 for x, y in pairs if x == 0.0 and y == 1.0)
    b10 = sum(1 for x, y in pairs if x == 1.0 and y == 0.0)
    n = b01 + b10
    if n == 0:
        return 1.0
    k = min(b01, b10)
    p = 2.0 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, p)


def paired_permutation_p(a, b, *, n_perm: int = 5000, seed: int = 0) -> float:
    """配对连续的置换检验(双尾,基于配对差的均值,随机翻转符号)。无配对/全零差→1.0。"""
    pairs = _paired(a, b)
    diffs = [y - x for x, y in pairs]
    if not diffs or all(d == 0.0 for d in diffs):
        return 1.0
    obs = abs(sum(diffs) / len(diffs))
    rng = random.Random(seed)
    cnt = 0
    for _ in range(n_perm):
        s = abs(sum(d if rng.random() < 0.5 else -d for d in diffs) / len(diffs))
        if s >= obs - 1e-12:
            cnt += 1
    return cnt / n_perm


def paired_significance(a, b) -> float:
    """自动选检验:两组逐项值全 ∈{0,1} → McNemar;否则配对置换。返回 p 值。"""
    if is_binary(a) and is_binary(b):
        return mcnemar_p(a, b)
    return paired_permutation_p(a, b)

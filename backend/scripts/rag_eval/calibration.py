"""judge 可信前提:judge 裁决 vs 人工标注的一致性。Cohen's kappa(达标才采信 judge)。"""
from __future__ import annotations

import math
from collections import Counter


def cohen_kappa(a: list, b: list) -> float:
    n = len(a)
    if n == 0 or n != len(b):
        return math.nan
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe == 1.0:
        return 1.0 if po == 1.0 else math.nan
    return (po - pe) / (1 - pe)


def calibrate(judge_labels: list, human_labels: list) -> dict:
    n = len(judge_labels)
    agreement = (sum(1 for x, y in zip(judge_labels, human_labels) if x == y) / n) if n else math.nan
    return {"kappa": cohen_kappa(judge_labels, human_labels), "n": n, "agreement": agreement}

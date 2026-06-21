"""nan-aware 聚合(judge 失败 = nan,均值跳过 nan;全 nan → nan)。"""
from __future__ import annotations

import math


def mean_skipnan(vals) -> float:
    good = [v for v in vals if not (isinstance(v, float) and math.isnan(v))]
    return sum(good) / len(good) if good else math.nan


def _keys(per_q) -> list[str]:
    ks: list[str] = []
    for r in per_q:
        for k in r["metrics"]:
            if k not in ks:
                ks.append(k)
    return ks


def aggregate(per_q, dims=("domain", "doc_type", "lang", "q_type")) -> dict:
    keys = _keys(per_q)
    overall = {k: mean_skipnan([r["metrics"].get(k, math.nan) for r in per_q]) for k in keys}
    by_slice: dict = {}
    for dim in dims:
        groups: dict = {}
        for r in per_q:
            v = r["slices"].get(dim)
            if v is not None:
                groups.setdefault(f"{dim}={v}", []).append(r)
        for name, rows in groups.items():
            by_slice[name] = {k: mean_skipnan([r["metrics"].get(k, math.nan) for r in rows]) for k in keys}
    return {"overall": overall, "by_slice": by_slice}

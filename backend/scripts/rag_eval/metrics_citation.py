"""引用指标。validity/accuracy 确定性(faithfulness 需 judge,见 metrics_generation)。"""
from __future__ import annotations

import math
import re

from scripts.rag_eval.metrics import chunk_hits

_CITE = re.compile(r"\[(\d+)\]")


def parse_citation_ids(answer: str) -> list[int]:
    seen, out = set(), []
    for m in _CITE.findall(answer or ""):
        n = int(m)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def citation_validity(answer: str, n_pool: int) -> float:
    ids = parse_citation_ids(answer)
    if not ids:
        return math.nan
    valid = sum(1 for n in ids if 1 <= n <= n_pool)
    return valid / len(ids)


def citation_accuracy(answer: str, pool, gold_spans) -> float:
    valid = [n for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]
    if not valid:
        return math.nan
    hits = sum(1 for n in valid if chunk_hits(pool[n - 1], gold_spans))
    return hits / len(valid)

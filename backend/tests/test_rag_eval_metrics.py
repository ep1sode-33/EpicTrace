# backend/tests/test_rag_eval_metrics.py
from collections import namedtuple

from scripts.rag_eval.golden import GoldSpan
from scripts.rag_eval.metrics import (
    chunk_hits, mrr, overlaps, recall_any_at_k, recall_coverage_at_k,
)

C = namedtuple("C", "ingest_record_id char_start char_end")


def test_overlaps_half_open():
    assert overlaps(10, 20, 15, 25) is True
    assert overlaps(10, 20, 20, 30) is False   # 邻接不算重叠(半开区间)
    assert overlaps(10, 20, 5, 11) is True


def test_chunk_hits_requires_same_record():
    g = (GoldSpan(1, 100, 200),)
    assert chunk_hits(C(1, 150, 250), g) is True
    assert chunk_hits(C(2, 150, 250), g) is False   # 文档不同 → 不命中


def test_recall_any_and_coverage():
    gold = (GoldSpan(1, 100, 200), GoldSpan(2, 0, 50))
    ranked = [C(9, 0, 10), C(1, 180, 260), C(5, 0, 10)]   # 命中第 1 条 gold,未命中第 2 条
    assert recall_any_at_k(ranked, gold, k=3) == 1.0
    assert recall_any_at_k(ranked, gold, k=1) == 0.0       # top-1 不含命中
    assert recall_coverage_at_k(ranked, gold, k=3) == 0.5  # 2 条 gold 命中 1 条


def test_mrr():
    gold = (GoldSpan(1, 100, 200),)
    assert mrr([C(9, 0, 1), C(1, 150, 160)], gold) == 0.5   # 第 2 名首次命中
    assert mrr([C(9, 0, 1)], gold) == 0.0                   # 无命中


import math

from scripts.rag_eval.metrics import (
    context_precision_at_k, context_precision_ordered_at_k, ndcg_at_k,
)


def test_ndcg_perfect_and_imperfect():
    gold = (GoldSpan(1, 0, 10),)
    # 命中在第 1 名 → nDCG=1.0
    assert ndcg_at_k([C(1, 0, 5), C(9, 0, 1)], gold, k=2) == 1.0
    # 命中在第 2 名 → DCG=1/log2(3), IDCG=1/log2(2)=1 → nDCG=1/log2(3)
    got = ndcg_at_k([C(9, 0, 1), C(1, 0, 5)], gold, k=2)
    assert math.isclose(got, 1.0 / math.log2(3), rel_tol=1e-9)
    assert ndcg_at_k([C(9, 0, 1)], gold, k=2) == 0.0   # 无命中


def test_context_precision_plain_and_ordered():
    gold = (GoldSpan(1, 0, 10), GoldSpan(2, 0, 10))
    ranked = [C(1, 0, 5), C(9, 0, 1), C(2, 0, 5)]      # 命中在第 1、3 名
    # plain: 命中 2 / 取前 3 = 2/3
    assert math.isclose(context_precision_at_k(ranked, gold, k=3), 2 / 3, rel_tol=1e-9)
    # ordered: (precision@1 * 1 + precision@3 * 1) / 命中数 = (1/1 + 2/3) / 2
    assert math.isclose(
        context_precision_ordered_at_k(ranked, gold, k=3), (1.0 + 2 / 3) / 2, rel_tol=1e-9)
    assert context_precision_ordered_at_k([C(9, 0, 1)], gold, k=3) == 0.0

import math
from collections import namedtuple

from scripts.rag_eval.golden import GoldSpan
from scripts.rag_eval.metrics_citation import citation_accuracy, citation_validity, parse_citation_ids

C = namedtuple("C", "ingest_record_id char_start char_end")


def test_parse_ids_in_order_unique():
    assert parse_citation_ids("据 [2] 与 [1],又见 [2]。") == [2, 1]
    assert parse_citation_ids("无引用") == []


def test_validity():
    assert citation_validity("看 [1] 和 [3]", n_pool=3) == 1.0
    assert citation_validity("看 [1] 和 [9]", n_pool=3) == 0.5    # [9] 越界
    assert math.isnan(citation_validity("无引用", n_pool=3))


def test_accuracy_uses_gold():
    gold = (GoldSpan(1, 0, 50),)
    pool = [C(1, 10, 40), C(2, 0, 10)]      # [1] 命中 gold,[2] 不命中
    assert citation_accuracy("依据 [1]", pool, gold) == 1.0
    assert citation_accuracy("依据 [2]", pool, gold) == 0.0
    assert citation_accuracy("依据 [1] 和 [2]", pool, gold) == 0.5
    assert math.isnan(citation_accuracy("无引用", pool, gold))
    assert math.isnan(citation_accuracy("越界 [9]", pool, gold))   # 无合法引用

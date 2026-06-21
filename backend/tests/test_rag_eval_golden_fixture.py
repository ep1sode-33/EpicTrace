# backend/tests/test_rag_eval_golden_fixture.py
from pathlib import Path

from scripts.rag_eval.golden import load_golden

FIX = Path(__file__).parent / "fixtures" / "rag_eval" / "golden.jsonl"


def test_fixture_loads_and_has_slice_coverage():
    items = load_golden(FIX)
    assert len(items) >= 6
    qtypes = {it.slices.get("q_type") for it in items}
    assert {"single_hop", "multi_hop", "negation"} <= qtypes
    langs = {it.slices.get("lang") for it in items}
    assert {"zh", "en"} <= langs
    # 多跳题至少一条有 ≥2 个 gold 跨度;否定题参考答案为拒答语义。
    assert any(len(it.gold_spans) >= 2 for it in items if it.slices.get("q_type") == "multi_hop")
    assert all(it.id for it in items) and len({it.id for it in items}) == len(items)

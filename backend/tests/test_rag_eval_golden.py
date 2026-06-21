from scripts.rag_eval.golden import GoldItem, GoldSpan, load_golden, save_golden


def test_round_trip(tmp_path):
    items = [
        GoldItem(
            id="g0001", question="什么是缓存命中率?",
            gold_spans=(GoldSpan(12, 100, 240),),
            reference_answer="命中数除以总访问数。",
            slices={"domain": "study-cs", "doc_type": "pdf", "lang": "zh", "q_type": "single_hop"},
            provenance="hand", source="own", corpus_version="v1",
        ),
        GoldItem(
            id="g0002", question="multi-hop example",
            gold_spans=(GoldSpan(3, 0, 50), GoldSpan(7, 80, 130)),
            reference_answer="...", slices={"q_type": "multi_hop"},
            provenance="hand", source="own", corpus_version="v1",
        ),
    ]
    p = tmp_path / "golden.jsonl"
    save_golden(items, p)
    loaded = load_golden(p)
    assert loaded == items
    assert loaded[1].gold_spans[1].doc_char_start == 80


def test_load_skips_blank_lines(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('\n', encoding="utf-8")
    assert load_golden(p) == []

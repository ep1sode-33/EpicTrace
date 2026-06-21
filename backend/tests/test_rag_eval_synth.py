from scripts.rag_eval.synth import is_leaky, is_self_contained, map_support_to_span, synth_item


class _FakeGen:
    def __init__(self, reply):
        self._reply = reply

    def judge_json(self, system, user):
        return self._reply


def test_filters():
    assert is_self_contained("缓存命中率怎么算?") is True
    assert is_self_contained("这段讲了什么?") is False
    assert is_leaky("请背诵:命中率等于命中次数除以总访问次数", "命中率等于命中次数除以总访问次数啊") is True
    assert is_leaky("缓存命中率怎么算?", "命中率等于命中次数除以总访问次数") is False


def test_map_support_to_span():
    doc = "前言。命中率 = 命中 / 总访问。结语。"
    s = map_support_to_span(doc, "命中率 = 命中 / 总访问")
    assert s is not None and doc[s[0]:s[1]] == "命中率 = 命中 / 总访问"
    assert map_support_to_span(doc, "不存在的句子") is None


def test_synth_item_ok_and_rejects():
    doc = "略。命中率 = 命中 / 总访问。略。"
    good = _FakeGen({"question": "缓存命中率怎么算?", "reference_answer": "命中除以总访问",
                     "support_sentence": "命中率 = 命中 / 总访问"})
    it = synth_item(good, item_id="g100", ingest_record_id=7, doc_text=doc,
                    chunk_text=doc, slices={"lang": "zh"}, corpus_version="v1")
    assert it is not None and it.gold_spans[0].ingest_record_id == 7
    assert doc[it.gold_spans[0].doc_char_start:it.gold_spans[0].doc_char_end] == "命中率 = 命中 / 总访问"

    leaky = _FakeGen({"question": "背:命中率 = 命中 / 总访问", "reference_answer": "x",
                      "support_sentence": "命中率 = 命中 / 总访问"})
    assert synth_item(leaky, item_id="g101", ingest_record_id=7, doc_text=doc,
                      chunk_text=doc, slices={}, corpus_version="v1") is None
    assert synth_item(_FakeGen(None), item_id="g102", ingest_record_id=7, doc_text=doc,
                      chunk_text=doc, slices={}, corpus_version="v1") is None

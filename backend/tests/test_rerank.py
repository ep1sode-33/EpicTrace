from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeReranker


def _c(rid, text):
    return RetrievedChunk(text=text, ingest_record_id=rid, project_id=7,
                          char_start=0, char_end=len(text), source_type="folder_scan")


def test_fake_reranker_orders_by_query_substring_and_truncates_top_k():
    chunks = [_c(1, "无关内容"), _c(2, "命中 关键词 命中"), _c(3, "命中 一次")]
    out = FakeReranker().rerank("关键词", chunks, top_k=2)
    assert len(out) == 2
    assert out[0].ingest_record_id == 2  # 命中最多排第一

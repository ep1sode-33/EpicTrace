from epictrace.retrieval.pipeline import HybridRetriever
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _rec(rid, text):
    return {"vector": FakeEmbedder().embed([text])[0], "text": text, "ingest_record_id": rid,
            "project_id": 7, "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
            "embed_model_id": "fake"}


def test_hybrid_retrieve_returns_top_k_reranked():
    store = FakeVectorStore()
    store.upsert([_rec(1, "虚拟内存 页表 分页"), _rec(2, "数据库 事务"), _rec(3, "页表 缺页")])
    r = HybridRetriever(FakeEmbedder(), store, FakeReranker())
    out = r.retrieve(project_id=7, query="页表", k=2)
    assert len(out) == 2
    assert out[0].ingest_record_id in {1, 3}  # 含"页表"的排前
    assert all(hasattr(c, "char_start") for c in out)

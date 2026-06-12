from epictrace.retrieval.dense import dense_search
from epictrace.retrieval.sparse import sparse_search
from epictrace.retrieval.pipeline import HybridRetriever
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _rows(store):
    for ing, text in [(10, "页表 映射"), (20, "缓存 一致性"), (30, "页表 替换")]:
        store.upsert([{"vector": [0.0] * 1024, "text": text, "ingest_record_id": ing,
                       "project_id": 1, "char_start": 0, "char_end": len(text),
                       "source_type": "folder_scan"}])


def test_dense_search_scopes_to_focus_ids():
    store = FakeVectorStore(); _rows(store)
    hits = dense_search(FakeEmbedder(), store, project_id=1, query="页表", k=10,
                        ingest_record_ids=[10])
    assert {h.ingest_record_id for h in hits} == {10}


def test_sparse_search_scopes_to_focus_ids():
    store = FakeVectorStore(); _rows(store)
    hits = sparse_search(store, project_id=1, query="页表", k=10, ingest_record_ids=[30])
    assert all(h.ingest_record_id == 30 for h in hits)


def test_hybrid_retriever_threads_focus_ids():
    store = FakeVectorStore(); _rows(store)
    r = HybridRetriever(FakeEmbedder(), store, FakeReranker())
    hits = r.retrieve(project_id=1, query="页表", ingest_record_ids=[10, 30])
    assert {h.ingest_record_id for h in hits} <= {10, 30}

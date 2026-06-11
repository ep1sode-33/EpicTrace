from epictrace.retrieval.sparse import sparse_search
from tests.fakes import FakeVectorStore


def _rec(rid, text):
    return {"vector": [0.0], "text": text, "ingest_record_id": rid, "project_id": 7,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan", "embed_model_id": "fake"}


def test_sparse_search_ranks_by_keyword_overlap():
    store = FakeVectorStore()
    store.upsert([_rec(1, "操作系统 虚拟内存 页表"), _rec(2, "数据库 事务 隔离级别"),
                  _rec(3, "虚拟内存 分页 缺页中断")])
    out = sparse_search(store, project_id=7, query="虚拟内存 页表", k=2)
    ids = [c.ingest_record_id for c in out]
    assert 2 not in ids  # 无关项不该进 top-2
    assert set(ids) <= {1, 3}


def test_sparse_search_empty_project_returns_empty():
    assert sparse_search(FakeVectorStore(), project_id=7, query="x", k=5) == []

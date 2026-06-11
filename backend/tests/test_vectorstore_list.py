from pathlib import Path

from epictrace.vectorstore import milvus_lite
from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _rec(pid, rid, text):
    return {"vector": [0.1] * DIM, "text": text, "ingest_record_id": rid, "project_id": pid,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan", "embed_model_id": "fake"}


def test_list_by_project_returns_only_that_project(tmp_path: Path):
    s = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    s.upsert([_rec(7, 1, "alpha"), _rec(7, 2, "beta"), _rec(8, 3, "gamma")])
    rows = s.list_by_project(7)
    assert {r["text"] for r in rows} == {"alpha", "beta"}
    assert all(r["project_id"] == 7 for r in rows)
    assert {"char_start", "char_end", "ingest_record_id"} <= set(rows[0])


def test_list_by_project_warns_on_limit_truncation(caplog):
    # 行数正好等于硬上限 → 视为可能被截断,记一条 warning(BM25 语料不完整的可见信号)。
    # 用轻量 fake client 避免插 16384 行真数据(太慢);只验告警逻辑。
    class _FakeClient:
        def query(self, *a, **k):
            return [{"project_id": 7}] * milvus_lite._LIST_LIMIT

    s = MilvusLiteStore.__new__(MilvusLiteStore)
    s._client = _FakeClient()
    with caplog.at_level("WARNING", logger="epictrace"):
        rows = s.list_by_project(7)
    assert len(rows) == milvus_lite._LIST_LIMIT
    assert any("可能被截断" in r.message for r in caplog.records)

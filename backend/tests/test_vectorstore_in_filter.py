from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _rec(rid: int, ing: int, text: str) -> dict:
    return {"vector": [0.1] * DIM, "text": text, "ingest_record_id": ing, "project_id": 1,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
            "embed_model_id": "fake"}


def test_query_filters_by_ingest_record_id_in_list(tmp_path: Path):
    s = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    s.upsert([_rec(1, 10, "甲"), _rec(2, 20, "乙"), _rec(3, 30, "丙")])
    hits = s.query([0.1] * DIM, filter={"project_id": 1, "ingest_record_id": [10, 30]}, k=10)
    got = sorted(h["ingest_record_id"] for h in hits)
    assert got == [10, 30]                       # 只命中聚焦的两个文件
    s.close()

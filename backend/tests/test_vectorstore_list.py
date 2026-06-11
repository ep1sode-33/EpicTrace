from pathlib import Path

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

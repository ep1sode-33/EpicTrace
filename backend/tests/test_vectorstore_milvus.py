from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _vec(seed: float) -> list[float]:
    return [seed] * DIM


def test_upsert_query_roundtrip(tmp_path: Path):
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    store.upsert([
        {"vector": _vec(0.1), "text": "alpha", "ingest_record_id": 1, "project_id": 7,
         "char_start": 0, "char_end": 5, "source_type": "folder_scan", "embed_model_id": "fake"},
        {"vector": _vec(0.9), "text": "omega", "ingest_record_id": 2, "project_id": 7,
         "char_start": 0, "char_end": 5, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    hits = store.query(_vec(0.1), filter={"project_id": 7}, k=1)
    assert len(hits) == 1
    assert hits[0]["text"] == "alpha"


def test_filter_by_project(tmp_path: Path):
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    store.upsert([
        {"vector": _vec(0.5), "text": "p7", "ingest_record_id": 1, "project_id": 7,
         "char_start": 0, "char_end": 2, "source_type": "folder_scan", "embed_model_id": "fake"},
        {"vector": _vec(0.5), "text": "p8", "ingest_record_id": 2, "project_id": 8,
         "char_start": 0, "char_end": 2, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    hits = store.query(_vec(0.5), filter={"project_id": 8}, k=5)
    assert {h["text"] for h in hits} == {"p8"}


def test_delete_by_record(tmp_path: Path):
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    store.upsert([
        {"vector": _vec(0.3), "text": "keep", "ingest_record_id": 1, "project_id": 7,
         "char_start": 0, "char_end": 4, "source_type": "folder_scan", "embed_model_id": "fake"},
        {"vector": _vec(0.3), "text": "gone", "ingest_record_id": 2, "project_id": 7,
         "char_start": 0, "char_end": 4, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    store.delete_by_record(2)
    hits = store.query(_vec(0.3), filter={"project_id": 7}, k=10)
    assert {h["text"] for h in hits} == {"keep"}

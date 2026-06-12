from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore, _ATTACHMENT_SCALARS

DIM = 1024


def _arec(cid: int, rid: int, text: str) -> dict:
    return {"vector": [0.1] * DIM, "text": text, "conversation_id": cid, "reference_id": rid,
            "char_start": 0, "char_end": len(text), "source_type": "attachment",
            "embed_model_id": "fake"}


def test_attachment_collection_roundtrip_filter_and_cleanup(tmp_path: Path):
    db = str(tmp_path / "v.db")
    store = MilvusLiteStore(db_path=db, dim=DIM, collection="attachment_chunks",
                            scalars=_ATTACHMENT_SCALARS)
    store.upsert([_arec(1, 10, "页表"), _arec(1, 20, "缓存"), _arec(2, 30, "无关")])
    rows = store.list_by({"conversation_id": 1, "reference_id": [10, 20]})
    assert {r["reference_id"] for r in rows} == {10, 20}
    hits = store.query([0.1] * DIM, filter={"conversation_id": 1, "reference_id": [10]}, k=10)
    assert [h["reference_id"] for h in hits] == [10]
    store.delete({"reference_id": 10})
    assert {r["reference_id"] for r in store.list_by({"conversation_id": 1})} == {20}
    store.delete({"conversation_id": 1})
    assert store.list_by({"conversation_id": 1}) == []
    store.close()


def test_default_chunks_collection_still_works(tmp_path: Path):
    s = MilvusLiteStore(db_path=str(tmp_path / "c.db"), dim=DIM)
    s.upsert([{"vector": [0.1] * DIM, "text": "x", "ingest_record_id": 1, "project_id": 7,
               "char_start": 0, "char_end": 1, "source_type": "folder_scan", "embed_model_id": "f"}])
    assert len(s.list_by_project(7)) == 1
    s.close()

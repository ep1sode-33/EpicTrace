from epictrace.retrieval.dense import dense_search
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeEmbedder, FakeVectorStore


def test_dense_search_embeds_query_and_returns_chunks():
    store = FakeVectorStore()
    store.upsert([
        {"vector": FakeEmbedder().embed(["alpha"])[0], "text": "alpha", "ingest_record_id": 1,
         "project_id": 7, "char_start": 0, "char_end": 5, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    out = dense_search(FakeEmbedder(), store, project_id=7, query="alpha", k=5)
    assert out and out[0].text == "alpha"
    assert out[0].project_id == 7 and out[0].ingest_record_id == 1


def _row(**extra):
    row = {"text": "t", "ingest_record_id": 1, "project_id": 7,
           "char_start": 0, "char_end": 1, "source_type": "folder_scan"}
    row.update(extra)
    return row


def test_from_row_normalizes_capture_sentinels_to_none():
    # 哨兵 0 / "" → None(非会话来源的行不该冒充会话溯源)
    c = RetrievedChunk.from_row(_row(capture_session_id=0, ts=""))
    assert c.capture_session_id is None and c.ts is None


def test_from_row_passes_through_real_capture_values():
    c = RetrievedChunk.from_row(_row(capture_session_id=12, ts="2026-07-11T03:00:00"))
    assert c.capture_session_id == 12 and c.ts == "2026-07-11T03:00:00"


def test_from_row_missing_capture_keys_default_none():
    # 附件行根本没有这两个键 → get 返回 None
    c = RetrievedChunk.from_row(_row())
    assert c.capture_session_id is None and c.ts is None

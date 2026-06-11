from epictrace.retrieval.dense import dense_search
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

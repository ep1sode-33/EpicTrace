from epictrace.retrieval.attachment import AttachmentRetriever
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _seed(store, cid):
    for rid, text in [(10, "页表 映射 地址"), (10, "缓存 一致性"), (20, "别的引用 页表")]:
        store.upsert([{"vector": [0.0] * 1024, "text": text, "conversation_id": cid,
                       "reference_id": rid, "char_start": 0, "char_end": len(text),
                       "source_type": "attachment", "embed_model_id": "fake"}])


def test_retrieve_scoped_to_conversation_and_references():
    store = FakeVectorStore(); _seed(store, cid=1)
    r = AttachmentRetriever(FakeEmbedder(), store, FakeReranker())
    hits = r.retrieve(conversation_id=1, reference_ids=[10], query="页表", k=6)
    assert hits and all(h.source_kind == "attachment" for h in hits)
    assert all(h.reference_id == 10 for h in hits)
    assert all(h.char_start is not None for h in hits)


def test_empty_when_no_reference_ids():
    store = FakeVectorStore(); _seed(store, cid=1)
    r = AttachmentRetriever(FakeEmbedder(), store, FakeReranker())
    assert r.retrieve(conversation_id=1, reference_ids=[], query="页表", k=6) == []

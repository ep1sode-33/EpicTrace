import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EPICTRACE_RUN_SLOW") != "1",
    reason="真 BGE-M3 冒烟:需下载模型,设 EPICTRACE_RUN_SLOW=1 才跑",
)


def test_real_bge_m3_embed_store_query_roundtrip(tmp_path):
    """真模型走全链 + 断言维度 == collection 维度(兜契约/维度/归一化漂移)。"""
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    emb = BgeM3Embedder()
    vecs = emb.embed(["虚拟内存如何工作", "完全无关的内容:量子色动力学"])
    assert len(vecs[0]) == 1024                      # 真实维度 == collection 的 1024

    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    store.upsert([
        {"vector": vecs[0], "text": "虚拟内存", "ingest_record_id": 1, "project_id": 1,
         "char_start": 0, "char_end": 4, "source_type": "folder_scan", "embed_model_id": emb.model_id},
        {"vector": vecs[1], "text": "量子色动力学", "ingest_record_id": 2, "project_id": 1,
         "char_start": 0, "char_end": 6, "source_type": "folder_scan", "embed_model_id": emb.model_id},
    ])
    q = emb.embed(["虚拟内存"])[0]
    hits = store.query(q, filter={"project_id": 1}, k=1)
    assert hits[0]["text"] == "虚拟内存"             # 最近的应是语义相近的那条

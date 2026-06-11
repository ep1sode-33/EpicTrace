import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("EPICTRACE_RUN_SLOW") != "1",
                                reason="真 embedder+reranker;设 EPICTRACE_RUN_SLOW=1")


def test_real_hybrid_retrieve_end_to_end(tmp_path: Path):
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.retrieval.pipeline import HybridRetriever
    from epictrace.retrieval.rerank import BgeReranker
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    emb = BgeM3Embedder(); emb.warmup()
    rer = BgeReranker(); rer.warmup()                      # 两个模型都在 Milvus 前加载
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    for i, t in enumerate(["虚拟内存通过页表把虚拟地址映射到物理地址", "数据库事务的隔离级别", "缺页中断与按需调页"]):
        store.upsert([{ "vector": emb.embed([t])[0], "text": t, "ingest_record_id": i + 1, "project_id": 1,
                        "char_start": 0, "char_end": len(t), "source_type": "folder_scan", "embed_model_id": "bge-m3" }])
    out = HybridRetriever(emb, store, rer).retrieve(project_id=1, query="页表怎么映射地址", k=2)
    assert out and "页表" in out[0].text       # 进程没崩 + 语义最相关排第一

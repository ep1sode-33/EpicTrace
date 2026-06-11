"""真模型回归测试:防止 'gRPC(Milvus)激活后再加载 BGE-M3 → fork 段错误'。

按 app 的真实顺序跑 IndexService(真 BGE-M3 + 延迟构造的真 Milvus Lite):
_run 必须先 warmup 模型、再构造 Milvus,否则段错误。默认跳过(需下/载模型)。
跑:`EPICTRACE_RUN_SLOW=1 HF_HUB_OFFLINE=1 .venv/bin/pytest tests/test_index_real_smoke.py -v`
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EPICTRACE_RUN_SLOW") != "1",
    reason="真 BGE-M3 + Milvus 全链回归:需模型,设 EPICTRACE_RUN_SLOW=1 才跑",
)


def test_index_real_flow_app_order_no_segfault(tmp_path):
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.services.index import IndexService
    from epictrace.services.ingest import IngestService
    from epictrace.services.projects import ProjectService
    from epictrace.services.scan import ScanService
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    (Path(proj.folder_path) / "note.md").write_text("虚拟内存与页表 " * 50, encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)

    # 像 router 那样:store 用 getter 延迟构造,embedder 真件。
    holder: dict = {}

    def get_store() -> MilvusLiteStore:
        if "s" not in holder:
            holder["s"] = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
        return holder["s"]

    svc = IndexService(db, BgeM3Embedder(), get_store)
    job = svc.index_project(proj.id)
    svc._run(job)  # 同步:warmup → 建 Milvus → 嵌入 → 入库;顺序错会段错误(进程崩溃)

    assert job.status == "done"
    assert job.done == 1
    recs = IngestService(db).list_for_project(proj.id)
    assert all(r.indexed for r in recs)
    # 向量确实进了库(就地构造的 store)
    hits = get_store().query(BgeM3Embedder().embed(["页表"])[0], filter={"project_id": proj.id}, k=1)
    assert len(hits) >= 1


def test_get_vector_store_warms_model_before_milvus(tmp_path, monkeypatch):
    """回归(段错误复发):任何路径(删除/索引/RAG)首次构造 Milvus 时,
    get_vector_store 必须先 warmup 模型、再起 gRPC。否则"gRPC 先起、再 fork 加载模型"段错误。"""
    from types import SimpleNamespace

    import epictrace.api.deps as deps
    from epictrace.config import AppConfig

    monkeypatch.setattr(
        AppConfig, "milvus_path", property(lambda self: str(tmp_path / "v.db"))
    )
    req = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(embedder=None, reranker=None, vector_store=None))
    )

    store = deps.get_vector_store(req)  # 危险顺序:首次就构造 Milvus;fix 应先暖模型
    assert req.app.state.embedder is not None  # 模型必须已在构造 Milvus 之前加载好

    emb = deps.get_embedder(req)
    v = emb.embed(["页表"])
    store.upsert(
        [
            {
                "vector": v[0], "text": "x", "ingest_record_id": 1, "project_id": 1,
                "char_start": 0, "char_end": 1, "source_type": "folder_scan",
                "embed_model_id": emb.model_id,
            }
        ]
    )  # 跑到这里进程没崩 = 顺序正确


def test_hybrid_retrieve_real_models_no_segfault(tmp_path, monkeypatch):
    """真 embedder + 真 reranker + 真 Milvus 同进程检索一条,不应段错误。"""
    from types import SimpleNamespace

    import epictrace.api.deps as deps
    from epictrace.config import AppConfig
    from epictrace.retrieval.pipeline import HybridRetriever

    monkeypatch.setattr(AppConfig, "milvus_path", property(lambda self: str(tmp_path / "v.db")))
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(embedder=None, reranker=None, vector_store=None)))
    store = deps.get_vector_store(req)  # 先暖 embedder+reranker 再起 Milvus
    emb = deps.get_embedder(req)
    store.upsert([{ "vector": emb.embed(["虚拟内存 页表"])[0], "text": "虚拟内存 页表", "ingest_record_id": 1,
                    "project_id": 7, "char_start": 0, "char_end": 6, "source_type": "folder_scan",
                    "embed_model_id": emb.model_id }])
    out = HybridRetriever(emb, store, deps.get_reranker(req)).retrieve(project_id=7, query="页表", k=3)
    assert out and out[0].ingest_record_id == 1  # 进程没崩 + 检到

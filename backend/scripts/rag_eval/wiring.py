"""真生产组件装配(懒导入重依赖:FlagEmbedding / Milvus / reranker)。仅 CLI 真跑时调用。"""
from __future__ import annotations


def build_retriever(project_id: int):
    # 懒导入:测试/纯逻辑路径不拉重依赖。
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.retrieval.pipeline import HybridRetriever
    from epictrace.retrieval.rerank import BgeReranker
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    embedder = BgeM3Embedder()
    reranker = BgeReranker()
    embedder.warmup()          # 必须在建 Milvus(gRPC)之前 warmup,避免 macOS fork 段错误
    reranker.warmup()
    store = MilvusLiteStore()  # 默认数据目录;eval 索引在该库内,project_id 区隔
    return HybridRetriever(embedder, store, reranker)

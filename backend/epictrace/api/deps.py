from __future__ import annotations

import threading

from fastapi import Request

from epictrace.db import Database

# 串行化 vector store 的首次构造:避免并发两次构造抢 milvus-lite 的独占文件锁。
_vector_store_lock = threading.Lock()


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_embedder(request: Request):
    """延迟构造默认 embedder:仅在索引路由首次用到时才起真件(BGE-M3)。"""
    embedder = request.app.state.embedder
    if embedder is None:
        from epictrace.embedding.bge_m3 import BgeM3Embedder

        embedder = BgeM3Embedder()
        request.app.state.embedder = embedder
    return embedder


def get_reranker(request: Request):
    """延迟构造默认 reranker(BGE-reranker-v2)。同 get_embedder 模式:首次用到才起真件,
    且与 embedder 一样必须在任何 Milvus/gRPC 之前 warmup(见 macos-embedding-milvus-fork-order)。"""
    reranker = getattr(request.app.state, "reranker", None)
    if reranker is None:
        from epictrace.retrieval.rerank import BgeReranker

        reranker = BgeReranker()
        request.app.state.reranker = reranker
    return reranker


def get_vector_store(request: Request):
    """延迟构造默认 vector store(Milvus Lite),并保证"模型先加载、再起 gRPC"。

    macOS 上:milvus-lite 的 gRPC 客户端激活后,再 fork 加载 BGE-M3 / reranker 模型会段错误。
    所有首次用到 Milvus 的路径(索引 / 删除 / RAG 查询)都经过这里,所以在构造 Milvus
    之前先 warmup embedding 与 reranker 模型(此时进程内还没有任何 gRPC),全局保证顺序安全。
    用锁串行化,避免并发两次构造抢 milvus-lite 的独占文件锁。"""
    store = request.app.state.vector_store
    if store is not None:
        return store
    with _vector_store_lock:
        store = request.app.state.vector_store
        if store is None:
            get_embedder(request).warmup()  # 先加载 embedding 模型(此时无 gRPC)
            get_reranker(request).warmup()  # 再加载 reranker 模型(仍无 gRPC)
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore

            store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024)
            request.app.state.vector_store = store
    return store


def get_llm(request: Request):
    """已注入的 chat LLM(默认 None)。完整的 SettingsService 接线在后续 Task 完成;
    此处仅读 app.state.llm,未配置时返回 None,由路由处理"未配置"。"""
    return getattr(request.app.state, "llm", None)


def get_retriever(request: Request):
    """混合检索器:dense + sparse → RRF → rerank。复用延迟构造的 embedder / store / reranker。"""
    from epictrace.retrieval.pipeline import HybridRetriever

    return HybridRetriever(
        get_embedder(request),
        get_vector_store(request),
        get_reranker(request),
    )

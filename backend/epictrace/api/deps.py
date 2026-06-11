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


def get_vector_store(request: Request):
    """延迟构造默认 vector store(Milvus Lite),并保证"模型先加载、再起 gRPC"。

    macOS 上:milvus-lite 的 gRPC 客户端激活后,再 fork 加载 BGE-M3 模型会段错误。
    所有首次用到 Milvus 的路径(索引 / 删除 / 将来 RAG 查询)都经过这里,所以在构造
    Milvus 之前先 warmup embedding 模型(此时进程内还没有任何 gRPC),全局保证顺序安全。
    用锁串行化,避免并发两次构造抢 milvus-lite 的独占文件锁。"""
    store = request.app.state.vector_store
    if store is not None:
        return store
    with _vector_store_lock:
        store = request.app.state.vector_store
        if store is None:
            get_embedder(request).warmup()  # 先加载模型(此时无 gRPC),再起 Milvus
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore

            store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024)
            request.app.state.vector_store = store
    return store

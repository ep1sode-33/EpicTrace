from __future__ import annotations

from fastapi import Request

from epictrace.db import Database


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
    """延迟构造默认 vector store:仅在索引路由首次用到时才起 Milvus Lite。"""
    store = request.app.state.vector_store
    if store is None:
        from epictrace.config import AppConfig
        from epictrace.vectorstore.milvus_lite import MilvusLiteStore

        store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024)
        request.app.state.vector_store = store
    return store

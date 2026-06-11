from __future__ import annotations

from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.dense import dense_search
from epictrace.retrieval.fuse import rrf_fuse
from epictrace.retrieval.sparse import sparse_search
from epictrace.retrieval.types import RetrievedChunk


class HybridRetriever:
    def __init__(self, embedder: EmbeddingProvider, store: VectorStore, reranker) -> None:
        self._embedder = embedder
        self._store = store
        self._reranker = reranker

    def retrieve(self, *, project_id: int, query: str, k: int = 6,
                 dense_n: int = 30, fuse_m: int = 20) -> list[RetrievedChunk]:
        dense = dense_search(self._embedder, self._store, project_id=project_id, query=query, k=dense_n)
        sparse = sparse_search(self._store, project_id=project_id, query=query, k=dense_n)
        fused = rrf_fuse([dense, sparse], k=fuse_m)
        if not fused:
            return []
        return self._reranker.rerank(query, fused, top_k=k)

from __future__ import annotations

import threading

from epictrace.retrieval.types import RetrievedChunk


class BgeReranker:
    """BGE-reranker-v2 cross-encoder。懒加载;务必在任何 Milvus/gRPC 之前 warmup
    (torch 加载会 fork,见 macos-embedding-milvus-fork-order)。"""

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from FlagEmbedding import FlagReranker
                    self._model = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
        return self._model

    def warmup(self) -> None:
        self._ensure()

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int = 6) -> list[RetrievedChunk]:
        if not chunks:
            return []
        model = self._ensure()
        scores = model.compute_score([[query, c.text] for c in chunks], normalize=True)
        if not isinstance(scores, list):
            scores = [scores]
        for c, s in zip(chunks, scores):
            c.score = float(s)
        return sorted(chunks, key=lambda c: c.score, reverse=True)[:top_k]

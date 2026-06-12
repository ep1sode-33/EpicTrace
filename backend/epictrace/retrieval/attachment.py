from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from epictrace.retrieval.fuse import rrf_fuse
from epictrace.retrieval.types import RetrievedChunk


def _row_to_chunk(row: dict, score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        text=row["text"], ingest_record_id=0, project_id=0,
        char_start=row["char_start"], char_end=row["char_end"],
        source_type="attachment", score=score,
        source_kind="attachment", reference_id=row["reference_id"],
    )


def _tok(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


class AttachmentRetriever:
    """对会话级临时集合做 dense+sparse→RRF→rerank,按 conversation_id + reference_id 过滤。
    与项目 HybridRetriever 同形,但作用于附件向量、产出 source_kind=attachment 的 chunk。"""

    def __init__(self, embedder, store, reranker) -> None:
        self._embedder = embedder
        self._store = store
        self._reranker = reranker

    def retrieve(self, *, conversation_id: int, reference_ids: list[int], query: str,
                 k: int = 6, dense_n: int = 30, fuse_m: int = 20) -> list[RetrievedChunk]:
        if not reference_ids:
            return []
        flt = {"conversation_id": conversation_id, "reference_id": list(reference_ids)}
        vec = self._embedder.embed([query])[0]
        dense_rows = self._store.query(vec, filter=flt, k=dense_n)
        dense = [_row_to_chunk(r, score=1.0 / (i + 1)) for i, r in enumerate(dense_rows)]
        rows = self._store.list_by(flt)
        sparse: list[RetrievedChunk] = []
        if rows:
            bm25 = BM25Okapi([_tok(r["text"]) for r in rows])
            scores = bm25.get_scores(_tok(query))
            ranked = sorted(zip(rows, scores), key=lambda rs: rs[1], reverse=True)[:dense_n]
            sparse = [_row_to_chunk(r, score=float(s)) for r, s in ranked if s > 0]
        fused = rrf_fuse([dense, sparse], k=fuse_m)
        if not fused:
            return []
        return self._reranker.rerank(query, fused, top_k=k)

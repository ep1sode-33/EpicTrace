from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.types import RetrievedChunk


def _tok(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


def sparse_search(store: VectorStore, *, project_id: int, query: str, k: int = 30) -> list[RetrievedChunk]:
    rows = store.list_by_project(project_id)
    if not rows:
        return []
    corpus = [_tok(r["text"]) for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tok(query))
    ranked = sorted(zip(rows, scores), key=lambda rs: rs[1], reverse=True)[:k]
    return [RetrievedChunk.from_row(r, score=float(s)) for r, s in ranked if s > 0]

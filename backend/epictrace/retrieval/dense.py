from __future__ import annotations

from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.types import RetrievedChunk


def dense_search(embedder: EmbeddingProvider, store: VectorStore, *, project_id: int,
                 query: str, k: int = 30) -> list[RetrievedChunk]:
    vec = embedder.embed([query])[0]
    rows = store.query(vec, filter={"project_id": project_id}, k=k)
    return [RetrievedChunk.from_row(r, score=1.0 / (i + 1)) for i, r in enumerate(rows)]

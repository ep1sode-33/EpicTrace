from __future__ import annotations

from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.types import RetrievedChunk


def dense_search(embedder: EmbeddingProvider, store: VectorStore, *, project_id: int,
                 query: str, k: int = 30, ingest_record_ids: list[int] | None = None) -> list[RetrievedChunk]:
    vec = embedder.embed([query])[0]
    flt: dict = {"project_id": project_id}
    if ingest_record_ids:
        flt["ingest_record_id"] = list(ingest_record_ids)
    rows = store.query(vec, filter=flt, k=k)
    return [RetrievedChunk.from_row(r, score=1.0 / (i + 1)) for i, r in enumerate(rows)]

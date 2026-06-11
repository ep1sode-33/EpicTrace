from __future__ import annotations

from epictrace.retrieval.types import RetrievedChunk

_K0 = 60  # RRF 常数


def rrf_fuse(ranked_lists: list[list[RetrievedChunk]], k: int = 20) -> list[RetrievedChunk]:
    scores: dict[tuple, float] = {}
    keep: dict[tuple, RetrievedChunk] = {}
    for lst in ranked_lists:
        for rank, chunk in enumerate(lst):
            key = chunk.key()
            scores[key] = scores.get(key, 0.0) + 1.0 / (_K0 + rank)
            keep.setdefault(key, chunk)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    out = []
    for key, score in ordered:
        c = keep[key]
        c.score = score
        out.append(c)
    return out

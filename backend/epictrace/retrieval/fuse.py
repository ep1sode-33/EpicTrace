from __future__ import annotations

from epictrace.retrieval.types import RetrievedChunk

_K0 = 60  # RRF 常数


def rrf_fuse(ranked_lists: list[list[RetrievedChunk]], k: int = 20) -> list[RetrievedChunk]:
    scores: dict[tuple, float] = {}
    keep: dict[tuple, RetrievedChunk] = {}
    for lst in ranked_lists:
        # start=1:排名从 1 起算,故榜首得分 1/(60+1)(规范 RRF;rank 0 会让分母只有 60、偏离公式)。
        for rank, chunk in enumerate(lst, start=1):
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

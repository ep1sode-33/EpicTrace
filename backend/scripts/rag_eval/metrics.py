"""检索指标(确定性、免 LLM)。chunk「命中」gold = 同 ingest_record_id 且 char 区间重叠(半开)。"""
from __future__ import annotations


def overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    """半开区间 [a0,a1) 与 [b0,b1) 是否重叠。邻接(a1==b0)不算。"""
    return a0 < b1 and b0 < a1


def chunk_hits(chunk, gold_spans) -> bool:
    return any(
        chunk.ingest_record_id == g.ingest_record_id
        and overlaps(chunk.char_start, chunk.char_end, g.doc_char_start, g.doc_char_end)
        for g in gold_spans
    )


def recall_any_at_k(ranked, gold_spans, k: int) -> float:
    """top-k 内有任一命中 = 1.0 否则 0.0。"""
    return 1.0 if any(chunk_hits(c, gold_spans) for c in ranked[:k]) else 0.0


def recall_coverage_at_k(ranked, gold_spans, k: int) -> float:
    """多跳:top-k 命中的 gold 跨度数 / 总 gold 跨度数。"""
    if not gold_spans:
        return 0.0
    top = ranked[:k]
    covered = sum(
        1 for g in gold_spans
        if any(c.ingest_record_id == g.ingest_record_id
               and overlaps(c.char_start, c.char_end, g.doc_char_start, g.doc_char_end)
               for c in top)
    )
    return covered / len(gold_spans)


def mrr(ranked, gold_spans) -> float:
    """第一个命中 chunk 名次的倒数(rank 从 1 起);无命中 = 0.0。"""
    for i, c in enumerate(ranked, start=1):
        if chunk_hits(c, gold_spans):
            return 1.0 / i
    return 0.0

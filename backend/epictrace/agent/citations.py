from __future__ import annotations

import re

from epictrace.retrieval.types import RetrievedChunk

_CITE = re.compile(r"\[(\d+)\]")
_SNIPPET = 160


def build_citations(answer: str, chunks: list[RetrievedChunk]) -> list[dict]:
    """从答案里抽 [n],映射到第 n 个 chunk(1-based);只保留有效且实际出现的。"""
    used = sorted({int(m) for m in _CITE.findall(answer)})
    out = []
    for n in used:
        if 1 <= n <= len(chunks):
            c = chunks[n - 1]
            out.append({
                "n": n, "ingest_record_id": c.ingest_record_id,
                "char_start": c.char_start, "char_end": c.char_end,
                "source_type": c.source_type,
                "snippet": c.text[:_SNIPPET],
            })
    return out

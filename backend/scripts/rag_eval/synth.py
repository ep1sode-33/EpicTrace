"""LLM golden 合成:采样 chunk → 让模型出题+参考答案+支撑句 → 支撑句映射回文档偏移 = gold 跨度。
自动过滤泄漏/指代/不可定位。生成模型 injectable(默认 AnthropicJudge.judge_json)。"""
from __future__ import annotations

from scripts.rag_eval.golden import GoldItem, GoldSpan

_DANGLING = ("这段", "上文", "下文", "上述", "如图", "如下", "前面", "above", "below", "the passage", "this section")
_SYS = "你是 RAG 评测出题助手。只输出 JSON,不要多余文字。"


def is_self_contained(question: str) -> bool:
    q = (question or "").lower()
    return not any(tok.lower() in q for tok in _DANGLING)


def is_leaky(question: str, chunk_text: str, *, n: int = 12) -> bool:
    """题面逐字抄了原文 ≥ n 个连续字 → 背诵题,泄漏。"""
    q = question or ""
    for i in range(0, max(0, len(q) - n + 1)):
        if q[i:i + n] in chunk_text:
            return True
    return False


def map_support_to_span(doc_text: str, support: str) -> tuple[int, int] | None:
    if not support:
        return None
    idx = doc_text.find(support)
    if idx == -1:
        return None
    return (idx, idx + len(support))


def synth_item(gen, *, item_id: str, ingest_record_id: int, doc_text: str, chunk_text: str,
               slices: dict, corpus_version: str) -> GoldItem | None:
    user = (
        "基于下面这段资料,出一道**只能由它回答**的自然问题,并给参考答案,"
        "再原样抄出资料中**支撑答案的那一句**(必须是资料里的原文子串)。\n"
        "只输出 JSON:{\"question\":\"...\",\"reference_answer\":\"...\",\"support_sentence\":\"...\"}。\n\n"
        f"【资料】\n{chunk_text}"
    )
    out = gen.judge_json(_SYS, user)
    if not out:
        return None
    q = out.get("question", "")
    ref = out.get("reference_answer", "")
    support = out.get("support_sentence", "")
    if not (q and ref and support):
        return None
    if not is_self_contained(q) or is_leaky(q, chunk_text):
        return None
    span = map_support_to_span(doc_text, support)
    if span is None:
        return None
    return GoldItem(
        id=item_id, question=q, gold_spans=(GoldSpan(ingest_record_id, span[0], span[1]),),
        reference_answer=ref, slices=dict(slices), provenance="synthetic",
        source="own", corpus_version=corpus_version,
    )

"""生成指标(LLM judge)。judge.judge_json(system,user)->dict|None;None/无声明 → NaN(不记 0)。"""
from __future__ import annotations

import math

_FAITH_SYS = "你是严格的 RAG 评测裁判。只输出 JSON,不要多余文字、不要解释。"


def score_faithfulness(judge, *, answer: str, context: str) -> float:
    """声明分解法:把答案拆成原子声明,逐条判是否被检索上下文蕴含。score = 被支撑/总数。"""
    user = (
        "把【答案】拆成原子声明,逐条判断它是否能由【上下文】支撑(蕴含)。\n"
        "只输出 JSON:{\"claims\":[{\"text\":\"...\",\"supported\":true/false}]}。\n\n"
        f"【上下文】\n{context}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out:
        return math.nan
    claims = out.get("claims") or []
    if not claims:
        return math.nan
    supported = sum(1 for c in claims if c.get("supported") is True)
    return supported / len(claims)


def score_answer_relevancy(judge, *, question: str, answer: str) -> float:
    """答非所问度:答案在多大程度上直接回答了问题(0..1)。"""
    user = (
        "判断【答案】在多大程度上直接回答了【问题】(0 到 1 的小数,1=完全切题)。\n"
        "只输出 JSON:{\"relevancy\": 0.0~1.0}。\n\n"
        f"【问题】\n{question}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out or "relevancy" not in out:
        return math.nan
    try:
        return max(0.0, min(1.0, float(out["relevancy"])))
    except (TypeError, ValueError):
        return math.nan


def _mean_bool(xs) -> float:
    return sum(1 for x in xs if x is True) / len(xs) if xs else math.nan


def score_answer_correctness(judge, *, question: str, answer: str, reference: str) -> float:
    """声明级 F1:答案声明被参考支撑(P)× 参考声明被答案覆盖(R)。"""
    user = (
        "对照【参考答案】评估【答案】。给出两个布尔数组:\n"
        "answer_claims_supported(答案每条原子声明是否被参考支撑)、"
        "reference_claims_covered(参考每条原子声明是否被答案覆盖)。\n"
        "只输出 JSON:{\"answer_claims_supported\":[true/false...],"
        "\"reference_claims_covered\":[true/false...]}。\n\n"
        f"【问题】\n{question}\n\n【参考答案】\n{reference}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out:
        return math.nan
    p = _mean_bool(out.get("answer_claims_supported") or [])
    r = _mean_bool(out.get("reference_claims_covered") or [])
    if math.isnan(p) or math.isnan(r) or (p + r) == 0:
        return 0.0 if not (math.isnan(p) or math.isnan(r)) else math.nan
    return 2 * p * r / (p + r)


def score_refusal_correctness(judge, *, question: str, answer: str) -> float:
    """否定/不可答题:答案是否为恰当的「拒答/说没有」。仅对 negation 题调用。"""
    user = (
        "判断【答案】是否在恰当地表示『资料中没有/无法回答』(拒答)。\n"
        "只输出 JSON:{\"is_refusal\": true/false}。\n\n"
        f"【问题】\n{question}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out or "is_refusal" not in out:
        return math.nan
    return 1.0 if out["is_refusal"] is True else 0.0

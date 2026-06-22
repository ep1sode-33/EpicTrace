"""引用指标。validity/accuracy 确定性(faithfulness 需 judge,见 metrics_generation)。"""
from __future__ import annotations

import math
import re

from scripts.rag_eval.metrics import chunk_hits

_CITE = re.compile(r"\[(\d+)\]")


def parse_citation_ids(answer: str) -> list[int]:
    seen, out = set(), []
    for m in _CITE.findall(answer or ""):
        n = int(m)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def citation_validity(answer: str, n_pool: int) -> float:
    ids = parse_citation_ids(answer)
    if not ids:
        return math.nan
    valid = sum(1 for n in ids if 1 <= n <= n_pool)
    return valid / len(ids)


def citation_accuracy(answer: str, pool, gold_spans) -> float:
    valid = [n for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]
    if not valid:
        return math.nan
    hits = sum(1 for n in valid if chunk_hits(pool[n - 1], gold_spans))
    return hits / len(valid)


def citation_recall(answer: str, pool, gold_spans) -> float:
    """端到端可追溯性:答案**实际引用的块**覆盖了多少 gold 跨度(引没引到 gold 源)。
    与 accuracy 互补——单 gold 跨度下,多引相关旁证会拉低 accuracy 但不影响 recall。
    无 gold 跨度 → nan;没引/没覆盖 → 0。"""
    if not gold_spans:
        return math.nan
    valid = [pool[n - 1] for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]
    covered = sum(1 for g in gold_spans if any(chunk_hits(c, (g,)) for c in valid))
    return covered / len(gold_spans)


_CF_SYS = "你是严格的引用核验裁判。只输出 JSON,不要多余文字。"


def score_citation_faithfulness(judge, *, answer: str, cited_texts: list[str]) -> float:
    """逐条:被引片段是否真支撑答案里引用它的那句。score = 被支撑/总数。"""
    if not cited_texts:
        return math.nan
    blocks = "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(cited_texts))
    user = (
        "下列每个被引片段,是否真的支撑【答案】中引用它的论述?逐条给布尔。\n"
        "只输出 JSON:{\"citations\":[{\"supported\":true/false}, ...]}(顺序对应片段)。\n\n"
        f"【答案】\n{answer}\n\n【被引片段】\n{blocks}"
    )
    out = judge.judge_json(_CF_SYS, user)
    if not out:
        return math.nan
    cits = out.get("citations") or []
    if not cits:
        return math.nan
    return sum(1 for c in cits if c.get("supported") is True) / len(cits)

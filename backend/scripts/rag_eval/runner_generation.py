"""外循环 runner(测量点 ②③):复用真 agent 原语跑被测生成器,judge 算生成/引用质量。
组件全注入 → 烟测用 FakeChatModel + 假 judge,不碰真模型/网络。

注:ChunkAccumulator 实际定义在 epictrace.agent.tools(react.py 也从那里取),
故此处从 tools 导入;它仍是本模块的裸模块级名,烟测照常 monkeypatch rg.ChunkAccumulator。"""
from __future__ import annotations

import math
import sys
import time

from epictrace.agent.answer import stream_final_answer
from epictrace.agent.react import FALLBACK, run_react_loop
from epictrace.agent.tools import ChunkAccumulator, build_tools

from scripts.rag_eval.aggregate import aggregate
from scripts.rag_eval.judge_cache import cache_key
from scripts.rag_eval.metrics import (
    context_precision_ordered_at_k, mrr, ndcg_at_k, recall_any_at_k, recall_coverage_at_k,
)
from scripts.rag_eval.metrics_citation import (
    citation_accuracy, citation_recall, citation_validity, parse_citation_ids,
    score_citation_faithfulness,
)
from scripts.rag_eval.metrics_generation import (
    score_answer_correctness, score_answer_relevancy, score_faithfulness, score_refusal_correctness,
)


def _cached(cache, judge_model, metric, qid, answer, context, fn):
    """judge 评分缓存包装:命中读盘,未命中算了再写(只缓存非 nan)。cache=None → 不缓存。"""
    if cache is None:
        return fn()
    k = cache_key(metric, qid, answer, context, judge_model)
    hit = cache.get(k)
    if hit is not None:
        return hit.get("v", math.nan)
    val = fn()
    if not (isinstance(val, float) and math.isnan(val)):
        cache.put(k, {"v": val})
    return val


def _run_one(it, *, build_chat_model, llm, retriever, judge, cache, judge_model, project_id, config):
    acc = ChunkAccumulator()
    tools = build_tools(retriever=retriever, project_id=project_id, focus_ids=[],
                        attachment_retriever=None, conversation_id=0, indexed_ext_ids=[],
                        reference_texts={}, fulltext_ids=[])
    status = run_react_loop(build_chat_model(), tools, acc, it.question, history=[], attachment_manifest="")
    pool = list(acc.chunks)
    answer = ""
    for ev in stream_final_answer(llm, it.question, pool, history=[], attached_names=[]):
        if ev.get("event") == "_answer":
            answer = ev["data"]
    context = "\n\n".join(getattr(c, "text", "") for c in pool)
    cited_texts = [pool[n - 1].text for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]

    m: dict = {"agent_fallback": 1.0 if status == FALLBACK else 0.0}
    for k in config.k_values:
        m[f"agent_recall_any@{k}"] = recall_any_at_k(pool, it.gold_spans, k)
        m[f"agent_recall_cov@{k}"] = recall_coverage_at_k(pool, it.gold_spans, k)
        m[f"agent_ndcg@{k}"] = ndcg_at_k(pool, it.gold_spans, k)
        m[f"agent_ctxp_ord@{k}"] = context_precision_ordered_at_k(pool, it.gold_spans, k)
    m["agent_mrr"] = mrr(pool, it.gold_spans)
    m["citation_validity"] = citation_validity(answer, len(pool))
    m["citation_accuracy"] = citation_accuracy(answer, pool, it.gold_spans)
    m["citation_recall"] = citation_recall(answer, pool, it.gold_spans)
    m["citation_faithfulness"] = _cached(
        cache, judge_model, "citation_faithfulness", it.id, answer, context,
        lambda: score_citation_faithfulness(judge, answer=answer, cited_texts=cited_texts))
    m["faithfulness"] = _cached(cache, judge_model, "faithfulness", it.id, answer, context,
                                lambda: score_faithfulness(judge, answer=answer, context=context))
    m["answer_relevancy"] = _cached(cache, judge_model, "answer_relevancy", it.id, answer, "",
                                    lambda: score_answer_relevancy(judge, question=it.question, answer=answer))
    m["answer_correctness"] = _cached(
        cache, judge_model, "answer_correctness", it.id, answer, it.reference_answer,
        lambda: score_answer_correctness(judge, question=it.question, answer=answer, reference=it.reference_answer))
    if it.slices.get("q_type") == "negation":
        m["refusal_correctness"] = _cached(cache, judge_model, "refusal_correctness", it.id, answer, "",
                                           lambda: score_refusal_correctness(judge, question=it.question, answer=answer))
    return {"id": it.id, "slices": it.slices, "metrics": m, "answer": answer}


def run_generation(golden, *, build_chat_model, llm, retriever, judge, cache,
                   project_id: int, config) -> dict:
    judge_model = getattr(getattr(judge, "_cfg", None), "model", "judge")
    total = len(golden)

    def _f(x):  # nan 安全的紧凑分数格式
        return "  nan" if (isinstance(x, float) and math.isnan(x)) else f"{x:5.2f}"

    per_q = []
    t0 = time.perf_counter()
    for i, it in enumerate(golden, 1):
        ts = time.perf_counter()
        rec = _run_one(it, build_chat_model=build_chat_model, llm=llm, retriever=retriever,
                       judge=judge, cache=cache, judge_model=judge_model,
                       project_id=project_id, config=config)
        per_q.append(rec)
        m, dt = rec["metrics"], time.perf_counter() - ts
        eta = (time.perf_counter() - t0) / i * (total - i)
        # 逐题进度 → stderr:序号、id、关键分数、单题耗时、实时 ETA(治"盲跑")
        print(f"[{i:>3}/{total}] {it.id:<10} "
              f"faith={_f(m.get('faithfulness'))} corr={_f(m.get('answer_correctness'))} "
              f"relev={_f(m.get('answer_relevancy'))} citeF={_f(m.get('citation_faithfulness'))} "
              f"rec@5={_f(m.get('agent_recall_any@5', math.nan))} fb={_f(m.get('agent_fallback'))}"
              f" | {dt:4.0f}s  ETA {eta / 60:5.1f}m", file=sys.stderr, flush=True)
    agg = aggregate(per_q)
    return {"config_hash": config.config_hash(), "n": len(per_q), "per_question": per_q,
            "by_slice": agg["by_slice"], "overall": agg["overall"]}

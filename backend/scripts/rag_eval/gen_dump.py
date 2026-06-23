"""生成段(无判官):跑真 agent(ReAct+检索+终答)+ 算确定性指标,把每题的判官输入
dump 成 JSON。判官改用 Claude Code 子代理(off-family、走 harness 通道,不碰 krill 代理)。
手动跑:./.venv/bin/python -m scripts.rag_eval.gen_dump [golden] [project_id] [out]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def gen_dump(golden_path: str = "eval-data/golden.jsonl", project_id: int = 2,
             out_path: str = "scripts/rag_eval/runs/gen_dump_v65.json") -> str:
    from epictrace.agent.answer import stream_final_answer
    from epictrace.agent.react import FALLBACK, run_react_loop
    from epictrace.agent.tools import ChunkAccumulator, build_tools

    from scripts.rag_eval.config import EvalConfig
    from scripts.rag_eval.golden import load_golden
    from scripts.rag_eval.metrics import (
        context_precision_ordered_at_k, mrr, ndcg_at_k, recall_any_at_k, recall_coverage_at_k,
    )
    from scripts.rag_eval.metrics_citation import (
        citation_accuracy, citation_recall, citation_validity, parse_citation_ids,
    )
    from scripts.rag_eval.wiring import build_chat_model_factory, build_llm, build_retriever

    golden = load_golden(golden_path)
    retriever = build_retriever(project_id)   # 内部已先 warmup BGE 再建 Milvus(macOS fork)
    build_chat_model = build_chat_model_factory()
    llm = build_llm()
    cfg = EvalConfig()
    total = len(golden)
    out = []
    t0 = time.perf_counter()
    for i, it in enumerate(golden, 1):
        ts = time.perf_counter()
        acc = ChunkAccumulator()
        tools = build_tools(retriever=retriever, project_id=project_id, focus_ids=[],
                            attachment_retriever=None, conversation_id=0, indexed_ext_ids=[],
                            reference_texts={}, fulltext_ids=[])
        status = run_react_loop(build_chat_model(), tools, acc, it.question,
                                history=[], attachment_manifest="")
        pool = list(acc.chunks)
        answer = ""
        for ev in stream_final_answer(llm, it.question, pool, history=[], attached_names=[]):
            if ev.get("event") == "_answer":
                answer = ev["data"]
        context = "\n\n".join(getattr(c, "text", "") for c in pool)
        cited_texts = [pool[n - 1].text for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]

        m: dict = {"agent_fallback": 1.0 if status == FALLBACK else 0.0}
        for k in cfg.k_values:
            m[f"agent_recall_any@{k}"] = recall_any_at_k(pool, it.gold_spans, k)
            m[f"agent_recall_cov@{k}"] = recall_coverage_at_k(pool, it.gold_spans, k)
            m[f"agent_ndcg@{k}"] = ndcg_at_k(pool, it.gold_spans, k)
            m[f"agent_ctxp_ord@{k}"] = context_precision_ordered_at_k(pool, it.gold_spans, k)
        m["agent_mrr"] = mrr(pool, it.gold_spans)
        m["citation_validity"] = citation_validity(answer, len(pool))
        m["citation_accuracy"] = citation_accuracy(answer, pool, it.gold_spans)
        m["citation_recall"] = citation_recall(answer, pool, it.gold_spans)

        out.append({"id": it.id, "slices": it.slices, "question": it.question,
                    "reference_answer": it.reference_answer, "answer": answer,
                    "context": context, "cited_texts": cited_texts, "det_metrics": m})
        dt = time.perf_counter() - ts
        eta = (time.perf_counter() - t0) / i * (total - i)
        print(f"[{i:>3}/{total}] {it.id:<10} ans={len(answer):>4}c pool={len(pool):>2} "
              f"rec@5={m.get('agent_recall_any@5', float('nan')):.2f} cited={len(cited_texts)}"
              f" | {dt:4.0f}s  ETA {eta / 60:5.1f}m", file=sys.stderr, flush=True)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[gen_dump] {len(out)} 题 → {out_path}", file=sys.stderr)
    return out_path


if __name__ == "__main__":
    a = sys.argv
    gen_dump(a[1] if len(a) > 1 else "eval-data/golden.jsonl",
             int(a[2]) if len(a) > 2 else 2,
             a[3] if len(a) > 3 else "scripts/rag_eval/runs/gen_dump_v65.json")

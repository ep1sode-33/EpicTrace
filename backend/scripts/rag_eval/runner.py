"""检索器单测 runner(测量点 ①):每题用 raw 问题查 HybridRetriever,算确定性检索指标。免 LLM。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.metrics import (
    context_precision_at_k, context_precision_ordered_at_k, mrr,
    ndcg_at_k, recall_any_at_k, recall_coverage_at_k,
)


def _per_question_metrics(ranked, gold_spans, k_values) -> dict:
    m: dict = {"mrr": mrr(ranked, gold_spans)}
    for k in k_values:
        m[f"recall_any@{k}"] = recall_any_at_k(ranked, gold_spans, k)
        m[f"recall_cov@{k}"] = recall_coverage_at_k(ranked, gold_spans, k)
        m[f"ndcg@{k}"] = ndcg_at_k(ranked, gold_spans, k)
        m[f"ctxp@{k}"] = context_precision_at_k(ranked, gold_spans, k)
        m[f"ctxp_ord@{k}"] = context_precision_ordered_at_k(ranked, gold_spans, k)
    return m


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _aggregate(per_q: list[dict]) -> dict:
    keys = per_q[0]["metrics"].keys() if per_q else []
    return {k: _mean([r["metrics"][k] for r in per_q]) for k in keys}


def run_retrieve(golden, retriever, *, project_id: int, config: EvalConfig) -> dict:
    per_q: list[dict] = []
    for it in golden:
        ranked = retriever.retrieve(project_id=project_id, query=it.question,
                                    k=config.k, dense_n=config.dense_n, fuse_m=config.fuse_m)
        per_q.append({"id": it.id, "slices": it.slices,
                      "metrics": _per_question_metrics(ranked, it.gold_spans, config.k_values)})

    by_slice: dict = {}
    for dim in ("domain", "doc_type", "lang", "q_type"):
        for rec in per_q:
            val = rec["slices"].get(dim)
            if val is None:
                continue
            by_slice.setdefault(f"{dim}={val}", []).append(rec)
    by_slice = {kk: _aggregate(v) for kk, v in by_slice.items()}

    return {"config_hash": config.config_hash(), "n": len(per_q),
            "per_question": per_q, "by_slice": by_slice, "overall": _aggregate(per_q)}


def write_run(result: dict, runs_dir: str | Path) -> Path:
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    base = result["config_hash"]
    seq = len(list(runs_dir.glob(f"{base}-*")))
    out = runs_dir / f"{base}-{seq}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(
        {k: result[k] for k in ("config_hash", "n", "by_slice", "overall")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "per_question.jsonl").open("w", encoding="utf-8") as f:
        for rec in result["per_question"]:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (out / "config.json").write_text(json.dumps({"config_hash": base}, ensure_ascii=False), encoding="utf-8")
    return out

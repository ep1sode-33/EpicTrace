"""合并子代理判官结果:gen_dump 确定性指标 + judge_workflow 判官分 → per_question → 落 run dir
(带 provenance)→ 打印 report-ci(带 CI 的分片报表)。判官走 Claude Code 子代理,不碰 krill。
用法: ./.venv/bin/python -m scripts.rag_eval.merge_judged <gen_dump.json> <judge_scores.json>
judge_scores.json = judge_workflow 返回的数组 [{id, slices, judge:{...}}](判官分,null=nan)。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_REPORT_METRICS = [
    "faithfulness", "answer_correctness", "answer_relevancy", "refusal_correctness",
    "citation_faithfulness", "citation_recall", "citation_accuracy", "citation_validity",
    "agent_recall_any@5", "agent_ndcg@5", "agent_fallback",
]


def merge(gen_path: str, judge_path: str, golden_path: str = "eval-data/golden.jsonl",
          label: str = "v65-subagent-judge") -> Path:
    from scripts.rag_eval.aggregate import aggregate
    from scripts.rag_eval.cli import _active_model_name, _build_meta
    from scripts.rag_eval.config import EvalConfig
    from scripts.rag_eval.report import format_report_ci
    from scripts.rag_eval.runner import write_run

    gen = json.loads(Path(gen_path).read_text(encoding="utf-8"))
    judged = {r["id"]: (r.get("judge") or {}) for r in json.loads(Path(judge_path).read_text(encoding="utf-8"))}

    def nz(v):
        return math.nan if v is None else v

    per_q = []
    for g in gen:
        m = dict(g["det_metrics"])
        for k, v in judged.get(g["id"], {}).items():
            m[k] = nz(v)
        per_q.append({"id": g["id"], "slices": g["slices"], "metrics": m, "answer": g.get("answer", "")})

    cfg = EvalConfig(label=label)
    agg = aggregate(per_q)
    res = {"config_hash": cfg.config_hash(), "n": len(per_q),
           "per_question": per_q, "by_slice": agg["by_slice"], "overall": agg["overall"]}
    meta = _build_meta(cfg, golden_path, judge_model="claude-opus-4-8 (CC subagent)",
                       gen_model=_active_model_name())
    out = write_run(res, Path(__file__).parent / "runs", meta=meta)

    judged_n = sum(1 for r in per_q if not (isinstance(r["metrics"].get("faithfulness"), float)
                                            and math.isnan(r["metrics"].get("faithfulness", math.nan))))
    print(f"[merge] {len(per_q)} 题; faithfulness 有效 {judged_n} 题; run → {out}", file=sys.stderr)
    print(format_report_ci(per_q, metrics=_REPORT_METRICS))
    return out


if __name__ == "__main__":
    merge(sys.argv[1], sys.argv[2])

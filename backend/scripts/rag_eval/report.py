"""分片报告 + run-vs-run delta(markdown)。含统计严谨版:bootstrap CI / 配对显著性 / N-run mean±std。"""
from __future__ import annotations

import math

from scripts.rag_eval.aggregate import mean_skipnan
from scripts.rag_eval.stats import bootstrap_ci, mean_std, paired_significance

_CORE = ["recall_any@5", "recall_cov@5", "mrr", "ndcg@5", "ctxp_ord@5"]

_DIMS = ("domain", "doc_type", "lang", "q_type")

# 生成类 run 的核心指标(供生成评测报告方便取用)。
GEN_CORE = ["faithfulness", "answer_relevancy", "answer_correctness",
            "citation_accuracy", "citation_recall", "citation_faithfulness", "agent_recall_any@5"]


def _rows(summary: dict) -> dict[str, dict]:
    rows = {"overall": summary["overall"]}
    rows.update(summary.get("by_slice", {}))
    return rows


def format_report(summary: dict, metrics: list[str] | None = None) -> str:
    metrics = metrics or _CORE
    rows = _rows(summary)
    head = "| slice | " + " | ".join(metrics) + " |"
    sep = "|" + "---|" * (len(metrics) + 1)
    lines = [f"# run {summary.get('config_hash','?')} (n={summary.get('n','?')})", "", head, sep]
    for name, mvals in rows.items():
        cells = [f"{mvals.get(m, float('nan')):.2f}" for m in metrics]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _mark(delta: float) -> str:
    return "▲" if delta > 1e-9 else ("▼" if delta < -1e-9 else "=")


def diff_runs(summary_a: dict, summary_b: dict, metrics: list[str] | None = None) -> str:
    metrics = metrics or _CORE
    ra, rb = _rows(summary_a), _rows(summary_b)
    head = "| slice | " + " | ".join(metrics) + " |"
    sep = "|" + "---|" * (len(metrics) + 1)
    lines = [f"# diff {summary_a.get('config_hash','A')} → {summary_b.get('config_hash','B')}",
             "", head, sep]
    for name in ra:
        if name not in rb:
            continue
        cells = []
        for m in metrics:
            va, vb = ra[name].get(m), rb[name].get(m)
            if va is None or vb is None:
                cells.append("nan")
                continue
            d = vb - va
            cells.append(f"{d:+.2f}{_mark(d)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---- 统计严谨版(逐项 per_question)----

def _group_rows(per_q, dims=_DIMS) -> dict:
    """{slice_name: [per_q 行]},含 overall。"""
    groups = {"overall": list(per_q)}
    for dim in dims:
        for r in per_q:
            v = r["slices"].get(dim)
            if v is not None:
                groups.setdefault(f"{dim}={v}", []).append(r)
    return groups


def format_report_ci(per_q, metrics: list[str] | None = None, *, dims=_DIMS) -> str:
    """单 run 逐项:每指标 mean [95% bootstrap CI](逐项重采样;nan 题被跳过)。"""
    metrics = metrics or GEN_CORE
    head = "| slice (n) | " + " | ".join(metrics) + " |"
    lines = ["# report (mean [95% CI])", "", head, "|" + "---|" * (len(metrics) + 1)]
    for name, rows in _group_rows(per_q, dims).items():
        cells = []
        for m in metrics:
            vals = [r["metrics"].get(m, math.nan) for r in rows]
            mean = mean_skipnan(vals)
            if math.isnan(mean):
                cells.append("nan")
                continue
            lo, hi = bootstrap_ci(vals)
            cells.append(f"{mean:.2f} [{lo:.2f},{hi:.2f}]" if not math.isnan(lo) else f"{mean:.2f}")
        lines.append(f"| {name} ({len(rows)}) | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def diff_runs_paired(per_q_a, per_q_b, metrics: list[str] | None = None, *, dims=_DIMS) -> str:
    """配对显著性 diff:按 id 配对,逐指标 Δ + 检验(二值 McNemar / 连续置换);
    ▲▼ 只在 p<0.05 打,否则 ~(不显著=可能是噪声,不可当回归信号)。"""
    metrics = metrics or GEN_CORE
    a_by = {r["id"]: r for r in per_q_a}
    b_by = {r["id"]: r for r in per_q_b}
    common = [r for r in per_q_a if r["id"] in b_by]
    head = "| slice (n) | " + " | ".join(metrics) + " |"
    lines = ["# diff (Δ + 显著性;~ = 不显著/可能噪声)", "", head, "|" + "---|" * (len(metrics) + 1)]
    for name, rows in _group_rows(common, dims).items():
        cells = []
        for m in metrics:
            av = [a_by[r["id"]]["metrics"].get(m, math.nan) for r in rows]
            bv = [b_by[r["id"]]["metrics"].get(m, math.nan) for r in rows]
            da, db = mean_skipnan(av), mean_skipnan(bv)
            if math.isnan(da) or math.isnan(db):
                cells.append("nan")
                continue
            d = db - da
            p = paired_significance(av, bv)
            mark = ("▲" if d > 0 else "▼") if p < 0.05 else "~"
            cells.append(f"{d:+.2f}{mark}")
        lines.append(f"| {name} ({len(rows)}) | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def aggregate_multirun(summaries, metrics: list[str] | None = None) -> dict:
    """N 个 run 的 summary → overall 每指标跨 run 的 (mean, std)。"""
    metrics = metrics or GEN_CORE
    return {m: mean_std([s["overall"].get(m, math.nan) for s in summaries]) for m in metrics}


def format_multirun(summaries, metrics: list[str] | None = None) -> str:
    """N-run overall mean±std(治 LLM 随机性:单点估计 → 区间)。"""
    metrics = metrics or GEN_CORE
    agg = aggregate_multirun(summaries, metrics)
    lines = [f"# {len(summaries)}-run mean ± std (overall)", ""]
    for m in metrics:
        mean, std = agg[m]
        sd = "nan" if math.isnan(std) else f"{std:.3f}"
        lines.append(f"- {m}: {mean:.3f} ± {sd}")
    return "\n".join(lines)

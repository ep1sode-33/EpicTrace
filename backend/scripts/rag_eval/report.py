"""分片报告 + run-vs-run delta(markdown)。"""
from __future__ import annotations

_CORE = ["recall_any@5", "recall_cov@5", "mrr", "ndcg@5", "ctxp_ord@5"]


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
            d = rb[name].get(m, 0.0) - ra[name].get(m, 0.0)
            cells.append(f"{d:+.2f}{_mark(d)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)

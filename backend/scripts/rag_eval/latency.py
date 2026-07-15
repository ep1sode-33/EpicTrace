"""agent 链路时延分解 profiler(eval-only,零 judge、零 opus 成本)。

只在 eval 侧「包一层」给产品链路打点,**绝不改 backend/epictrace/**:
  · run_react_loop 的 on_step / on_think 回调里塞 time.perf_counter() 记录器;
  · stream_final_answer 用到的 llm 用 TimingLLM 代理——.complete()=接地闸门、
    .stream()/.stream_events()=终答流,借相邻方法边界近似每次 generator LLM 调用的时刻。

事件 (t, kind, meta):run_start / seed_done / think / tool_step / react_done /
gate_start / gate_done / answer_first_token / answer_done。由这些边界切出各阶段耗时。

纯逻辑(记录器/汇总/聚合/表格/TimingLLM)在模块顶层,无重依赖;真跑用到的 agent 原语
(run_react_loop / stream_final_answer / build_tools / ChunkAccumulator)在 run_one_timed
里**懒导入**,好让记录器/聚合能被单测轻量导入。"""
from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# 阶段顺序(非重叠,拼起来 ≈ total);答复分成「首 token 前(TTFT)」与「流式产出」两段。
STAGES = ("seed", "agent", "gate", "answer_ttft", "answer_stream", "total")
_STAGE_LABEL = {
    "seed": "强制首检索 seed",
    "agent": "ReAct 循环 agent",
    "gate": "接地闸门 gate",
    "answer_ttft": "终答首 token answer_ttft",
    "answer_stream": "终答流式 answer_stream",
    "total": "整题 total",
}


# ---------------------------------------------------------------- 事件记录器

@dataclass
class LatencyRecorder:
    """单题事件记录器:记 (t, kind, meta)。clock 可注入(测试用假时钟)。

    连续的 think 事件合并成一条(记 chunks/chars 累计,t 取首块、t_last 取末块)——
    reasoning 流每块都回调 on_think,不合并会刷出成百上千条 think 噪声。"""
    clock: object = field(default=time.perf_counter)
    events: list[dict] = field(default_factory=list)

    def mark(self, kind: str, meta: dict | None = None) -> None:
        t = float(self.clock())
        meta = dict(meta or {})
        if kind == "think" and self.events and self.events[-1]["kind"] == "think":
            last = self.events[-1]
            last["t_last"] = t
            last["meta"]["chunks"] = last["meta"].get("chunks", 1) + 1
            last["meta"]["chars"] = last["meta"].get("chars", 0) + int(meta.get("chars", 0))
            return
        if kind == "think":
            meta.setdefault("chunks", 1)
            meta["chars"] = int(meta.get("chars", 0))
        self.events.append({"t": t, "t_last": t, "kind": kind, "meta": meta})

    def _first(self, kind: str) -> float | None:
        for e in self.events:
            if e["kind"] == kind:
                return e["t"]
        return None

    def _last(self, kind: str) -> float | None:
        for e in reversed(self.events):
            if e["kind"] == kind:
                return e["t"]
        return None


def _dur(a: float | None, b: float | None) -> float | None:
    """b−a;任一端缺失 → None(该阶段不可测,别当 0 拉低分布)。"""
    if a is None or b is None:
        return None
    return round(b - a, 4)


def summarize_events(events: list[dict]) -> dict:
    """把一题的事件序列切成各阶段耗时 + 计数。缺哪个边界哪个阶段记 None,鲁棒不崩。"""
    rec = LatencyRecorder(events=events)
    t_run = rec._first("run_start")
    t_seed = rec._first("seed_done")
    t_react = rec._first("react_done")
    t_gate_s = rec._first("gate_start")
    t_gate_e = rec._first("gate_done")
    t_first = rec._first("answer_first_token")
    t_done = rec._last("answer_done")

    agent_start = t_seed if t_seed is not None else t_run          # seed 缺 → 从 run 起
    ttft_base = t_gate_e if t_gate_e is not None else t_react      # 无闸门 → 从 react 结束起
    stages = {
        "seed": _dur(t_run, t_seed),
        "agent": _dur(agent_start, t_react),
        "gate": _dur(t_gate_s, t_gate_e),
        "answer_ttft": _dur(ttft_base, t_first),
        "answer_stream": _dur(t_first, t_done),
        "total": _dur(t_run, t_done),
    }
    counts = {
        "tool_steps": sum(1 for e in events if e["kind"] == "tool_step"),
        "think_bursts": sum(1 for e in events if e["kind"] == "think"),
        "gate_ran": t_gate_s is not None,
    }
    react = next((e for e in events if e["kind"] == "react_done"), None)
    if react is not None:
        counts["react_status"] = react["meta"].get("status")
        counts["pool"] = react["meta"].get("pool")
    return {"stages": stages, "counts": counts}


# ---------------------------------------------------------------- 计时 LLM 代理

class TimingLLM:
    """透明代理产品 LLMProvider,给终答链路的三个入口打点,不改产品代码:
      · complete()      → 接地闸门(_is_answerable):gate_start/gate_done;
      · stream_events() → 终答主路(有推理分离):首 item=answer_first_token,耗尽=answer_done;
      · stream()        → 拒答/退化路:同上。
    其余属性透传底层 llm(stream_final_answer 只用到这三个)。"""

    def __init__(self, llm, recorder: LatencyRecorder) -> None:
        self._llm = llm
        self._rec = recorder

    def complete(self, messages, **kwargs) -> str:
        self._rec.mark("gate_start", {})
        try:
            return self._llm.complete(messages, **kwargs)
        finally:
            self._rec.mark("gate_done", {})

    def _timed_stream(self, it: Iterator) -> Iterator:
        first = True
        for x in it:
            if first:
                self._rec.mark("answer_first_token", {})
                first = False
            yield x
        self._rec.mark("answer_done", {})

    def stream(self, messages, **kwargs) -> Iterator[str]:
        return self._timed_stream(self._llm.stream(messages, **kwargs))

    def stream_events(self, messages, **kwargs) -> Iterator[dict]:
        return self._timed_stream(self._llm.stream_events(messages, **kwargs))

    def __getattr__(self, name):  # 透传底层其余接口(仅在本类未定义时命中)
        return getattr(self._llm, name)


# ---------------------------------------------------------------- 单题真跑

def run_one_timed(it, *, build_chat_model, llm, retriever, project_id: int,
                  recorder: LatencyRecorder | None = None) -> dict:
    """跑一题完整生成链路并打点。**不评分、不调 judge**。返回逐题时延记录。

    重依赖懒导入,好让本模块的记录器/聚合能被单测轻量导入(不拉 langchain 等)。"""
    from epictrace.agent.answer import stream_final_answer
    from epictrace.agent.react import run_react_loop
    from epictrace.agent.tools import ChunkAccumulator, build_tools

    rec = recorder or LatencyRecorder()
    rec.mark("run_start", {"qid": it.id})

    acc = ChunkAccumulator()
    tools = build_tools(retriever=retriever, project_id=project_id, focus_ids=[],
                        attachment_retriever=None, conversation_id=0, indexed_ext_ids=[],
                        reference_texts={}, fulltext_ids=[])

    seen_step = {"seed": False}

    def on_step(payload: dict) -> None:
        # run_react_loop 里 force_seed 在建图前**同步**先跑,其 on_step 必是第一条 → 记 seed_done;
        # 其后的都是真实工具轮 → tool_step。
        kind = "tool_step" if seen_step["seed"] else "seed_done"
        seen_step["seed"] = True
        rec.mark(kind, {"tool": payload.get("tool"), "count": payload.get("count")})

    def on_think(text: str) -> None:
        rec.mark("think", {"chars": len(text or "")})

    status = run_react_loop(build_chat_model(), tools, acc, it.question, history=[],
                            attachment_manifest="", on_step=on_step, on_think=on_think)
    pool = list(acc.chunks)
    rec.mark("react_done", {"status": status, "pool": len(pool)})

    timed = TimingLLM(llm, rec)
    answer = ""
    for ev in stream_final_answer(timed, it.question, pool, history=[], attached_names=[]):
        if ev.get("event") == "_answer":
            answer = ev["data"]
    # 极端:stream_final_answer 未产出任何 answer 流(不应发生)→ 补一个 answer_done 兜底,total 可测。
    if rec._first("answer_first_token") is None and rec._last("answer_done") is None:
        rec.mark("answer_done", {"empty": True})

    summ = summarize_events(rec.events)
    t0 = rec.events[0]["t"] if rec.events else 0.0
    return {
        "id": it.id,
        "slices": it.slices,
        "stages": summ["stages"],
        "counts": summ["counts"],
        "answer_len": len(answer),
        # 事件时间轴转相对秒,便于人读 / 归档复盘(不泄露答案内容)。
        "events": [{"t": round(e["t"] - t0, 4), "kind": e["kind"], "meta": e["meta"]}
                   for e in rec.events],
    }


def run_latency_profile(golden, *, build_chat_model, llm, retriever, project_id: int,
                        sample: int | None = None, progress: bool = True) -> dict:
    """跑批:抽前 sample 题(None=全部),逐题打点,聚合出分阶段 p50/p95/max。"""
    items = list(golden)[: sample] if sample else list(golden)
    total = len(items)
    per_q: list[dict] = []
    t0 = time.perf_counter()
    for i, it in enumerate(items, 1):
        rec = run_one_timed(it, build_chat_model=build_chat_model, llm=llm,
                            retriever=retriever, project_id=project_id)
        per_q.append(rec)
        if progress:
            s = rec["stages"]
            eta = (time.perf_counter() - t0) / i * (total - i)
            print(
                f"[{i:>3}/{total}] {it.id:<10} "
                f"seed={_fmt(s['seed'])} agent={_fmt(s['agent'])} gate={_fmt(s['gate'])} "
                f"ttft={_fmt(s['answer_ttft'])} ans={_fmt(s['answer_stream'])} "
                f"total={_fmt(s['total'])}  steps={rec['counts'].get('tool_steps')}"
                f"  ETA {eta / 60:4.1f}m",
                file=sys.stderr, flush=True)
    return {"n": len(per_q), "per_question": per_q, "aggregate": aggregate_latency(per_q)}


# ---------------------------------------------------------------- 聚合 / 表格

def _percentile(values: list[float], p: float) -> float | None:
    """线性插值分位数(numpy 默认法)。空 → None。p ∈ [0,100]。"""
    vals = sorted(float(v) for v in values if v is not None
                  and not (isinstance(v, float) and math.isnan(v)))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    rank = (p / 100.0) * (len(vals) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return vals[lo]
    frac = rank - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def _mean(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None
            and not (isinstance(v, float) and math.isnan(v))]
    return sum(vals) / len(vals) if vals else None


def aggregate_latency(per_q: list[dict]) -> dict:
    """跑批聚合:各阶段 p50/p95/max/mean/n + agent 轮数分布。"""
    by_stage: dict = {}
    for st in STAGES:
        vals = [q["stages"].get(st) for q in per_q]
        vals = [v for v in vals if v is not None]
        by_stage[st] = {"p50": _percentile(vals, 50), "p95": _percentile(vals, 95),
                        "max": max(vals) if vals else None, "mean": _mean(vals),
                        "n": len(vals)}
    steps = [q["counts"].get("tool_steps", 0) for q in per_q]
    rounds = {"p50": _percentile(steps, 50), "p95": _percentile(steps, 95),
              "max": max(steps) if steps else None, "mean": _mean(steps)}
    fb = sum(1 for q in per_q if q["counts"].get("react_status") == "fallback")
    return {"n": len(per_q), "by_stage": by_stage, "tool_steps": rounds, "fallbacks": fb}


def _fmt(v: float | None) -> str:
    return "   -  " if v is None else f"{v:6.2f}"


def format_latency_table(agg: dict) -> str:
    """人话可读的阶段 × p50/p95 汇总表(含 max/mean/n),外加最大瓶颈判读一行。"""
    lines: list[str] = []
    lines.append(f"[latency] n={agg['n']} 题  (单位:秒;'-'=该阶段本题不适用)")
    lines.append(f"{'阶段':<26}{'p50':>8}{'p95':>8}{'max':>8}{'mean':>8}{'n':>5}")
    lines.append("-" * 63)
    for st in STAGES:
        s = agg["by_stage"][st]
        lines.append(f"{_STAGE_LABEL[st]:<24}{_fmt(s['p50']):>8}{_fmt(s['p95']):>8}"
                     f"{_fmt(s['max']):>8}{_fmt(s['mean']):>8}{s['n']:>5}")
    r = agg["tool_steps"]
    lines.append("-" * 63)
    lines.append(f"agent 工具轮数(不含 seed):p50={_fmt(r['p50']).strip()} "
                 f"p95={_fmt(r['p95']).strip()} max={r['max']} mean={_fmt(r['mean']).strip()}"
                 f"  | fallback {agg['fallbacks']}/{agg['n']}")
    # 最大瓶颈:除 total 外 p50 最大的阶段。
    ranked = [(st, agg["by_stage"][st]["p50"]) for st in STAGES if st != "total"
              and agg["by_stage"][st]["p50"] is not None]
    if ranked:
        st, v = max(ranked, key=lambda kv: kv[1])
        tot = agg["by_stage"]["total"]["p50"]
        share = f"({v / tot * 100:.0f}% of total p50)" if tot else ""
        lines.append(f">> 最大瓶颈(p50):{_STAGE_LABEL[st]} ≈ {v:.2f}s {share}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 落盘

def write_latency_run(result: dict, runs_dir: str | Path, *, label: str,
                      meta: dict | None = None) -> Path:
    """落 runs/latency-<label>/:逐题 JSONL + 汇总 JSON(+ 可选 run.json 溯源)。"""
    out = Path(runs_dir) / f"latency-{label or 'run'}"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "per_question.jsonl").open("w", encoding="utf-8") as f:
        for rec in result["per_question"]:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(
        json.dumps({"n": result["n"], "aggregate": result["aggregate"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    if meta is not None:
        (out / "run.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

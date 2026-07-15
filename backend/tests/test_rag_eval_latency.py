"""latency profiler(eval-only)单测:记录器 / 阶段汇总 / 聚合 / TimingLLM 打点 / 单题真跑
(全用假 LLM+假检索,不碰真模型)/ CLI 子命令 plumbing。"""
import json

from scripts.rag_eval import latency
from scripts.rag_eval.golden import GoldItem, GoldSpan, save_golden
from scripts.rag_eval.latency import (
    LatencyRecorder, TimingLLM, aggregate_latency, format_latency_table,
    run_one_timed, summarize_events, _percentile,
)


class _Clock:
    """脚本化假时钟:每次调用吐下一个预设时刻。"""
    def __init__(self, times):
        self.times = list(times)
        self.i = 0

    def __call__(self):
        t = self.times[self.i]
        self.i += 1
        return t


def _item(qid="g1", q="问题?", slices=None):
    return GoldItem(qid, q, (GoldSpan(1, 0, 50),), "ref", slices or {"lang": "zh"},
                    "synthetic", "own", "v1")


# ---------------------------------------------------------------- 记录器

def test_recorder_coalesces_think_bursts():
    # run_start, think×3(连续,合并), tool_step, think(独立)
    rec = LatencyRecorder(clock=_Clock([0, 1, 2, 3, 4, 5]))
    rec.mark("run_start")
    rec.mark("think", {"chars": 3})
    rec.mark("think", {"chars": 4})
    rec.mark("think", {"chars": 2})
    rec.mark("tool_step", {"tool": "search", "count": 3})
    rec.mark("think", {"chars": 7})

    kinds = [e["kind"] for e in rec.events]
    assert kinds == ["run_start", "think", "tool_step", "think"]      # 连续 think 合并成一条
    burst = rec.events[1]
    assert burst["meta"]["chunks"] == 3 and burst["meta"]["chars"] == 9
    assert burst["t"] == 1 and burst["t_last"] == 3                   # t=首块, t_last=末块
    assert rec.events[3]["meta"]["chunks"] == 1                       # tool_step 后重新起一条


# ---------------------------------------------------------------- 阶段汇总

def test_summarize_events_stage_durations():
    events = [
        {"t": 0.0, "kind": "run_start", "meta": {}},
        {"t": 0.9, "kind": "seed_callback_fired", "meta": {"tool": "search", "count": 3}},
        {"t": 1.0, "kind": "seed_done", "meta": {"fired": True, "callbacks": 1}},
        {"t": 1.2, "kind": "think", "meta": {"chunks": 5}},
        {"t": 2.5, "kind": "tool_step", "meta": {"tool": "search", "count": 2}},
        {"t": 3.0, "kind": "react_done", "meta": {"status": "ok", "pool": 5}},
        {"t": 3.1, "kind": "gate_start", "meta": {}},
        {"t": 3.6, "kind": "gate_done", "meta": {}},
        {"t": 3.8, "kind": "answer_first_event", "meta": {}},   # 底层流首 item(reasoning,旧口径)
        {"t": 4.0, "kind": "answer_first_token", "meta": {}},   # 首个正文 token(产品口径)
        {"t": 6.0, "kind": "answer_done", "meta": {}},
    ]
    s = summarize_events(events)
    st = s["stages"]
    assert st["seed"] == 1.0
    assert st["agent"] == 2.0            # seed_done → react_done
    assert st["gate"] == 0.5
    assert round(st["answer_ttft"], 4) == 0.4   # gate_done → 首个 token 事件(新口径)
    assert st["answer_stream"] == 2.0
    assert st["total"] == 6.0
    assert round(s["extras"]["answer_ttfe"], 4) == 0.2   # 旧口径:gate_done → 底层流首 item
    assert s["counts"]["tool_steps"] == 1
    assert s["counts"]["gate_ran"] is True
    assert s["counts"]["seed_callbacks"] == 1 and s["counts"]["seed_callback_fired"] is True
    assert s["counts"]["react_status"] == "ok" and s["counts"]["pool"] == 5


def test_summarize_events_no_gate_uses_react_for_ttft():
    # 空池 direct 路:无 gate 事件 → answer_ttft 从 react_done 起算。
    events = [
        {"t": 0.0, "kind": "run_start", "meta": {}},
        {"t": 0.5, "kind": "seed_done", "meta": {"count": 0}},
        {"t": 1.0, "kind": "react_done", "meta": {"status": "direct", "pool": 0}},
        {"t": 1.4, "kind": "answer_first_token", "meta": {}},
        {"t": 2.0, "kind": "answer_done", "meta": {}},
    ]
    st = summarize_events(events)["stages"]
    assert st["gate"] is None
    assert round(st["answer_ttft"], 4) == 0.4     # react_done → first token
    assert st["total"] == 2.0


# ---------------------------------------------------------------- 分位数 / 聚合

def test_percentile_linear_interpolation():
    assert _percentile([], 50) is None
    assert _percentile([5.0], 95) == 5.0
    assert _percentile([0.0, 10.0], 50) == 5.0
    # 1..10 → p50 中位 = 5.5;p95 = 9.55(线性插值)
    vals = [float(x) for x in range(1, 11)]
    assert _percentile(vals, 50) == 5.5
    assert abs(_percentile(vals, 95) - 9.55) < 1e-9


def test_aggregate_latency():
    per_q = [
        {"stages": {"seed": 1.0, "agent": 4.0, "gate": 0.5, "answer_ttft": 0.4,
                    "answer_stream": 2.0, "total": 7.9}, "counts": {"tool_steps": 2,
                    "react_status": "ok"}},
        {"stages": {"seed": 2.0, "agent": 6.0, "gate": None, "answer_ttft": 0.6,
                    "answer_stream": 3.0, "total": 11.6}, "counts": {"tool_steps": 1,
                    "react_status": "fallback"}},
    ]
    agg = aggregate_latency(per_q)
    assert agg["n"] == 2
    assert agg["by_stage"]["agent"]["p50"] == 5.0         # (4+6)/2 interp
    assert agg["by_stage"]["agent"]["max"] == 6.0
    assert agg["by_stage"]["gate"]["n"] == 1              # None 被排除
    assert agg["by_stage"]["gate"]["p50"] == 0.5
    assert agg["tool_steps"]["max"] == 2
    assert agg["fallbacks"] == 1


def test_format_table_has_bottleneck_line():
    per_q = [{"stages": {"seed": 1.0, "agent": 9.0, "gate": 0.5, "answer_ttft": 0.4,
                         "answer_stream": 2.0, "total": 12.9},
              "counts": {"tool_steps": 2, "react_status": "ok"}}]
    out = format_latency_table(aggregate_latency(per_q))
    assert "最大瓶颈" in out
    assert "ReAct 循环 agent" in out            # agent 是最大 p50 阶段


# ---------------------------------------------------------------- TimingLLM

class _FakeLLM:
    def complete(self, messages, **kw):
        return "yes"

    def stream(self, messages, **kw):
        yield "a"
        yield "b"

    def stream_events(self, messages, **kw):
        yield {"type": "reasoning", "text": "r"}
        yield {"type": "content", "text": "c"}


def test_timing_llm_marks_gate_and_answer():
    rec = LatencyRecorder(clock=_Clock([0, 1, 2, 3]))
    timed = TimingLLM(_FakeLLM(), rec)
    assert timed.complete([{"role": "user", "content": "x"}]) == "yes"   # gate_start/gate_done
    assert list(timed.stream_events([])) == [                            # first→done
        {"type": "reasoning", "text": "r"}, {"type": "content", "text": "c"}]
    kinds = [e["kind"] for e in rec.events]
    # 底层流首 item 记 answer_first_event(旧口径;产品口径 token 由 run_one_timed 另记)
    assert kinds == ["gate_start", "gate_done", "answer_first_event", "answer_done"]
    assert [e["t"] for e in rec.events] == [0, 1, 2, 3]


def test_timing_llm_stream_path_marks_answer():
    rec = LatencyRecorder(clock=_Clock([0, 1]))
    timed = TimingLLM(_FakeLLM(), rec)
    assert list(timed.stream([])) == ["a", "b"]        # 拒答/退化路走 .stream()
    assert [e["kind"] for e in rec.events] == ["answer_first_event", "answer_done"]


class _NoEventsLLM:
    """无 stream_events 的老 provider:answer.py 特征探测应优雅降级到 stream()。"""
    def complete(self, messages, **kw):
        return "yes"

    def stream(self, messages, **kw):
        yield "a"


def test_timing_llm_hides_stream_events_when_underlying_lacks_it():
    # 底层无 stream_events → 代理也不暴露(getattr 得 None),不骗探测方上主路。
    rec = LatencyRecorder(clock=_Clock([0, 1]))
    timed = TimingLLM(_NoEventsLLM(), rec)
    assert getattr(timed, "stream_events", None) is None
    assert list(timed.stream([])) == ["a"]             # 降级路照常打点
    assert [e["kind"] for e in rec.events] == ["answer_first_event", "answer_done"]


class _NoneEventsLLM:
    """stream_events 有名无实(=None)的假件:callable 检查须视同没有。"""
    stream_events = None

    def complete(self, messages, **kw):
        return "yes"

    def stream(self, messages, **kw):
        yield "a"


def test_timing_llm_non_callable_stream_events_treated_as_missing():
    # 底层 stream_events 非可调用(None)→ 代理 raise AttributeError(镜像 answer.py 的
    # callable(se) 检查),探测方 getattr(..., None) 得 None → 优雅降级到 stream()。
    rec = LatencyRecorder(clock=_Clock([0, 1]))
    timed = TimingLLM(_NoneEventsLLM(), rec)
    assert getattr(timed, "stream_events", None) is None
    assert list(timed.stream([])) == ["a"]
    assert [e["kind"] for e in rec.events] == ["answer_first_event", "answer_done"]


# ---------------------------------------------------------------- 单题真跑(全假件)

class _Acc:
    def __init__(self):
        self.chunks = []

    def extend(self, xs):
        self.chunks.extend(xs)


def test_run_one_timed_end_to_end(monkeypatch):
    # 桩掉懒导入的 agent 原语:seed_first_retrieval 触发专属 seed 回调(显式标记),
    # run_react_loop 触发 think/tool_step 回调并塞池;
    # stream_final_answer 用传入的 TimingLLM 代理触发 gate + answer 打点。
    def fake_seed(tools, accumulator, question, **kw):
        kw["on_step"]({"tool": "search_project_library", "query": question, "count": 3})

    def fake_loop(chat_model, tools, accumulator, question, **kw):
        assert kw.get("force_seed") is False          # seed 已在外部显式跑过,循环内不重跑
        kw["on_think"]("正在想搜什么")
        kw["on_step"]({"tool": "search_project_library", "query": "q2", "count": 2})  # 真实工具步
        accumulator.chunks.append(object())
        return "ok"

    def fake_stream(llm, question, pool, **kw):
        assert isinstance(llm, TimingLLM)
        llm.complete([{"role": "user", "content": "gate?"}])          # → gate_start/gate_done
        for ev in llm.stream_events([{"role": "user", "content": "ans"}]):
            if ev["type"] == "content":                               # 镜像 answer.py 分流:
                yield {"event": "token", "data": ev["text"]}          # 仅正文吐 token 事件
        yield {"event": "_answer", "data": "答案 [1]"}

    monkeypatch.setattr("epictrace.agent.react.seed_first_retrieval", fake_seed)
    monkeypatch.setattr("epictrace.agent.react.run_react_loop", fake_loop)
    monkeypatch.setattr("epictrace.agent.answer.stream_final_answer", fake_stream)
    monkeypatch.setattr("epictrace.agent.tools.build_tools", lambda **k: [])
    monkeypatch.setattr("epictrace.agent.tools.ChunkAccumulator", _Acc)

    rec = run_one_timed(_item(), build_chat_model=lambda: object(), llm=_FakeLLM(),
                        retriever=object(), project_id=1)

    kinds = [e["kind"] for e in rec["events"]]
    assert kinds == ["run_start", "seed_callback_fired", "seed_done", "think", "tool_step",
                     "react_done", "gate_start", "gate_done", "answer_first_event",
                     "answer_first_token", "answer_done"]
    assert rec["counts"]["tool_steps"] == 1           # seed 回调走专属标记,不计入工具步
    assert rec["counts"]["seed_callbacks"] == 1 and rec["counts"]["seed_callback_fired"] is True
    assert rec["counts"]["pool"] == 1
    st = rec["stages"]
    for name in ("seed", "agent", "gate", "answer_ttft", "answer_stream", "total"):
        assert st[name] is not None and st[name] >= 0.0
    assert st["total"] >= st["agent"]
    # 旧口径首事件(reasoning)先于新口径首 token → answer_ttfe ≤ answer_ttft
    assert rec["extras"]["answer_ttfe"] is not None
    assert rec["extras"]["answer_ttfe"] <= st["answer_ttft"]
    assert rec["answer_len"] == len("答案 [1]")


def test_run_one_timed_seed_silent_failure_attributed_to_seed(monkeypatch):
    # seed 工具缺失/静默吞错 → 无回调:seed 边界仍**无条件**记录(返回时刻),失败 seed 的
    # 耗时归 seed 段而非混进 agent 段;回调未触发要可见(seed_callback_fired=False)。
    def fake_seed(tools, accumulator, question, **kw):
        pass                                          # 无回调(工具缺失/内部吞错)

    def fake_loop(chat_model, tools, accumulator, question, **kw):
        kw["on_step"]({"tool": "search_project_library", "query": "q", "count": 2})
        accumulator.chunks.append(object())
        return "ok"

    def fake_stream(llm, question, pool, **kw):
        for tok in llm.stream([{"role": "user", "content": "ans"}]):
            yield {"event": "token", "data": tok}
        yield {"event": "_answer", "data": "答"}

    monkeypatch.setattr("epictrace.agent.react.seed_first_retrieval", fake_seed)
    monkeypatch.setattr("epictrace.agent.react.run_react_loop", fake_loop)
    monkeypatch.setattr("epictrace.agent.answer.stream_final_answer", fake_stream)
    monkeypatch.setattr("epictrace.agent.tools.build_tools", lambda **k: [])
    monkeypatch.setattr("epictrace.agent.tools.ChunkAccumulator", _Acc)

    rec = run_one_timed(_item(), build_chat_model=lambda: object(), llm=_FakeLLM(),
                        retriever=object(), project_id=1)

    kinds = [e["kind"] for e in rec["events"]]
    assert "seed_callback_fired" not in kinds          # 回调确实没触发
    assert kinds.count("seed_done") == 1               # 边界仍无条件记录
    assert rec["counts"]["seed_callback_fired"] is False
    assert rec["stages"]["seed"] is not None           # 失败 seed 耗时可测且归 seed 段
    assert rec["counts"]["tool_steps"] == 1            # 真实工具步照常计数、不被误标
    assert rec["stages"]["agent"] is not None          # agent 从 seed 边界起算
    assert rec["stages"]["total"] is not None


class _EmptyStreamLLM:
    """流式零产出的极端 provider:answer.py 拒答路会在流耗尽后补发兜底 token。"""
    def complete(self, messages, **kw):
        return "no"

    def stream(self, messages, **kw):
        return iter(())


def test_run_one_timed_refusal_fallback_token_still_gets_ttft(monkeypatch):
    # 拒答兜底路(answer.py:底层流零产出,流耗尽后补发兜底 token):首 token 仍要被记到;
    # 此时 answer_done(流耗尽时刻)早于首 token → run_one_timed 补记校正,answer_stream 非负。
    def fake_seed(tools, accumulator, question, **kw):
        kw["on_step"]({"tool": "search_project_library", "query": question, "count": 1})

    def fake_loop(chat_model, tools, accumulator, question, **kw):
        accumulator.chunks.append(object())
        return "ok"

    def fake_stream(llm, question, pool, **kw):
        # 镜像 answer.py 拒答路:gate(complete)→ 流(零产出)→ 兜底 token
        llm.complete([{"role": "user", "content": "gate?"}])
        parts = []
        for tok in llm.stream([{"role": "user", "content": "refuse"}]):
            parts.append(tok)
            yield {"event": "token", "data": tok}
        if not parts:
            yield {"event": "token", "data": "现有资料没有涉及"}
        yield {"event": "_answer", "data": "现有资料没有涉及"}

    monkeypatch.setattr("epictrace.agent.react.seed_first_retrieval", fake_seed)
    monkeypatch.setattr("epictrace.agent.react.run_react_loop", fake_loop)
    monkeypatch.setattr("epictrace.agent.answer.stream_final_answer", fake_stream)
    monkeypatch.setattr("epictrace.agent.tools.build_tools", lambda **k: [])
    monkeypatch.setattr("epictrace.agent.tools.ChunkAccumulator", _Acc)

    rec = run_one_timed(_item(), build_chat_model=lambda: object(), llm=_EmptyStreamLLM(),
                        retriever=object(), project_id=1)

    kinds = [e["kind"] for e in rec["events"]]
    assert "answer_first_token" in kinds               # 兜底 token 也计 TTFT
    assert "answer_first_event" not in kinds           # 底层流零产出,无旧口径首事件
    assert rec["extras"]["answer_ttfe"] is None
    st = rec["stages"]
    assert st["answer_ttft"] is not None
    assert st["answer_stream"] is not None and st["answer_stream"] >= 0.0
    assert st["total"] is not None


def test_run_latency_profile_aggregates_and_samples(monkeypatch):
    # 桩掉 run_one_timed,验证抽样 + 聚合装配(不碰重依赖)。
    calls = []

    def fake_run_one(it, **kw):
        calls.append(it.id)
        return {"id": it.id, "slices": it.slices,
                "stages": {"seed": 1.0, "agent": 2.0, "gate": 0.5, "answer_ttft": 0.3,
                           "answer_stream": 1.0, "total": 4.8},
                "counts": {"tool_steps": 1, "react_status": "ok", "pool": 3},
                "answer_len": 5, "events": []}

    monkeypatch.setattr(latency, "run_one_timed", fake_run_one)
    golden = [_item("g1"), _item("g2"), _item("g3")]
    res = latency.run_latency_profile(golden, build_chat_model=lambda: object(),
                                      llm=object(), retriever=object(), project_id=1,
                                      sample=2, progress=False)
    assert calls == ["g1", "g2"]                    # 只抽前 2 题
    assert res["n"] == 2
    assert res["aggregate"]["by_stage"]["total"]["p50"] == 4.8


# ---------------------------------------------------------------- CLI 子命令

def test_latency_cli_writes_and_prints(tmp_path, monkeypatch, capsys):
    from scripts.rag_eval import cli, wiring

    golden_path = tmp_path / "g.jsonl"
    save_golden([_item("g1"), _item("g2")], golden_path)

    monkeypatch.setattr(wiring, "build_chat_model_factory", lambda: (lambda: object()))
    monkeypatch.setattr(wiring, "build_llm", lambda: object())
    monkeypatch.setattr(wiring, "build_retriever", lambda pid: object())

    pq = [{"id": "g1", "slices": {}, "stages": {"seed": 1.0, "agent": 4.0, "gate": 0.5,
            "answer_ttft": 0.4, "answer_stream": 2.0, "total": 7.9},
           "counts": {"tool_steps": 2, "react_status": "ok", "pool": 3},
           "answer_len": 10, "events": []}]
    canned = {"n": 1, "per_question": pq, "aggregate": aggregate_latency(pq)}
    monkeypatch.setattr(latency, "run_latency_profile", lambda *a, **k: canned)
    monkeypatch.setattr(cli, "_RUNS", tmp_path / "runs")

    rc = cli.main(["latency", "--golden", str(golden_path), "--project-id", "2",
                   "--sample", "2", "--label", "utest"])
    assert rc == 0
    assert "最大瓶颈" in capsys.readouterr().out
    run_dir = tmp_path / "runs" / "latency-utest"
    assert (run_dir / "per_question.jsonl").exists()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["n"] == 1
    assert summary["aggregate"]["by_stage"]["agent"]["p50"] == 4.0
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "latency" and meta["sample"] == 2

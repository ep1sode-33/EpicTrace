"""透明对话·阶段2:run_react_loop / seed_first_retrieval 的 on_step 回调把每次知识库检索
作为一步透明化(工具名 + 查询 + 命中段数);stream_mode=values 重复给全量消息时不重复计步。"""
from langchain_core.messages import AIMessage

from epictrace.agent.react import run_react_loop, seed_first_retrieval
from epictrace.agent.tools import ChunkAccumulator
from tests.fakes import FakeChatModel
from tests.test_agent_react import _Retr, _call, _proj_chunk, _tools


def test_on_step_emits_tool_step_with_query_and_count():
    retr = _Retr([_proj_chunk("a"), _proj_chunk("b", cs=4, ce=8)])
    model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "TLB是什么"})]),
        AIMessage(content="结束"),
    ])
    acc = ChunkAccumulator()
    steps: list[dict] = []
    run_react_loop(model, _tools(retr), acc, "TLB是什么", history=[],
                   force_seed=False, on_step=steps.append)
    # 恰好一步(不因 values 重复消息而多计),带查询词 + 命中段数。
    assert steps == [{"tool": "search_project_library", "query": "TLB是什么", "count": 2}]


def test_force_seed_emits_a_step():
    retr = _Retr([_proj_chunk("x")])
    model = FakeChatModel(script=[AIMessage(content="我直接答")])  # agent 不调工具
    acc = ChunkAccumulator()
    steps: list[dict] = []
    run_react_loop(model, _tools(retr), acc, "什么是X", history=[], on_step=steps.append)
    # 强制预检索用原始问题 → 一步;agent 自己没调工具 → 无其它步。
    assert {"tool": "search_project_library", "query": "什么是X", "count": 1} in steps


def test_seed_first_retrieval_on_step_standalone():
    retr = _Retr([_proj_chunk("y")])
    acc = ChunkAccumulator()
    steps: list[dict] = []
    seed_first_retrieval(_tools(retr), acc, "问题", on_step=steps.append)
    assert steps == [{"tool": "search_project_library", "query": "问题", "count": 1}]


def test_no_on_step_is_noop_backward_compat():
    # 不传 on_step(eval / 老调用点)→ 行为不变,不崩。
    retr = _Retr([_proj_chunk("z")])
    model = FakeChatModel(script=[AIMessage(content="结束")])
    acc = ChunkAccumulator()
    status = run_react_loop(model, _tools(retr), acc, "q", history=[])
    assert status == "ok"

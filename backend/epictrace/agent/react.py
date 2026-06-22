from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from epictrace.agent.state import ReactState
from epictrace.agent.tools import ChunkAccumulator

FALLBACK = "fallback"  # 第一轮就崩 + 池空 → 让 ChatService 回退 Plan 5

LOOP_SYS = (
    "你是检索助手,用工具搜集回答用户问题所需的资料,可在一轮里并行调用多个工具。"
    "只要问题涉及任何具体知识、事实或项目内容,即使你自认为知道答案,也必须先调用检索工具取证再作答——"
    "本系统的回答必须有资料来源支撑、可追溯,不可仅凭记忆直接作答;"
    "资料够了就停止调用工具、直接回普通消息(其文本会被丢弃);"
    "仅当是纯打招呼/闲聊等完全无需任何资料时才不调用任何工具。"
)


def run_react_loop(chat_model, tools, accumulator: ChunkAccumulator, question: str,
                   *, history: list[dict], max_rounds: int = 8,
                   attachment_manifest: str = "") -> str:
    """跑 agent↔tools 循环,只攒池(chunk 从 ToolMessage.artifact 收割)。返回状态:
      "ok"      → 池里有 chunk(或正常停手),交给 GENERATE 作答;
      "direct"  → 全程未调工具且池空(寒暄)→ ChatService 走 direct 直答;
      FALLBACK  → 第一轮就崩且池空 → ChatService 回退 Plan 5。
    鲁棒:撞 max_rounds → 停搜 force-answer;某轮 invoke 抛错 → 重试 1 次,再坏则按池空/非空收尾。"""
    bound = chat_model.bind_tools(tools)
    tool_node = ToolNode(tools)

    def agent(state: ReactState) -> ReactState:
        rounds = state.get("rounds", 0)
        # 撞轮数上限:不再给工具,逼模型停手(它的文本被丢弃,只用已攒池)。
        if rounds >= max_rounds:
            return {"messages": [AIMessage(content="")], "rounds": rounds}
        msg = bound.invoke(state["messages"])
        # 模型想调工具但 JSON 坏了(langchain 塞进 invalid_tool_calls 而非 tool_calls)且
        # 没有任何合法 tool_call → 抛错,流入"重试 1 次 → force-answer / FALLBACK"路;
        # 绝不当成"干净停手/direct"。
        if getattr(msg, "invalid_tool_calls", None) and not getattr(msg, "tool_calls", None):
            raise ValueError("invalid_tool_calls without any valid tool_calls")
        return {"messages": [msg], "rounds": rounds + 1}

    def harvest(state: ReactState) -> ReactState:
        # ToolNode 刚把每个工具的 ToolMessage(含 .artifact=chunk 列表)写进 messages;
        # 收割最近一批 ToolMessage 的 artifact 进累积池(去重/封顶在 accumulator 内)。
        for m in reversed(state["messages"]):
            if isinstance(m, ToolMessage):
                if m.artifact:
                    accumulator.extend(list(m.artifact))
            elif isinstance(m, AIMessage):
                break  # 越过本轮 tool 结果就停(更早的已在上一轮收割过)
        return {}

    def route(state: ReactState) -> str:
        # 轮数上限的强制停手只在 agent 节点判(撞顶→回空的无工具消息);route 只看最后一条
        # 是否真要调工具。这样"最后一批被请求的工具"仍会被执行/收割,而非被悄悄丢弃。
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "end"

    g = StateGraph(ReactState)
    g.add_node("agent", agent)
    g.add_node("tools", tool_node)
    g.add_node("harvest", harvest)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", "end": END})
    g.add_edge("tools", "harvest")
    g.add_edge("harvest", "agent")
    graph = g.compile()

    # 把可读附件清单拼进循环系统提示,模型才知道有哪些 reference_id 可传给 read_attachment。
    sys_text = LOOP_SYS
    if attachment_manifest:
        sys_text = f"{LOOP_SYS}\n\n可读附件清单(可用 read_attachment 按 reference_id 通读):\n{attachment_manifest}"
    init = [SystemMessage(content=sys_text)]
    for h in history:
        # 复用历史轮次的纯文本上下文(role→LangChain 消息;assistant 文本用 AIMessage)。
        if h["role"] == "user":
            init.append(HumanMessage(content=h["content"]))
        else:
            init.append(AIMessage(content=h["content"]))
    init.append(HumanMessage(content=question))

    used_tools = False
    try:
        for ev in graph.stream({"messages": init, "rounds": 0}, stream_mode="values"):
            if any(isinstance(m, ToolMessage) for m in ev["messages"]):
                used_tools = True
    except Exception:  # noqa: BLE001 — invoke 抛错(坏 tool_call 等):重试 1 次
        try:
            for ev in graph.stream({"messages": init, "rounds": 0}, stream_mode="values"):
                if any(isinstance(m, ToolMessage) for m in ev["messages"]):
                    used_tools = True
        except Exception:  # noqa: BLE001 — 再坏:池非空 force-answer,池空回退
            return "ok" if accumulator.chunks else FALLBACK

    if accumulator.chunks:
        return "ok"
    return "direct" if not used_tools else "ok"

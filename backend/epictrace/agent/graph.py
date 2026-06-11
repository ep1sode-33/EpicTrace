from __future__ import annotations

from langgraph.graph import END, StateGraph

from epictrace.agent.prompts import GRADE_SYS, REWRITE_SYS, format_chunks
from epictrace.agent.state import AgentState


def build_rag_graph(llm, retriever, max_iterations: int = 2):
    """智能体检索环:retrieve → grade(反思充分性)→ 不足则 rewrite→retrieve(有界),
    足够或到上限即结束。终态输出最终 chunks/query;**答案不在图内生成**——由 ChatService
    流式生成唯一一次(避免双重 LLM 调用)。"""

    def retrieve(state: AgentState) -> AgentState:
        chunks = retriever.retrieve(project_id=state["project_id"], query=state["query"])
        return {"chunks": chunks}

    def grade(state: AgentState) -> AgentState:
        verdict = llm.complete([
            {"role": "system", "content": GRADE_SYS},
            {"role": "user", "content": f"问题:{state['question']}\n\n资料:\n{format_chunks(state['chunks'])}"},
        ]).strip().lower()
        return {"_grade": "insufficient" if "insufficient" in verdict else "sufficient"}

    def decide(state: AgentState) -> str:
        if state.get("_grade") == "sufficient":
            return "end"
        if state.get("iterations", 0) >= max_iterations:
            return "end"
        return "rewrite"

    def rewrite(state: AgentState) -> AgentState:
        new_q = llm.complete([
            {"role": "system", "content": REWRITE_SYS},
            {"role": "user", "content": f"问题:{state['question']}\n原查询:{state['query']}"},
        ]).strip()
        return {"query": new_q or state["query"], "iterations": state.get("iterations", 0) + 1}

    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve)
    g.add_node("grade", grade)
    g.add_node("rewrite", rewrite)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", decide, {"end": END, "rewrite": "rewrite"})
    g.add_edge("rewrite", "retrieve")
    return g.compile()

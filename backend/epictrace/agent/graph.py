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
        # 零 chunk 不喂 LLM:既省一次调用,也杜绝"无资料却判 sufficient"的幻觉
        # (空资料 → 必然 insufficient → 继续改写检索,直到迭代上限才兜底生成)。
        if not state.get("chunks"):
            return {"_grade": "insufficient"}
        verdict = llm.complete([
            {"role": "system", "content": GRADE_SYS},
            {"role": "user", "content": f"问题:{state['question']}\n\n资料:\n{format_chunks(state['chunks'])}"},
        ]).strip().lower()
        # 严格解析:必须明确出现 sufficient 且不含 insufficient 才算充分;
        # 含糊/垃圾输出一律按 insufficient 处理(保守 → 继续重试,而非误判充分)。
        sufficient = "sufficient" in verdict and "insufficient" not in verdict
        return {"_grade": "sufficient" if sufficient else "insufficient"}

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

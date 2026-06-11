from __future__ import annotations

from typing import TypedDict

from epictrace.retrieval.types import RetrievedChunk


class AgentState(TypedDict, total=False):
    project_id: int
    question: str
    query: str
    history: list[dict]
    chunks: list[RetrievedChunk]
    iterations: int
    _grade: str          # grade 节点写、decide 读;不生成答案(交给 ChatService 流式)

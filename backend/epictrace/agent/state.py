from __future__ import annotations

from typing import TypedDict

from epictrace.retrieval.types import RetrievedChunk


class AgentState(TypedDict, total=False):
    project_id: int
    question: str
    query: str
    route: str           # route 节点写:"retrieve" → 走检索环;"direct" → 直接结束(无 chunk)
    history: list[dict]
    chunks: list[RetrievedChunk]
    iterations: int
    _grade: str          # grade 节点写、decide 读;不生成答案(交给 ChatService 流式)

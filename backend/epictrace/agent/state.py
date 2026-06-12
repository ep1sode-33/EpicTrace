from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

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
    focus_ids: list[int]   # ChatService 写:pin 的内部文件(聚焦检索);空/缺省=全项目


class ReactState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    rounds: int          # agent 节点跑过的轮数(撞上限→force-answer)

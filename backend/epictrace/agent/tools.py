from __future__ import annotations

from langchain_core.tools import tool

from epictrace.agent.attachment_paging import read_attachment_slice
from epictrace.retrieval.types import RetrievedChunk

_SNIPPET = 280  # 给模型读的截断长度(决策够用,不撑爆上下文)


class ChunkAccumulator:
    """跨轮收集工具产出的 RetrievedChunk:按 RetrievedChunk.key() 去重、封顶(≤12)。
    工具用 artifact 把 chunk 旁路给 ReAct 循环,循环把它们 extend 进这里,不污染模型可见文本。"""

    def __init__(self, cap: int = 12) -> None:
        self._cap = cap
        self._seen: set = set()
        self.chunks: list[RetrievedChunk] = []

    def extend(self, new_chunks: list[RetrievedChunk]) -> None:
        for c in new_chunks:
            if len(self.chunks) >= self._cap:
                return
            k = c.key()
            if k in self._seen:
                continue
            self._seen.add(k)
            self.chunks.append(c)


def _render(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(无结果)"
    return "\n".join(f"- {c.text[:_SNIPPET]}" for c in chunks)


def build_tools(*, retriever, project_id: int, focus_ids: list[int],
                attachment_retriever, conversation_id: int,
                indexed_ext_ids: list[int], reference_texts: dict[int, str]):
    """构造本轮暴露给 agent 的工具列表。附件类工具仅在有 indexed 外部引用时暴露
    (替 Plan 5 压制启发式:不暴露=agent 看不见,而非硬切)。
    每个工具 response_format='content_and_artifact':content 给模型读,artifact=chunk 列表
    旁路进累积池。"""

    @tool(response_format="content_and_artifact")
    def search_project_library(query: str):
        """检索本项目的永久知识库(课程/会话/笔记等已归档资料)。回答涉及项目内部内容时用。
        query 为中文检索词。"""
        kwargs = {"ingest_record_ids": focus_ids} if focus_ids else {}
        chunks = retriever.retrieve(project_id=project_id, query=query, **kwargs)
        return _render(chunks), chunks

    tools = [search_project_library]

    if attachment_retriever is not None and indexed_ext_ids:
        @tool(response_format="content_and_artifact")
        def search_attachment(query: str):
            """语义检索用户本次对话附加的外部文件。问题针对所附文件的具体内容/片段时用。
            query 为中文检索词。"""
            ar = attachment_retriever() if callable(attachment_retriever) else attachment_retriever
            chunks = ar.retrieve(conversation_id=conversation_id,
                                 reference_ids=indexed_ext_ids, query=query)
            return _render(chunks), chunks

        @tool(response_format="content_and_artifact")
        def read_attachment(reference_id: int, cursor: int = 0):
            """从 cursor 处顺序读取某个附件的下一段原文(分页)。需要通读/总结整篇,或检索
            片段不足时,反复调用并传上次返回的 next_cursor 翻页。"""
            text = reference_texts.get(reference_id)
            if text is None:
                return f"(reference_id={reference_id} 不是本次对话的可读附件)", []
            slice_text, next_cursor, chunk, done = read_attachment_slice(
                reference_id=reference_id, text=text, cursor=cursor)
            if chunk is None:
                return f"(已到文件末尾,无更多内容;done={done})", []
            hint = f"\n\n[next_cursor={next_cursor}, done={done}]"
            return slice_text[:_SNIPPET] + hint, [chunk]

        tools.extend([search_attachment, read_attachment])

    return tools

from __future__ import annotations

import json
from collections.abc import Iterator

from epictrace.agent.citations import build_citations
from epictrace.agent.graph import build_rag_graph
from epictrace.agent.prompts import GENERATE_SYS, format_chunks
from epictrace.db import Database
from epictrace.models import Conversation, Message


class ChatService:
    def __init__(self, db: Database, llm, retriever) -> None:
        self._db = db
        self._llm = llm
        self._retriever = retriever

    def stream_answer(self, conversation_id: int, question: str) -> Iterator[dict]:
        # 落 user message
        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="user", content=question))

        yield {"event": "status", "data": "检索中"}
        # 跑图到拿到最终 chunks(grade/rewrite 在图里),但生成改为这里流式
        graph = build_rag_graph(self._llm, self._retriever)
        state = graph.invoke({"project_id": self._project_id(conversation_id), "question": question,
                              "query": question, "history": [], "iterations": 0})
        chunks = state.get("chunks", [])

        yield {"event": "status", "data": "生成中"}
        # 流式生成最终答案(用与图内 generate 相同的提示词)
        parts: list[str] = []
        for tok in self._llm.stream([
            {"role": "system", "content": GENERATE_SYS},
            {"role": "user", "content": f"问题:{question}\n\n【资料】\n{format_chunks(chunks)}"},
        ]):
            parts.append(tok)
            yield {"event": "token", "data": tok}
        answer = "".join(parts)

        citations = build_citations(answer, chunks)
        yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}

        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="assistant", content=answer,
                          citations_json=json.dumps(citations, ensure_ascii=False)))
        yield {"event": "done", "data": ""}

    def _project_id(self, conversation_id: int) -> int:
        with self._db.session() as s:
            c = s.get(Conversation, conversation_id)
            return c.project_id if c else 0

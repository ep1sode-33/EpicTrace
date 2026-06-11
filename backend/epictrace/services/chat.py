from __future__ import annotations

import json
from collections.abc import Iterator

from sqlalchemy import select

from epictrace.agent.citations import build_citations
from epictrace.agent.graph import build_rag_graph
from epictrace.agent.prompts import GENERATE_SYS, format_chunks
from epictrace.db import Database
from epictrace.models import Conversation, Message, _utcnow

_DEFAULT_TITLE = "新对话"
_TITLE_MAX = 30


class ChatService:
    def __init__(self, db: Database, llm, retriever) -> None:
        self._db = db
        self._llm = llm
        self._retriever = retriever

    def stream_answer(self, conversation_id: int, question: str) -> Iterator[dict]:
        # 先读历史(本轮 user message 尚未落库,故不会把它算进历史),再落 user message。
        history = self._load_history(conversation_id)
        is_first_user_turn = not any(m["role"] == "user" for m in history)
        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="user", content=question))

        yield {"event": "status", "data": "检索中"}
        # 检索 + 生成全程兜异常:任一步抛错 → 发 error 事件并中止(不落半截 assistant 消息)。
        try:
            # 跑图到拿到最终 chunks(grade/rewrite 在图里),但生成改为这里流式。
            graph = build_rag_graph(self._llm, self._retriever)
            state = graph.invoke({"project_id": self._project_id(conversation_id), "question": question,
                                  "query": question, "history": history, "iterations": 0})
            chunks = state.get("chunks", [])

            yield {"event": "status", "data": "生成中"}
            # 多轮:系统提示 → 历史轮次 → 本轮(问题 + 【资料】)。用与图内 grade 相同的引用提示词。
            messages = [{"role": "system", "content": GENERATE_SYS}]
            messages.extend(history)
            messages.append(
                {"role": "user", "content": f"问题:{question}\n\n【资料】\n{format_chunks(chunks)}"}
            )
            parts: list[str] = []
            for tok in self._llm.stream(messages):
                parts.append(tok)
                yield {"event": "token", "data": tok}
        except Exception as exc:  # noqa: BLE001 — 把任何后端/LLM/检索故障转成前端可读的 error 事件
            yield {"event": "error", "data": str(exc)}
            return

        answer = "".join(parts)
        citations = build_citations(answer, chunks)
        yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}

        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="assistant", content=answer,
                          citations_json=json.dumps(citations, ensure_ascii=False)))
            # 完成一轮:更新会话时序;首轮且仍是默认标题 → 用问题首段当标题。
            c = s.get(Conversation, conversation_id)
            if c is not None:
                c.updated_at = _utcnow()
                if is_first_user_turn and c.title == _DEFAULT_TITLE:
                    c.title = question[:_TITLE_MAX]
        yield {"event": "done", "data": ""}

    def _load_history(self, conversation_id: int) -> list[dict]:
        """按时间顺序取本会话已落库的消息(role/content),供多轮上下文用。"""
        with self._db.session() as s:
            rows = s.execute(
                select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
            ).scalars()
            return [{"role": m.role, "content": m.content} for m in rows]

    def _project_id(self, conversation_id: int) -> int:
        with self._db.session() as s:
            c = s.get(Conversation, conversation_id)
            return c.project_id if c else 0

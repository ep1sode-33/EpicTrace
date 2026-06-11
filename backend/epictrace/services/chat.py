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
# 直答路径(图判定 route=direct,无 chunk):像普通聊天助手作答,不带【资料】、不产生引用。
CHAT_SYS = "你是有帮助的助手,用中文简洁作答。"
TITLE_SYS = "给这段对话起一个不超过 12 字的简短中文标题,只回标题本身。"


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
            # 有资料 → 带引用作答(GENERATE_SYS + 【资料】);无资料(direct 路由)→ 普通聊天作答。
            # 多轮:系统提示 → 历史轮次 → 本轮。
            if chunks:
                sys_prompt = GENERATE_SYS
                user_content = f"问题:{question}\n\n【资料】\n{format_chunks(chunks)}"
            else:
                sys_prompt = CHAT_SYS
                user_content = question
            messages = [{"role": "system", "content": sys_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_content})
            parts: list[str] = []
            for tok in self._llm.stream(messages):
                parts.append(tok)
                yield {"event": "token", "data": tok}
        except Exception as exc:  # noqa: BLE001 — 把任何后端/LLM/检索故障转成前端可读的 error 事件
            yield {"event": "error", "data": str(exc)}
            return

        answer = "".join(parts)
        # 无 chunk(direct)→ 不抽引用,citations 为空;有 chunk → 从答案的 [n] 抽引用。
        citations = build_citations(answer, chunks) if chunks else []
        yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}

        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="assistant", content=answer,
                          citations_json=json.dumps(citations, ensure_ascii=False)))
            # 完成一轮:更新会话时序;首轮且仍是默认标题 → 让 LLM 起一个简短标题。
            c = s.get(Conversation, conversation_id)
            if c is not None:
                c.updated_at = _utcnow()
                if is_first_user_turn and c.title == _DEFAULT_TITLE:
                    c.title = self._make_title(question)
        yield {"event": "done", "data": ""}

    def _make_title(self, question: str) -> str:
        """首轮自动命名:一次廉价 LLM 调用产出简短标题;失败/为空 → 回退到问题首段。"""
        fallback = question[:20]
        try:
            title = self._llm.complete([
                {"role": "system", "content": TITLE_SYS},
                {"role": "user", "content": question},
            ]).strip().strip("\"'“”‘’ ")
        except Exception:  # noqa: BLE001 — 标题失败不该影响主回答
            return fallback
        return (title or fallback)[:_TITLE_MAX]

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

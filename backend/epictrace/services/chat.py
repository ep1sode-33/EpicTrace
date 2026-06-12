from __future__ import annotations

import json
from collections.abc import Iterator

from sqlalchemy import select

from epictrace.agent.citations import build_citations
from epictrace.agent.graph import build_rag_graph
from epictrace.agent.prompts import GENERATE_SYS, format_chunks
from epictrace.db import Database
from epictrace.models import Conversation, Message, _utcnow
from epictrace.retrieval.types import RetrievedChunk

_DEFAULT_TITLE = "新对话"
_TITLE_MAX = 30
# 直答路径(图判定 route=direct,无 chunk):像普通聊天助手作答,不带【资料】、不产生引用。
CHAT_SYS = "你是有帮助的助手,用中文简洁作答。"
TITLE_SYS = "给这段对话起一个不超过 12 字的简短中文标题,只回标题本身。"


def _ref_chunk(r: dict) -> RetrievedChunk:
    """把"全文引用"包成单个 chunk 注入【资料】。外部→attachment(跳回外部文件),
    内部→project(跳回项目文件,带 ingest_record_id);char 区间覆盖整段(文件级引用)。"""
    text = r.get("extracted_text") or ""
    is_ext = r["kind"] == "external"
    return RetrievedChunk(
        text=text, ingest_record_id=r.get("ingest_record_id") or 0, project_id=0,
        char_start=0, char_end=len(text),
        source_type="attachment" if is_ext else "folder_scan",
        source_kind="attachment" if is_ext else "project",
        reference_id=r["id"],
    )


class ChatService:
    def __init__(self, db: Database, llm, retriever, references=None,
                 attachment_retriever=None) -> None:
        self._db = db
        self._llm = llm
        self._retriever = retriever
        self._references = references
        self._attachment_retriever = attachment_retriever

    def stream_answer(self, conversation_id: int, question: str) -> Iterator[dict]:
        # 先读历史(本轮 user message 尚未落库,故不会把它算进历史),再落 user message。
        history = self._load_history(conversation_id)
        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="user", content=question))
        yield from self._run_turn(conversation_id, question, history)

    def stream_regenerate(self, conversation_id: int) -> Iterator[dict]:
        """重生成最后一轮:找最后一条 user 消息,删它之后的所有消息(上轮或失败的 assistant),
        用它之前的消息作历史、对同一个 user 问题重跑同一条流水线(不新增 user 消息)。"""
        msgs = self._load_history(conversation_id)
        # 找最后一条 user 消息的下标;无 → 无可重生成的内容。
        last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i]["role"] == "user"), None)
        if last_user is None:
            yield {"event": "error", "data": "没有可重新生成的提问"}
            return
        question = msgs[last_user]["content"]
        history = msgs[:last_user]
        # 删该 user 消息之后的所有消息(已生成或失败的 assistant 轮次),避免重复累积。
        self._delete_messages_after(conversation_id, keep_count=last_user + 1)
        yield from self._run_turn(conversation_id, question, history)

    def stream_edit(self, conversation_id: int, message_id: int, content: str) -> Iterator[dict]:
        """编辑某条 user 消息并就地重生成:把该消息内容改为 content,删它之后的所有消息,
        以它之前的消息作历史、对新内容重跑同一条流水线(不新增 user 消息)。
        泛化自 regenerate——regenerate 是「重跑最后一条 user 的原内容」,这里指定任意 user 消息 + 新内容。"""
        with self._db.session() as s:
            rows = s.execute(
                select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
            ).scalars().all()
            # 找目标消息的下标,并校验它确是本会话内的一条 user 消息。
            idx = next((i for i, m in enumerate(rows) if m.id == message_id), None)
            if idx is None or rows[idx].role != "user":
                yield {"event": "error", "data": "找不到可编辑的提问"}
                return
            rows[idx].content = content                 # 就地改写该 user 消息
            # 编辑点之前的消息作历史(在会话内取值,出会话后 ORM 对象即失效)。
            history = [{"role": m.role, "content": m.content} for m in rows[:idx]]
        # 删该 user 消息之后的全部消息(旧/失败的 assistant 及后续轮次),避免重复累积。
        self._delete_messages_after(conversation_id, keep_count=idx + 1)
        yield from self._run_turn(conversation_id, content, history)

    def _run_turn(self, conversation_id: int, question: str, history: list[dict]) -> Iterator[dict]:
        """一轮生成的共享核心(stream_answer / stream_regenerate 共用):
        路由→(可选)检索→流式→引用→落 assistant 消息 + 首轮自动命名。
        前置条件:本轮 user 消息状态已就绪(stream_answer 先落库;regenerate 复用已存在的)。"""
        is_first_user_turn = not any(m["role"] == "user" for m in history)
        # 首发用中性「思考中」:此刻还没判 route,direct 直答并不真检索,「检索中」会误导。
        yield {"event": "status", "data": "思考中"}
        # 检索 + 生成全程兜异常:任一步抛错 → 发 error 事件并中止(不落半截 assistant 消息)。
        try:
            # 跑图到拿到最终 chunks(grade/rewrite 在图里),但生成改为这里流式。
            refs = self._references.list_active(conversation_id) if self._references else []
            fulltext_refs = [r for r in refs if r["mode"] == "fulltext"]
            focus_ids = [r["ingest_record_id"] for r in refs
                         if r["mode"] == "focus" and r.get("ingest_record_id")]
            indexed_ext_ids = [r["id"] for r in refs
                               if r["mode"] == "indexed" and r["kind"] == "external"]
            graph = build_rag_graph(self._llm, self._retriever)
            state = graph.invoke({"project_id": self._project_id(conversation_id),
                                  "question": question, "query": question, "history": history,
                                  "iterations": 0, "focus_ids": focus_ids})
            # 全文引用恒在最前;其后接项目检索;再接附件临时 RAG 检索(有活跃 indexed 引用时)。
            attach_chunks = []
            if indexed_ext_ids and self._attachment_retriever is not None:
                ar = (self._attachment_retriever()
                      if callable(self._attachment_retriever)
                      else self._attachment_retriever)
                attach_chunks = ar.retrieve(
                    conversation_id=conversation_id, reference_ids=indexed_ext_ids, query=question)
            chunks = [_ref_chunk(r) for r in fulltext_refs] + state.get("chunks", []) + attach_chunks

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

    def _delete_messages_after(self, conversation_id: int, keep_count: int) -> None:
        """保留按 id 升序的前 keep_count 条消息,删除其后的全部(重生成时清掉旧/失败 assistant 轮次)。"""
        with self._db.session() as s:
            rows = s.execute(
                select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
            ).scalars().all()
            for m in rows[keep_count:]:
                s.delete(m)

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

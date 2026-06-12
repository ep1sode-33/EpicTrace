from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from epictrace.api.deps import (
    get_db, get_llm, get_retriever, get_embedder, get_reranker, get_attachment_store,
)
from epictrace.api.routers.references import _Lazy
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project
from epictrace.retrieval.attachment import AttachmentRetriever
from epictrace.schemas import ConversationCreate, ConversationOut, MessageCreate, MessageOut
from epictrace.services.chat import ChatService
from epictrace.services.references import ReferenceService

router = APIRouter(tags=["conversations"])  # /api 由 app 工厂统一挂载


def _chat_service(request: Request, db: Database) -> ChatService:
    llm = get_llm(request)
    if llm is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "对话模型未配置:请在设置里填写 OpenAI-Compatible 端点")
    # 工厂 + 惰性代理:只有当本轮真有活跃 indexed 引用(ChatService 调 attach())或外部大文件
    # 真正走 indexed 分支(ReferenceService 用 embedder/store)时,才构造附件 Milvus/模型;
    # 无附件的普通聊天不该急切起第二个 Milvus 客户端 / BGE-M3(见 macos-embedding-milvus-fork-order)。
    attach = lambda: AttachmentRetriever(get_embedder(request), get_attachment_store(request),  # noqa: E731
                                         get_reranker(request))
    refs = ReferenceService(db, embedder=_Lazy(lambda: get_embedder(request)),
                            attachment_store=_Lazy(lambda: get_attachment_store(request)))
    return ChatService(db, llm, get_retriever(request), references=refs, attachment_retriever=attach)


@router.post("/projects/{project_id}/conversations", response_model=ConversationOut,
             status_code=status.HTTP_201_CREATED)
def create_conversation(project_id: int, payload: ConversationCreate, db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
        c = Conversation(project_id=project_id, title=payload.title or "新对话")
        s.add(c); s.flush(); s.refresh(c)
        return ConversationOut.model_validate(c)


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
def list_conversations(project_id: int, db: Database = Depends(get_db)):
    with db.session() as s:
        rows = s.execute(
            select(Conversation).where(Conversation.project_id == project_id)
            .order_by(Conversation.updated_at.desc())
        ).scalars()
        return [ConversationOut.model_validate(c) for c in rows]


@router.delete("/conversations/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(cid: int, request: Request, db: Database = Depends(get_db)):
    with db.session() as s:
        c = s.get(Conversation, cid)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        s.delete(c)  # messages 经 cascade="all, delete-orphan" 一并删除
    # 清理会话级临时附件向量(派生索引,随会话销毁;只清不重建)。
    store = getattr(request.app.state, "attachment_store", None)
    if store is not None:
        try:
            store.delete({"conversation_id": cid})
        except Exception:  # noqa: BLE001 — 清理失败不应让删除请求 500;残留向量无害
            pass


@router.get("/conversations/{cid}/messages", response_model=list[MessageOut])
def list_messages(cid: int, db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        rows = s.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.id)
        ).scalars()
        return [MessageOut.model_validate(m) for m in rows]


@router.post("/conversations/{cid}/messages")
def send_message(cid: int, payload: MessageCreate, request: Request, db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    svc = _chat_service(request, db)

    def gen():
        for e in svc.stream_answer(cid, payload.content):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())


@router.post("/conversations/{cid}/messages/{mid}/edit")
def edit_message(cid: int, mid: int, payload: MessageCreate, request: Request,
                 db: Database = Depends(get_db)):
    # 编辑某条 user 消息 + 就地重生成:改其内容、删它之后的消息、对新内容重跑流水线(不新增 user)。
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    svc = _chat_service(request, db)
    # 目标须是本会话内的一条 user 消息;否则 404(不存在 / 是 assistant / 属别的会话)。
    with db.session() as s:
        m = s.get(Message, mid)
        if m is None or m.conversation_id != cid or m.role != "user":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "editable user message not found")

    def gen():
        for e in svc.stream_edit(cid, mid, payload.content):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())


@router.post("/conversations/{cid}/regenerate")
def regenerate_message(cid: int, request: Request, db: Database = Depends(get_db)):
    # 重生成最后一轮:删最后一条 user 消息之后的消息、对同一提问重跑流水线(不新增 user)。
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    svc = _chat_service(request, db)

    def gen():
        for e in svc.stream_regenerate(cid):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())

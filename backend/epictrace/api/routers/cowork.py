"""Cowork agent API(需求 9/10)。

session CRUD + 消息 SSE 流 + 工具清单。SSE 事件协议与对话路由一致
(status/thinking/tool_step/token/done/error),新增 session_state。
complete_fn 可经 app.state.cowork_complete 注入(测试用脚本化假件)。
"""

from __future__ import annotations

from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from epictrace.api.deps import (
    get_asr_provisioner,
    get_attachment_store,
    get_db,
    get_embedder,
    get_provisioner,
    get_reranker,
    get_retriever,
    get_vector_store,
)
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import AgentMessage, AgentSession
from epictrace.schemas import (
    ApprovalDecisionIn,
    ApprovalOut,
    CoworkAgentDefOut,
    CoworkMessageCreate,
    CoworkMessageOut,
    CoworkProgressOut,
    CoworkSessionCreate,
    CoworkSessionOut,
    CoworkSessionUpdate,
    CoworkSkillOut,
    CoworkToolOut,
)
from epictrace.services.settings import SettingsService
from epictrace.cowork.llm_client import make_complete_fn
from epictrace.cowork.service import CoworkService
from epictrace.cowork.sessions import SessionManager
from epictrace.cowork.tools.builtin_extract import build_extract_tools, build_transcribe_tool
from epictrace.cowork.tools.builtin_fs import build_fs_tools
from epictrace.cowork.tools.builtin_projects import build_project_tools
from epictrace.cowork.tools.builtin_retrieval import build_retrieval_tools
from epictrace.cowork.tools.builtin_shell import build_shell_tools
from epictrace.cowork.tools.registry import ToolRegistry

router = APIRouter(tags=["cowork"])  # /api 由 app 工厂统一挂载


def _sessions(request: Request) -> SessionManager:
    return request.app.state.cowork_sessions


def _build_registry(request: Request, db: Database) -> ToolRegistry:
    """每请求组装工具注册表(ToolDef 只是轻量对象;重资源经惰性 getter 在调用时解析)。"""
    registry = ToolRegistry()
    for tool in build_fs_tools(db):
        registry.register(tool)

    def _dense():
        from epictrace.retrieval.dense import dense_search

        return partial(dense_search, get_embedder(request), get_vector_store(request))

    for tool in build_retrieval_tools(
        db, get_retriever=lambda: get_retriever(request), get_dense=_dense
    ):
        registry.register(tool)

    # shell 工具(需求 5):沙箱配置调用时现读,设置改动即时生效
    config = getattr(request.app.state, "config", None) or AppConfig()
    for tool in build_shell_tools(lambda: SettingsService(config).get_sandbox_settings()):
        registry.register(tool)

    # 文档提取(需求 3):复用 media 管线的 MediaProcessor
    for tool in build_extract_tools(db):
        registry.register(tool)

    # 音频转写(需求 3):ASR 就绪门请求侧注入,未就绪返回引导文本
    registry.register(build_transcribe_tool(
        db, is_asr_ready=lambda: get_asr_provisioner(request).is_ready()))

    # 项目写操作(需求 3):索引经 app.state.index_lock/index_jobs,与 projects 路由同路径
    for tool in build_project_tools(
        db,
        get_embedder=lambda: get_embedder(request),
        get_vector_store=lambda: get_vector_store(request),
        index_jobs=request.app.state.index_jobs,
        index_lock=request.app.state.index_lock,
        get_provisioner=lambda: get_provisioner(request),
    ):
        registry.register(tool)
    return registry


def _complete_factory(request: Request):
    """complete_fn 工厂:model="" 用活动 Profile 默认模型;非空按指定模型(子 agent 可换便宜模型)。
    测试注入 app.state.cowork_complete 时,工厂恒返回假件(忽略模型)。"""
    config = getattr(request.app.state, "config", None) or AppConfig()
    settings = SettingsService(config)
    profile = settings.get_active_profile()
    injected = getattr(request.app.state, "cowork_complete", None)
    if injected is None and profile is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "对话模型未配置:请在设置里填写 OpenAI-Compatible 端点")
    timeout = float(settings.get_agent_settings()["turn_timeout_sec"])

    def factory(model: str = ""):
        if injected is not None:
            return injected
        return make_complete_fn(
            base_url=profile.get("base_url", ""),
            api_key=profile.get("api_key", ""),
            model=model or profile.get("model", ""),
            timeout=timeout,
        )

    return factory


def _cowork_service(request: Request, db: Database) -> CoworkService:
    config = getattr(request.app.state, "config", None) or AppConfig()
    factory = _complete_factory(request)

    def _attachment_retriever():
        from epictrace.retrieval.attachment import AttachmentRetriever

        return AttachmentRetriever(get_embedder(request), get_attachment_store(request),
                                   get_reranker(request))

    return CoworkService(
        db=db,
        sessions=_sessions(request),
        registry=_build_registry(request, db),
        complete_fn=factory(""),
        settings=SettingsService(config),
        config=config,
        approvals=request.app.state.cowork_approvals,
        dispatcher=getattr(request.app.state, "cowork_dispatcher", None),
        complete_factory=factory,
        skills=getattr(request.app.state, "cowork_skills", None),
        get_attachment_retriever=_attachment_retriever,
        cancels=getattr(request.app.state, "cowork_cancels", None),
        turn_locks=getattr(request.app.state, "cowork_turn_locks", None),
    )


@router.post("/cowork/sessions", response_model=CoworkSessionOut,
             status_code=status.HTTP_201_CREATED)
def create_session(payload: CoworkSessionCreate, request: Request,
                   db: Database = Depends(get_db)):
    # 项目存在性校验(codex review R3:否则产生项目树里不可见的孤儿会话)
    if payload.project_id is not None:
        from epictrace.models import Project

        with db.session() as s:
            if s.get(Project, payload.project_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    # 未指定权限模式时用设置里的默认(codex review P2)
    config = getattr(request.app.state, "config", None) or AppConfig()
    default_mode = SettingsService(config).get_permission_settings()["mode"]
    row = _sessions(request).create(
        type=payload.type,
        name=payload.name or "",
        permission_mode=payload.permission_mode or default_mode,
        project_id=payload.project_id,
    )
    return CoworkSessionOut.model_validate(row)


@router.get("/cowork/sessions", response_model=list[CoworkSessionOut])
def list_sessions(request: Request, project_id: int | None = None,
                  free_only: bool = False):
    """project_id=某项目 → 该项目绑定会话(「项目与对话」);free_only → 仅自由会话(Cowork tab)。"""
    return [CoworkSessionOut.model_validate(r)
            for r in _sessions(request).list(project_id=project_id, free_only=free_only)]


@router.get("/cowork/sessions/{sid}", response_model=CoworkSessionOut)
def get_session(sid: int, request: Request):
    row = _sessions(request).get(sid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return CoworkSessionOut.model_validate(row)


@router.delete("/cowork/sessions/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(sid: int, request: Request, db: Database = Depends(get_db)):
    # 先置取消(codex review R3:再做任何可能阻塞的清理,agent 线程要尽快收到停止)
    cancels = getattr(request.app.state, "cowork_cancels", None)
    if cancels is not None and sid in cancels:
        cancels[sid].set()
    # 会话级附件向量清理(对齐旧对话栈的删除语义):仅当该会话确实有引用时才构造
    # attachment store(重资源),无附件的删除不走重路径;清理失败不阻断删除。
    from epictrace.models import Reference

    with db.session() as s:
        has_refs = s.execute(
            select(Reference.id).where(Reference.session_id == sid).limit(1)
        ).first() is not None
    if has_refs:
        try:
            get_attachment_store(request).delete({"session_id": sid})
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("epictrace.cowork").warning(
                "删除 session %s 的附件向量失败(不阻断删除)", sid, exc_info=True)
    if not _sessions(request).delete(sid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")


@router.patch("/cowork/sessions/{sid}", response_model=CoworkSessionOut)
def update_session(sid: int, payload: CoworkSessionUpdate, request: Request):
    """部分更新会话:改名 / 切换权限模式(会话级,需求 7)。"""
    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "name 不能为空")
        _sessions(request).rename(sid, name)
    if payload.permission_mode is not None:
        _sessions(request).set_permission_mode(sid, payload.permission_mode)
    return CoworkSessionOut.model_validate(_sessions(request).get(sid))


@router.get("/cowork/sessions/{sid}/messages", response_model=list[CoworkMessageOut])
def list_messages(sid: int, request: Request, db: Database = Depends(get_db)):
    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    with db.session() as s:
        rows = s.execute(
            select(AgentMessage).where(AgentMessage.session_id == sid).order_by(AgentMessage.id)
        ).scalars()
        return [CoworkMessageOut.model_validate(m) for m in rows]


@router.post("/cowork/sessions/{sid}/stop")
def stop_session(sid: int, request: Request):
    """停止当前正在运行的 agent 循环(循环/审批挂起都会收到取消;幂等)。"""
    import threading

    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    cancels = getattr(request.app.state, "cowork_cancels", None)
    if cancels is not None:
        cancels.setdefault(sid, threading.Event()).set()
    return {"ok": True}


@router.post("/cowork/sessions/{sid}/messages")
def send_message(sid: int, payload: CoworkMessageCreate, request: Request,
                 db: Database = Depends(get_db)):
    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    svc = _cowork_service(request, db)

    def gen():
        for e in svc.stream_message(sid, payload.content):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())


@router.post("/cowork/sessions/{sid}/regenerate")
def regenerate(sid: int, request: Request, db: Database = Depends(get_db)):
    """重生成最后一轮(删最后 user 消息之后的全部消息,重跑同一轮)。"""
    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    svc = _cowork_service(request, db)

    def gen():
        for e in svc.stream_regenerate(sid):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())


@router.post("/cowork/sessions/{sid}/messages/{mid}/edit")
def edit_message(sid: int, mid: int, payload: CoworkMessageCreate, request: Request,
                 db: Database = Depends(get_db)):
    """编辑某条 user 消息并就地重生成(语义同旧对话栈的 edit)。"""
    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    svc = _cowork_service(request, db)

    def gen():
        for e in svc.stream_edit(sid, mid, payload.content):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())


@router.get("/cowork/tools", response_model=list[CoworkToolOut])
def list_tools(request: Request, db: Database = Depends(get_db)):
    """已注册工具清单(验收 3:注册一次,前后端自动可见)。"""
    return [
        CoworkToolOut(
            name=t.name,
            description=t.description,
            permission=t.permission,
            sandbox=t.sandbox,
            always_allow_suppressed=t.always_allow_suppressed,
        )
        for t in _build_registry(request, db).list()
    ]


# ---- 子 agent(需求 4/10):可用定义 + 派发进度 ----

@router.get("/cowork/agents", response_model=list[CoworkAgentDefOut])
def list_agent_defs(request: Request):
    d = getattr(request.app.state, "cowork_dispatcher", None)
    defs = d.agent_defs if d is not None else {}
    return [
        CoworkAgentDefOut(
            name=x.name, description=x.description, tools=list(x.tools),
            disallowed_tools=list(x.disallowed_tools), model=x.model,
            permission_mode=x.permission_mode, max_turns=x.max_turns,
        )
        for x in defs.values()
    ]


@router.get("/cowork/skills", response_model=list[CoworkSkillOut])
def list_skills(request: Request):
    """已加载 skill 清单(需求 6;前端设置页/调试可见)。"""
    skills = getattr(request.app.state, "cowork_skills", {}) or {}
    return [CoworkSkillOut(name=s.name, description=s.description, source=s.source)
            for s in skills.values()]


@router.get("/cowork/sessions/{sid}/progress", response_model=CoworkProgressOut)
def session_progress(sid: int, request: Request):
    """主 agent 已派发子任务的进度快照(总数/完成/进行中)。"""
    if _sessions(request).get(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    d = getattr(request.app.state, "cowork_dispatcher", None)
    if d is None:
        return CoworkProgressOut(total=0, done=0, running=0)
    return CoworkProgressOut(**d.children_progress(sid))


# ---- 权限审批(需求 7):挂起中的请求列表 + 用户决策回传 ----

@router.get("/cowork/approvals", response_model=list[ApprovalOut])
def list_approvals(request: Request):
    """当前待审批请求(前端刷新/切换后恢复弹窗用)。"""
    return request.app.state.cowork_approvals.pending()


@router.post("/cowork/approvals/{approval_id}")
def decide_approval(approval_id: str, payload: ApprovalDecisionIn, request: Request):
    try:
        ok = request.app.state.cowork_approvals.decide(approval_id, payload.decision)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found or already resolved")
    return {"ok": True}

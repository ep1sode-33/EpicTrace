"""Cowork 编排服务:把 session、prompt 组装、agent 循环、消息持久化、SSE 事件串起来。

事件协议沿用现有对话的 SSE 约定(status/thinking/tool_step/token/done/error),
新增 session_state / approval_request / approval_resolved(权限审批,需求 7)。
循环在后台线程执行,事件经 queue 流向 SSE 生成器——与 services/chat.py 的 agent 路同一模式。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator

from sqlalchemy import select

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import AgentMessage, AgentSession
from epictrace.services.settings import SettingsService
from epictrace.cowork.approvals import ApprovalManager
from epictrace.cowork.dispatch import CompleteFactory, Dispatcher
from epictrace.cowork.llm_client import CompleteFn
from epictrace.cowork.loop import AgentLoop, AgentLoopError
from epictrace.cowork.permissions import (
    ALLOW,
    APPROVAL_TIMEOUT_SEC,
    DENY,
    PermissionEngine,
)
from epictrace.cowork.prompts.assemble import assemble_system_prompt
from epictrace.cowork.prompts.sections import SectionContext
from epictrace.cowork.sessions import SessionManager
from epictrace.cowork.skills import SkillDef
from epictrace.cowork.tools.builtin_ask import build_ask_user_tool
from epictrace.cowork.tools.builtin_attachments import attachment_manifest, build_attachment_tools
from epictrace.cowork.tools.builtin_dispatch import build_dispatch_tools
from epictrace.cowork.tools.registry import ToolRegistry

log = logging.getLogger("epictrace.cowork")

_TITLE_MAX = 30
_TITLE_SYS = (
    "你是对话标题生成器。为下面这段问答起一个不超过 12 字的简短中文标题,"
    "概括它们在聊什么。只输出标题本身,不要回答问题、不要加引号、不要解释。"
)


class CoworkService:
    def __init__(
        self,
        db: Database,
        sessions: SessionManager,
        registry: ToolRegistry,
        complete_fn: CompleteFn,
        settings: SettingsService,
        config: AppConfig,
        approvals: ApprovalManager,
        dispatcher: Dispatcher | None = None,
        complete_factory: CompleteFactory | None = None,
        skills: dict[str, SkillDef] | None = None,
        get_attachment_retriever: Callable[[], object | None] | None = None,
        cancels: dict[int, threading.Event] | None = None,
        turn_locks: dict[int, threading.Lock] | None = None,
    ) -> None:
        self._db = db
        self._sessions = sessions
        self._registry = registry
        self._complete = complete_fn
        self._settings = settings
        self._config = config
        self._approvals = approvals
        self._dispatcher = dispatcher
        self._complete_factory = complete_factory
        self._skills = skills or {}
        self._get_attachment_retriever = get_attachment_retriever
        # 每 session 的取消事件(app 级共享,stop 端点置位;turn 开始清零)
        self._cancels = cancels if cancels is not None else {}
        # 每 session 的 turn 串行锁(app 级共享;并发 turn 拒绝,见 _stream_turn)
        self._turn_locks = turn_locks if turn_locks is not None else {}

    # ---- 消息持久化与回放 ----
    def _append_message(self, session_id: int, msg: dict) -> None:
        tool_calls = msg.get("tool_calls")
        name = None
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            name = msg.get("name")
        with self._db.session() as s:
            # 会话可能已被删除(删除时先置取消,但线程可能在此之后才落库)——跳过防孤儿行
            if s.get(AgentSession, session_id) is None:
                log.info("session %s 已删除,丢弃消息落库(%s)", session_id, msg.get("role"))
                return
            s.add(AgentMessage(
                session_id=session_id,
                role=msg["role"],
                content=msg.get("content") or "",
                name=name,
                tool_call_id=msg.get("tool_call_id"),
                tool_calls_json=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                citations_json=(json.dumps(msg["citations"], ensure_ascii=False)
                                if msg.get("citations") else None),
            ))

    def _load_history(self, session_id: int) -> list[dict]:
        with self._db.session() as s:
            rows = s.execute(
                select(AgentMessage).where(AgentMessage.session_id == session_id)
                .order_by(AgentMessage.id)
            ).scalars()
            msgs = [_row_to_dict(r) for r in rows]
        return _sanitize_history(msgs)

    # ---- 主流程 ----
    def stream_message(self, session_id: int, content: str) -> Iterator[dict]:
        session = self._sessions.get(session_id)
        if session is None:
            yield {"event": "error", "data": "session not found"}
            return
        # 消息变更必须在会话临界区内(codex review R3:被拒的并发请求不能留下消息)
        lock = self._turn_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            yield {"event": "error", "data": "这个会话正在运行中,请先停止或等待当前轮结束。"}
            return
        try:
            self._append_message(session_id, {"role": "user", "content": content})
            yield from self._stream_turn_locked(session, first_turn=self._is_first_turn(session_id))
        finally:
            lock.release()

    def stream_regenerate(self, session_id: int) -> Iterator[dict]:
        """重生成最后一轮:找最后一条 user 消息,删它之后的所有消息,以它之前为历史重跑。"""
        session = self._sessions.get(session_id)
        if session is None:
            yield {"event": "error", "data": "session not found"}
            return
        lock = self._turn_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            yield {"event": "error", "data": "这个会话正在运行中,请先停止或等待当前轮结束。"}
            return
        try:
            rows = self._message_rows(session_id)
            last_user = next((r for r in reversed(rows) if r.role == "user"), None)
            if last_user is None:
                yield {"event": "error", "data": "没有可重新生成的提问"}
                return
            first = not any(r.role == "user" for r in rows if r.id < last_user.id)
            self._delete_messages_after(session_id, last_user.id)
            yield from self._stream_turn_locked(session, first_turn=first)
        finally:
            lock.release()

    def stream_edit(self, session_id: int, message_id: int, content: str) -> Iterator[dict]:
        """编辑某条 user 消息并重生成:就地改写,删其后消息,以之前为历史重跑。"""
        session = self._sessions.get(session_id)
        if session is None:
            yield {"event": "error", "data": "session not found"}
            return
        lock = self._turn_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            yield {"event": "error", "data": "这个会话正在运行中,请先停止或等待当前轮结束。"}
            return
        try:
            rows = self._message_rows(session_id)
            target = next((r for r in rows if r.id == message_id), None)
            if target is None or target.role != "user":
                yield {"event": "error", "data": "只能编辑用户消息"}
                return
            first = not any(r.role == "user" for r in rows if r.id < message_id)
            with self._db.session() as s:
                row = s.get(AgentMessage, message_id)
                row.content = content
            self._delete_messages_after(session_id, message_id)
            yield from self._stream_turn_locked(session, first_turn=first)
        finally:
            lock.release()

    def _message_rows(self, session_id: int) -> list[AgentMessage]:
        with self._db.session() as s:
            return list(s.execute(
                select(AgentMessage).where(AgentMessage.session_id == session_id)
                .order_by(AgentMessage.id)
            ).scalars())

    def _delete_messages_after(self, session_id: int, keep_id: int) -> None:
        """删 keep_id 之后(不含)的所有消息(与旧 ChatService 的 delete-after 语义一致)。"""
        from sqlalchemy import delete

        with self._db.session() as s:
            s.execute(delete(AgentMessage).where(
                AgentMessage.session_id == session_id, AgentMessage.id > keep_id))

    def _is_first_turn(self, session_id: int) -> bool:
        rows = self._message_rows(session_id)
        return sum(1 for r in rows if r.role == "user") == 1

    def _maybe_make_title(self, session, question: str, answer: str) -> str | None:
        """首轮且未命名时自动生成会话标题(复用 complete_fn;失败回退问题前 20 字)。"""
        if session.name.strip():
            return None
        try:
            resp = self._complete(
                [{"role": "user", "content": _TITLE_SYS + f"\n\n问:{question}\n答:{answer[:500]}"}],
                [])
            title = (resp.content or "").strip().strip('"\'')[:_TITLE_MAX]
        except Exception:  # noqa: BLE001 — 标题失败不致命,回退截断
            title = ""
        return title or question.strip()[:20]

    def _stream_turn_locked(self, session, *, first_turn: bool) -> Iterator[dict]:
        """会话临界区内的单轮执行(锁由 stream_message/edit/regenerate 持有)。"""
        session_id = session.id
        # 本轮的取消事件:turn 开始清零(上一轮残留的 stop 不影响新轮);stop 端点置位
        cancel = self._cancels.setdefault(session_id, threading.Event())
        cancel.clear()
        # SSE 事件队列提前建好:工具注册(ask_user)与循环线程共享它
        events: queue.Queue = queue.Queue()

        agent_cfg = self._settings.get_agent_settings()
        engine = PermissionEngine(self._settings, self._config)
        # 主 agent 且有派发能力时:注册 start_task/wait_task 并渲染 dispatch 节(需求 4)
        can_dispatch = (
            session.type == "agent"
            and self._dispatcher is not None
            and self._complete_factory is not None
            and bool(self._dispatcher.agent_defs)
        )
        if can_dispatch:
            for t in build_dispatch_tools(
                self._dispatcher,
                parent_session_id=session_id,
                registry=self._registry,
                complete_factory=self._complete_factory,
            ):
                self._registry.register(t)
        if session.type == "agent":
            # ask_user(需求 3):主动向用户提问;复用审批通道(permission=allow 不叠加闸门)。
            # 提问经 SSE 发到活跃前端(codex review R2:否则弹窗不出现,挂到超时)。
            from epictrace.cowork.tools.builtin_ask import approval_event, resolved_event

            self._registry.register(build_ask_user_tool(
                self._approvals, session_id=session_id,
                on_request=lambda req: events.put(approval_event(req)),
                on_resolved=lambda aid, d: events.put(resolved_event(aid, d)),
            ))
            # 附件工具(旧栈移植):按本会话活跃引用过滤;无附件时 handler 返回友好提示
            if self._get_attachment_retriever is not None:
                for t in build_attachment_tools(
                    self._db, session_id=session_id,
                    get_attachment_retriever=self._get_attachment_retriever,
                ):
                    self._registry.register(t)
        # dispatch_child 续聊:按 session.config 里的子 agent 定义恢复白名单/技能
        # (codex review P1:否则续聊变成全工具 + skip_all 的权限绕过)
        child_def = None
        if session.type == "dispatch_child" and self._dispatcher is not None:
            child_def = self._dispatcher.agent_defs.get((session.config or {}).get("agent", ""))

        all_names = [t.name for t in self._registry.list()]
        if session.type == "chat":
            tools: list = []
            allowed: list[str] | None = []
        elif child_def is not None:
            allowed = child_def.allowed_tool_names(all_names)
            tools = self._registry.list(allowed)
        else:
            tools = self._registry.list()
            allowed = None
        child_skills = (
            [self._skills[n].as_prompt_dict() for n in child_def.skills if n in self._skills]
            if child_def is not None else None
        )
        # 附件清单注入 prompt(空=无附件不渲染 attachments 节)
        manifest = "" if session.type != "agent" else attachment_manifest(self._db, session_id)
        # 项目绑定会话:把当前项目注入 prompt(codex review P1:模型不用猜 project_id)
        project_title = ""
        if session.project_id is not None:
            from epictrace.models import Project

            with self._db.session() as s:
                proj = s.get(Project, session.project_id)
            project_title = proj.title if proj is not None else ""
        ctx = SectionContext(
            session_type=session.type,
            tools=tools,
            # 主 agent 注入全部已加载 skill(需求 6);子 agent 按定义白名单;chat 不注入
            skills=(child_skills if child_skills is not None
                    else [] if session.type == "chat"
                    else [s.as_prompt_dict() for s in self._skills.values()]),
            attachment_manifest=manifest,
            project_id=session.project_id,
            project_title=project_title,
            agent_name=(session.config or {}).get("agent", "") if child_def is not None else "",
            user_instructions=agent_cfg["user_instructions"],
            admin_instructions=engine.admin_instructions,
            permission_mode=session.permission_mode,
            supports_dispatch=can_dispatch,
            dispatch_agents=(
                [{"name": d.name, "description": d.description}
                 for d in self._dispatcher.agent_defs.values()]
                if can_dispatch else []
            ),
            data_dir=str(self._config.data_dir),
        )
        system_prompt = assemble_system_prompt(ctx)

        # user 消息由调用方(stream_message / stream_edit / stream_regenerate)落库;
        # 此处只读历史(最后一条即本轮的 user 消息)。
        history = self._load_history(session_id)

        yield {"event": "status", "data": "思考中"}
        yield _state_event(self._sessions, session_id, "thinking")

        # turn 级引用池(需求:引用链下沉):检索工具执行时把 RetrievedChunk 追加进来,
        # 编号全局递增(见 builtin_retrieval._format_chunks);循环结束后 [n] → citations。
        chunk_pool: list = []
        # ask-session 记忆:本会话内用户已批准「都允许」的工具名(持久在 session.config)
        session_approved: set[str] = set((session.config or {}).get("approved_tools", []))

        def on_event(e: dict) -> None:
            # 工具开始执行 → worker 侧同步状态(此前在 generator,断连会丢 DB 状态)
            if e.get("event") == "tool_step":
                try:
                    step = json.loads(e.get("data") or "{}")
                except json.JSONDecodeError:
                    step = {}
                if step.get("status") == "started":
                    self._sessions.set_state(session_id, "executing")
                    events.put({"event": "session_state",
                                "data": json.dumps({"status": "executing"})})
            events.put(e)

        def before_tool(tc: dict) -> str | None:
            """权限闸门(在循环线程内运行):allow → None 放行;deny/拒绝 → 错误文本作工具结果。"""
            tool = self._registry.get(tc["name"])
            if tool is None:
                return None  # 未知工具交给 registry 的错误回传
            d = engine.decide(tool, session_mode=session.permission_mode,
                              session_approved=session_approved)
            if d.verdict == ALLOW:
                return None
            if d.verdict == DENY:
                log.info("tool %s blocked: %s", tc["name"], d.reason)
                return f"Error: 该工具被策略禁止({d.reason}),请不要重试。"
            # ASK:挂起循环,推 approval_request,等用户决策
            offer_session = d.ask_session and not PermissionEngine.is_suppressed(tc["name"])
            req = self._approvals.request(
                session_id=session_id, tool=tc["name"], args=tc["arguments"],
                allow_session_option=offer_session)
            events.put(_state_event(self._sessions, session_id, "waiting_approval"))
            events.put({"event": "approval_request",
                        "data": json.dumps(req, ensure_ascii=False)})
            decision = self._approvals.wait(req["approval_id"], APPROVAL_TIMEOUT_SEC,
                                            cancel=cancel)
            events.put({"event": "approval_resolved", "data": json.dumps(
                {"approval_id": req["approval_id"], "decision": decision or "deny"})})
            events.put(_state_event(self._sessions, session_id, "executing"))
            if decision == "once":
                return None
            if decision == "session" and offer_session:
                session_approved.add(tc["name"])
                self._sessions.add_approved_tool(session_id, tc["name"])
                return None
            if decision == "session":  # 被抑制工具没有「本次 session 都允许」,按仅此一次
                return None
            return "Error: 用户拒绝了这次工具调用。请换一种不需要该工具的方式,或向用户解释后结束。"

        def work() -> None:
            """循环线程:跑 agent 循环并完成**全部终局处理**(落库/状态/标题)。

            终局处理必须在 worker 而不能在 SSE generator:客户端断连(切后台/关页面)
            后没人再迭代 generator,generator 里的落库与状态复位会永远跳过,
            会话就此卡死 thinking(实锤于切后台即挂的 bug)。"""
            try:
                loop = AgentLoop(
                    complete_fn=self._complete,
                    registry=self._registry,
                    system_prompt=system_prompt,
                    max_turns=int(agent_cfg["max_turns"]),
                    turn_timeout=float(agent_cfg["turn_timeout_sec"]),
                    # chat 无工具(空白名单);dispatch_child 续聊用子 agent 白名单(codex P1)
                    allowed_tools=allowed,
                    before_tool=before_tool,
                    on_event=on_event,
                    exec_ctx={"chunk_pool": chunk_pool},
                    should_stop=cancel.is_set,
                )
                out = loop.run(history)
                _finalize_success(out)
            except AgentLoopError as e:
                _finalize_error(str(e), e.partial)
            except Exception as e:  # noqa: BLE001 — 任何意外都要流回前端,不能静默挂起
                log.exception("cowork loop crashed")
                _finalize_error(f"{type(e).__name__}: {e}", [])
            finally:
                events.put(None)  # 哨兵

        def _finalize_success(out) -> None:
            from epictrace.cowork.citations import build_citations

            citations = build_citations(out.text, chunk_pool) if chunk_pool else []
            for i, msg in enumerate(out.new_messages):
                if citations and i == len(out.new_messages) - 1 and msg.get("role") == "assistant":
                    msg = {**msg, "citations": citations}
                self._append_message(session_id, _with_tool_names(msg, out.new_messages))
            events.put({"event": "token", "data": out.text})
            if citations:
                events.put({"event": "citations", "data": json.dumps(citations, ensure_ascii=False)})
            # 首轮自动标题(与旧 ChatService 同语义:仅首轮 + 未命名;失败回退问题前 20 字)
            if first_turn:
                question = next((m["content"] for m in reversed(history)
                                 if m.get("role") == "user"), "")
                title = self._maybe_make_title(session, question, out.text)
                if title:
                    self._sessions.rename(session_id, title)
                    events.put({"event": "session_renamed",
                                "data": json.dumps({"name": title}, ensure_ascii=False)})
            self._sessions.set_state(session_id, "idle")
            events.put({"event": "session_state", "data": json.dumps({"status": "idle"})})
            events.put({"event": "done", "data": ""})

        def _finalize_error(err: str, partial: list | None) -> None:
            # 失败前已执行的工具步骤落库(codex review P1):副作用有据可查,
            # regenerate 不会盲 repeat;悬空的 tool_call 由 _sanitize_history 在下次读取时补齐。
            for msg in partial or []:
                self._append_message(session_id, _with_tool_names(msg, partial))
            self._sessions.set_state(session_id, "error")
            events.put({"event": "session_state", "data": json.dumps({"status": "error"})})
            events.put({"event": "error", "data": err})
            self._sessions.set_state(session_id, "idle")

        t = threading.Thread(target=work, daemon=True)
        t.start()

        # generator 只做事件转发(不碰 DB/状态)——断连不影响 worker 的终局处理。
        while True:
            e = events.get()
            if e is None:
                break
            yield e


def _state_event(sessions: SessionManager, session_id: int, state: str) -> dict:
    sessions.set_state(session_id, state)
    return {"event": "session_state", "data": json.dumps({"status": state})}


def _row_to_dict(r: AgentMessage) -> dict:
    msg: dict = {"role": r.role, "content": r.content}
    if r.tool_calls_json:
        msg["tool_calls"] = json.loads(r.tool_calls_json)
        if not msg["content"]:
            msg["content"] = None
    if r.role == "tool":
        msg["tool_call_id"] = r.tool_call_id
        if r.name:
            msg["name"] = r.name
    return msg


def _sanitize_history(msgs: list[dict]) -> list[dict]:
    """补齐悬空的 tool_calls:上次运行若中断在工具执行前,OpenAI 会拒绝缺少
    对应 tool 结果的历史——为每个缺失结果的 tool_call 注入 (interrupted) 占位。"""
    answered = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    out: list[dict] = []
    for m in msgs:
        out.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if tc.get("id") not in answered:
                    out.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "(interrupted: tool result missing)",
                    })
    return out


def _with_tool_names(msg: dict, all_msgs: list[dict]) -> dict:
    """tool 消息补上工具名(从 assistant 的 tool_calls 反查),便于持久化与展示。"""
    if msg.get("role") != "tool" or not msg.get("tool_call_id"):
        return msg
    for m in all_msgs:
        for tc in m.get("tool_calls") or []:
            if tc.get("id") == msg["tool_call_id"]:
                return {**msg, "name": tc.get("function", {}).get("name")}
    return msg

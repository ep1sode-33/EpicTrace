"""子 agent 派发(需求 4)。

主 agent 经 `start_task` 工具创建 dispatch_child session,在后台线程跑受限 agent 循环
(独立工具白名单/权限模式/模型/轮数上限);多个子 agent 并行。`wait_task` 阻塞收集结果,
回传给主 agent 汇总。子 agent 的消息与日志隔离在自己的 session,不混入主 agent(验收 4)。

Dispatcher 是 app 级长生命周期对象(任务表跨请求存活);每请求变化的依赖
(registry / complete_fn 工厂)在 start 时传入。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import AgentMessage, AgentSession
from epictrace.services.settings import SettingsService
from epictrace.cowork.agents import AgentDef
from epictrace.cowork.approvals import ApprovalManager
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
from epictrace.cowork.tools.registry import ToolRegistry

log = logging.getLogger("epictrace.cowork")

# wait_task 默认/上限等待时长(秒)
DEFAULT_WAIT_TIMEOUT = 600.0
MAX_WAIT_TIMEOUT = 3600.0

# model 为空时继承主 agent 的 complete_fn;非空时由工厂按模型名另建通道
CompleteFactory = Callable[[str], CompleteFn]


@dataclass
class _Task:
    child_session_id: int
    agent_name: str
    task: str
    done: threading.Event = field(default_factory=threading.Event)
    result: str = ""
    error: str = ""


class Dispatcher:
    def __init__(
        self,
        db: Database,
        sessions: SessionManager,
        agent_defs: dict[str, AgentDef],
        approvals: ApprovalManager,
        config: AppConfig,
        skills: dict[str, SkillDef] | None = None,
        cancels: dict[int, threading.Event] | None = None,
    ) -> None:
        self._db = db
        self._sessions = sessions
        self.agent_defs = agent_defs
        self._approvals = approvals
        self._config = config
        self._skills = skills or {}
        # 共享取消表(codex review R3:stop/delete 端点置位后,子 agent 循环也要收到)
        self._cancels = cancels if cancels is not None else {}
        self._lock = threading.Lock()
        self._tasks: dict[int, _Task] = {}  # child_session_id → task

    # ---- start_task ----
    def start(
        self,
        *,
        parent_session_id: int,
        agent_name: str,
        task: str,
        context: str = "",
        registry: ToolRegistry,
        complete_factory: CompleteFactory,
    ) -> str:
        """创建子 agent 并后台开跑;立即返回任务描述(含任务 ID)给主 agent。"""
        agent_def = self.agent_defs.get(agent_name)
        if agent_def is None:
            known = "、".join(sorted(self.agent_defs)) or "(无)"
            return f"Error: 未知子 agent「{agent_name}」。可用:{known}"
        if not task.strip():
            return "Error: task 不能为空(子 agent 看不到主对话,任务描述必须自包含)"

        parent = self._sessions.get(parent_session_id)
        child = self._sessions.create(
            type="dispatch_child",
            name=f"{agent_name}: {task.strip()[:40]}",
            parent_id=parent_session_id,
            # 项目随父会话:子任务在「项目与对话」的对应项目树下可见
            project_id=parent.project_id if parent is not None else None,
            permission_mode=agent_def.permission_mode,
            config={"agent": agent_name, "task": task},
        )
        t = _Task(child_session_id=child.id, agent_name=agent_name, task=task)
        with self._lock:
            self._tasks[child.id] = t

        th = threading.Thread(
            target=self._run_child,
            args=(t, agent_def, context, registry, complete_factory),
            daemon=True,
            name=f"cowork-child-{child.id}",
        )
        th.start()
        log.info("dispatched child session %s (agent=%s, parent=%s)",
                 child.id, agent_name, parent_session_id)
        return (
            f"已派发子 agent「{agent_name}」(任务 ID: {child.id})。"
            "它正在后台并行执行;派发完所有子任务后,用 wait_task 收集结果。"
        )

    # ---- wait_task ----
    def wait(
        self,
        *,
        parent_session_id: int,
        task_ids: list[int] | None = None,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
    ) -> str:
        """阻塞等待子任务完成并汇总结果。task_ids 为空 = 等本 session 派发的全部任务。"""
        timeout = max(1.0, min(float(timeout), MAX_WAIT_TIMEOUT))
        with self._lock:
            tasks = [t for t in self._tasks.values()
                     if self._parent_of(t) == parent_session_id
                     and (task_ids is None or t.child_session_id in task_ids)]
        if not tasks:
            return "没有进行中的子任务(或指定的任务 ID 不属于当前 session)。"

        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.start()
        parts: list[str] = []
        try:
            for t in tasks:
                while not t.done.wait(0.2):
                    if deadline.is_set():
                        parts.append(
                            f"## 任务 {t.child_session_id}({t.agent_name})⏳ 等待超时,仍在后台执行;"
                            f"稍后可再次 wait_task(task_ids=[{t.child_session_id}]) 收取结果。")
                        break
                else:
                    parts.append(self._render_result(t))
        finally:
            timer.cancel()
        return "\n\n".join(parts)

    def _render_result(self, t: _Task) -> str:
        head = f"## 任务 {t.child_session_id}({t.agent_name})"
        if t.error:
            return f"{head} ❌ 失败:{t.error}"
        return f"{head} ✅ 完成:\n{t.result}"

    def _parent_of(self, t: _Task) -> int | None:
        row = self._sessions.get(t.child_session_id)
        return row.parent_id if row is not None else None

    # ---- 子 agent 循环(后台线程) ----
    def _run_child(
        self,
        t: _Task,
        agent_def: AgentDef,
        context: str,
        registry: ToolRegistry,
        complete_factory: CompleteFactory,
    ) -> None:
        sid = t.child_session_id
        cancel = self._cancels.setdefault(sid, threading.Event())
        try:
            settings = SettingsService(self._config)
            agent_cfg = settings.get_agent_settings()
            engine = PermissionEngine(settings, self._config)

            all_names = [x.name for x in registry.list()]
            allowed = agent_def.allowed_tool_names(all_names)
            tools = registry.list(allowed)
            # 子 agent 按定义白名单注入 skill(需求 6;主 agent 是全量,见 service.py)
            child_skills = [self._skills[n].as_prompt_dict()
                            for n in agent_def.skills if n in self._skills]

            ctx = SectionContext(
                session_type="dispatch_child",
                task=t.task,
                tools=tools,
                skills=child_skills,
                agent_name=agent_def.name,
                permission_mode=agent_def.permission_mode,
                admin_instructions=engine.admin_instructions,
                data_dir=str(self._config.data_dir),
            )
            system_prompt = assemble_system_prompt(ctx)

            user_text = t.task if not context.strip() else f"{t.task}\n\n背景信息:\n{context.strip()}"
            self._append_message(sid, {"role": "user", "content": user_text})

            # 子 agent 独立的引用池(与主 agent 隔离,验收 4)
            chunk_pool: list = []

            def before_tool(tc: dict) -> str | None:
                tool = registry.get(tc["name"])
                if tool is None:
                    return None
                d = engine.decide(tool, session_mode=agent_def.permission_mode,
                                  session_approved=set())
                if d.verdict == ALLOW:
                    return None
                if d.verdict == DENY:
                    return f"Error: 该工具被策略禁止({d.reason}),请不要重试。"
                # 子 agent 也可能需用户确认:挂起,前端经 GET /cowork/approvals 可见
                req = self._approvals.request(
                    session_id=sid, tool=tc["name"], args=tc["arguments"],
                    allow_session_option=False)
                self._sessions.set_state(sid, "waiting_approval")
                decision = self._approvals.wait(req["approval_id"], APPROVAL_TIMEOUT_SEC,
                                                cancel=cancel)
                self._sessions.set_state(sid, "executing")
                if decision in ("once", "session"):
                    return None
                return "Error: 用户拒绝了这次工具调用。请换一种不需要该工具的方式,或汇报无法完成。"

            self._sessions.set_state(sid, "thinking")
            loop = AgentLoop(
                complete_fn=complete_factory(agent_def.model),
                registry=registry,
                system_prompt=system_prompt,
                max_turns=agent_def.max_turns,
                turn_timeout=float(agent_cfg["turn_timeout_sec"]),
                allowed_tools=allowed,
                before_tool=before_tool,
                on_event=lambda e: log.debug("child %s event: %s", sid, e.get("event")),
                exec_ctx={"chunk_pool": chunk_pool},
                should_stop=cancel.is_set,
            )
            out = loop.run([{"role": "user", "content": user_text}])
            # 子 agent 的引用链落自己的 session(与主 agent 隔离,验收 4)
            from epictrace.cowork.citations import build_citations

            citations = build_citations(out.text, chunk_pool) if chunk_pool else []
            for i, msg in enumerate(out.new_messages):
                if citations and i == len(out.new_messages) - 1 and msg.get("role") == "assistant":
                    msg = {**msg, "citations": citations}
                self._append_message(sid, _with_tool_names(msg, out.new_messages))
            t.result = out.text
            self._sessions.set_state(sid, "done")
            log.info("child session %s done (%s turns)", sid, out.turns)
        except AgentLoopError as e:
            log.info("child session %s failed: %s", sid, e)
            # 子 agent 同样保留失败前已执行的步骤(codex review P1)
            for msg in e.partial:
                self._append_message(sid, _with_tool_names(msg, e.partial))
            t.error = str(e)
            self._sessions.set_state(sid, "error")
        except Exception as e:  # noqa: BLE001 — 子任务失败回传给主 agent,不影响其它子任务
            log.exception("child session %s failed", sid)
            t.error = f"{type(e).__name__}: {e}"
            self._sessions.set_state(sid, "error")
        finally:
            t.done.set()

    # ---- 消息持久化(与 service 同规则,按 session 隔离) ----
    def _append_message(self, session_id: int, msg: dict) -> None:
        tool_calls = msg.get("tool_calls")
        name = msg.get("name") if msg.get("role") == "tool" else None
        with self._db.session() as s:
            # 会话已删(删除先置取消,但落库可能更晚)——跳过防孤儿行
            if s.get(AgentSession, session_id) is None:
                log.info("session %s 已删除,丢弃子 agent 消息落库", session_id)
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

    # ---- 前端可见性(需求 10):某 session 的子任务进度 ----
    def children_progress(self, parent_session_id: int) -> dict:
        """{total, done, running} — 主 agent 已派发子任务的进度快照。"""
        children = self._sessions.children_of(parent_session_id)
        with self._lock:
            live = dict(self._tasks)
        total = len(children)
        done = 0
        for c in children:
            t = live.get(c.id)
            if t is not None:
                if t.done.is_set():
                    done += 1
            elif c.status in ("done", "error", "idle"):
                done += 1
        return {"total": total, "done": done, "running": total - done}


def _with_tool_names(msg: dict, all_msgs: list[dict]) -> dict:
    """tool 消息补上工具名(从 assistant 的 tool_calls 反查),便于持久化与展示。"""
    if msg.get("role") != "tool" or not msg.get("tool_call_id"):
        return msg
    for m in all_msgs:
        for tc in m.get("tool_calls") or []:
            if tc.get("id") == msg["tool_call_id"]:
                return {**msg, "name": tc.get("function", {}).get("name")}
    return msg

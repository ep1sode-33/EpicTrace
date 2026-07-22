"""Agent session 生命周期(需求 9)。

五种 session 类型:agent(主对话)/ dispatch_child(子 agent)/ scheduled(定时)/
chat(纯聊天,无工具)/ radar(文件感知)。DB 落持久事实(AgentSession 表),
运行时瞬时状态经内存态跟踪;状态迁移同时回写 DB,重启后列表仍可见。
"""

from __future__ import annotations

import threading

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import AgentSession

SESSION_TYPES = ("agent", "dispatch_child", "scheduled", "chat", "radar")
SESSION_STATES = ("idle", "thinking", "executing", "waiting_approval", "done", "error")
PERMISSION_MODES = ("ask", "follow_a_plan", "skip_all")


class SessionManager:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()

    def create(
        self,
        *,
        type: str = "agent",
        name: str = "",
        parent_id: int | None = None,
        project_id: int | None = None,
        permission_mode: str = "ask",
        config: dict | None = None,
    ) -> AgentSession:
        if type not in SESSION_TYPES:
            raise ValueError(f"invalid session type: {type}")
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"invalid permission_mode: {permission_mode}")
        with self._db.session() as s:
            row = AgentSession(
                type=type, name=name, parent_id=parent_id, project_id=project_id,
                permission_mode=permission_mode, config=config or {}, status="idle",
            )
            s.add(row)
            s.flush()
            s.refresh(row)
            return row

    def get(self, session_id: int) -> AgentSession | None:
        with self._db.session() as s:
            return s.get(AgentSession, session_id)

    def list(self, *, include_children: bool = True,
             project_id: int | None = None,
             free_only: bool = False) -> list[AgentSession]:
        """project_id=某项目 → 该项目绑定的会话;free_only=True → 仅 Cowork 自由会话。"""
        with self._db.session() as s:
            q = select(AgentSession).order_by(AgentSession.updated_at.desc())
            if not include_children:
                q = q.where(AgentSession.parent_id.is_(None))
            if project_id is not None:
                q = q.where(AgentSession.project_id == project_id)
            elif free_only:
                q = q.where(AgentSession.project_id.is_(None))
            return list(s.execute(q).scalars())

    def children_of(self, session_id: int) -> list[AgentSession]:
        with self._db.session() as s:
            q = select(AgentSession).where(AgentSession.parent_id == session_id).order_by(AgentSession.id)
            return list(s.execute(q).scalars())

    def set_state(self, session_id: int, state: str) -> None:
        if state not in SESSION_STATES:
            raise ValueError(f"invalid session state: {state}")
        with self._lock, self._db.session() as s:
            row = s.get(AgentSession, session_id)
            if row is not None:
                row.status = state

    def delete(self, session_id: int) -> bool:
        with self._db.session() as s:
            row = s.get(AgentSession, session_id)
            if row is None:
                return False
            s.delete(row)  # messages 经 cascade 一并删除
            return True

    def rename(self, session_id: int, name: str) -> None:
        """重命名会话(自动标题/手动改名)。"""
        with self._lock, self._db.session() as s:
            row = s.get(AgentSession, session_id)
            if row is not None:
                row.name = name

    def set_permission_mode(self, session_id: int, mode: str) -> None:
        """切换会话级权限模式(ask/follow_a_plan/skip_all)。"""
        if mode not in PERMISSION_MODES:
            raise ValueError(f"invalid permission_mode: {mode}")
        with self._lock, self._db.session() as s:
            row = s.get(AgentSession, session_id)
            if row is not None:
                row.permission_mode = mode

    def add_approved_tool(self, session_id: int, tool_name: str) -> None:
        """ask-session 记忆:把工具名写入 session.config.approved_tools(幂等)。"""
        with self._lock, self._db.session() as s:
            row = s.get(AgentSession, session_id)
            if row is None:
                return
            config = dict(row.config or {})
            approved = list(config.get("approved_tools", []))
            if tool_name not in approved:
                approved.append(tool_name)
                config["approved_tools"] = approved
                row.config = config

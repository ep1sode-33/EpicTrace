from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from epictrace.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    folder_path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    ingest_records: Mapped[list["IngestRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    # 删项目时其绑定的会话(含消息/引用/子 agent,经各自 cascade)一并删除
    agent_sessions: Mapped[list["AgentSession"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class IngestRecord(Base):
    __tablename__ = "ingest_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    mtime: Mapped[float]
    ingest_method: Mapped[str] = mapped_column(String(32))  # file_direct / drag / session / folder_scan
    description: Mapped[str] = mapped_column(Text, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    indexed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    source_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("capture_sessions.id"), nullable=True, default=None
    )

    project: Mapped["Project"] = relationship(back_populates="ingest_records")


class Reference(Base):
    """Cowork session 的「对话引用」(由旧 ConversationReference 换绑而来;旧表经 db_migrate 一次性迁移):
    外部文件现场提取+缓存,或项目内 ingest 记录复用。绑定键是 agent_sessions.id。
    mode:fulltext(全文直注)/ focus(复用项目索引)/ deferred(待索引)/ indexed(已建会话级临时向量)。"""

    __tablename__ = "references"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))                 # external | internal
    display_name: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str | None] = mapped_column(String(1024), default=None)   # external
    ingest_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingest_records.id"), default=None                            # internal
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)        # external 缓存
    text_chars: Mapped[int] = mapped_column(default=0)
    mode: Mapped[str] = mapped_column(String(16))                # fulltext | focus | deferred | indexed
    detached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class CaptureSession(Base):
    __tablename__ = "capture_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="recording")  # recording|staged|organized
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    staging_dir: Mapped[str] = mapped_column(String(1024))
    sources: Mapped[list] = mapped_column(JSON, default=list)

    events: Mapped[list["CaptureEvent"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="CaptureEvent.ts",
    )


# 单一活动 session 的并发护栏:SQLite 部分唯一索引,只允许存在一条 status='recording'。
# 服务层的预检是快路径;此索引在并发下做最终保证(INSERT 触发 IntegrityError)。
Index(
    "uq_one_recording_session",
    CaptureSession.status,
    unique=True,
    sqlite_where=text("status = 'recording'"),
)


class CaptureEvent(Base):
    __tablename__ = "capture_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("capture_sessions.id"))
    kind: Mapped[str] = mapped_column(String(32))  # note|clipboard|screenshot|pause|resume|audio
    ts: Mapped[datetime] = mapped_column(default=_utcnow)
    payload: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    session: Mapped["CaptureSession"] = relationship(back_populates="events")


class AgentSession(Base):
    """Cowork agent session(需求 9):主 agent / 子 agent / 定时 / 纯聊天 / 文件感知。"""

    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="agent")  # agent|dispatch_child|scheduled|chat|radar
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id"), default=None              # 子 agent 指向主 agent
    )
    # 项目绑定的会话出现在「项目与对话」的对应项目下;None = Cowork 自由会话
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|thinking|executing|waiting_approval|done|error
    permission_mode: Mapped[str] = mapped_column(String(16), default="ask")  # ask|follow_a_plan|skip_all
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # 子 agent 定义、工具白名单等
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AgentMessage.id"
    )
    # 级联(codex review P2):删父会话连同子 agent;删会话连同引用;删项目连同会话
    children: Mapped[list["AgentSession"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan",
    )
    parent: Mapped["AgentSession | None"] = relationship(
        back_populates="children", remote_side="AgentSession.id",
    )
    references: Mapped[list["Reference"]] = relationship(
        cascade="all, delete-orphan",
    )
    project: Mapped["Project"] = relationship(back_populates="agent_sessions")


class AgentMessage(Base):
    """Cowork session 的消息记录(role 含 tool,工具调用经 tool_calls_json 回放)。"""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str | None] = mapped_column(String(64), default=None)        # tool 消息的工具名
    tool_call_id: Mapped[str | None] = mapped_column(String(64), default=None)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, default=None)   # assistant 的工具调用
    citations_json: Mapped[str | None] = mapped_column(Text, default=None)    # assistant 的引用链([n] → chunk)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    session: Mapped["AgentSession"] = relationship(back_populates="messages")

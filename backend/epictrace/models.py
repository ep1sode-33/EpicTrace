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
    conversations: Mapped[list["Conversation"]] = relationship(
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


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="新对话")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )
    references: Mapped[list["ConversationReference"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="ConversationReference.id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    citations_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class ConversationReference(Base):
    __tablename__ = "conversation_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))                 # external | internal
    display_name: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str | None] = mapped_column(String(1024), default=None)   # external
    ingest_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingest_records.id"), default=None                            # internal
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)        # external 缓存
    text_chars: Mapped[int] = mapped_column(default=0)
    mode: Mapped[str] = mapped_column(String(16))                # fulltext | focus | deferred
    detached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="references")


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

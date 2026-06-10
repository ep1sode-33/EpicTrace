from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text
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


class IngestRecord(Base):
    __tablename__ = "ingest_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    mtime: Mapped[float]
    ingest_method: Mapped[str] = mapped_column(String(32))  # file_direct / drag / session
    description: Mapped[str] = mapped_column(Text, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="ingest_records")

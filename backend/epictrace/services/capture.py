from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import CaptureEvent, CaptureSession
from epictrace.services.errors import (
    ActiveSessionExists,
    CaptureSessionNotFound,
    SessionNotRecording,
)


def _utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


class CaptureService:
    """会话生命周期 + 带时间戳事件 + 暂停语义 + 暂存目录。clock 可注入便于测试。"""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _utcnow) -> None:
        self._db = db
        self._clock = clock

    # —— 会话 ——
    def start(self, sources: list[str]) -> CaptureSession:
        with self._db.session() as s:
            active = s.execute(
                select(CaptureSession).where(CaptureSession.status == "recording")
            ).scalars().first()
            if active is not None:
                raise ActiveSessionExists()
            now = self._clock()
            sess = CaptureSession(
                title=f"会话 @{now.strftime('%Y-%m-%d %H:%M')}",
                status="recording", started_at=now, sources=list(sources),
                staging_dir="",  # 落库拿到 id 后再定
            )
            s.add(sess)
            s.flush()
            sess.staging_dir = str(self._db.config.data_dir / "sessions" / str(sess.id))
            Path(sess.staging_dir).mkdir(parents=True, exist_ok=True)
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def stop(self, session_id: int) -> CaptureSession:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            sess.status = "staged"
            sess.ended_at = self._clock()
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def rename(self, session_id: int, title: str) -> CaptureSession:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            sess.title = title
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def delete(self, session_id: int) -> None:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            staging = sess.staging_dir
            s.delete(sess)
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    def active_session(self) -> CaptureSession | None:
        with self._db.session() as s:
            sess = s.execute(
                select(CaptureSession).where(CaptureSession.status == "recording")
            ).scalars().first()
            if sess is not None:
                s.expunge(sess)
            return sess

    def list_sessions(self) -> list[CaptureSession]:
        with self._db.session() as s:
            rows = s.execute(
                select(CaptureSession).order_by(CaptureSession.started_at.desc())
            ).scalars().all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def get_session(self, session_id: int) -> CaptureSession:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            _ = sess.events  # 触发加载
            s.expunge_all()
            return sess

    # —— 事件 ——
    def append_event(self, session_id: int, kind: str, payload: str = "",
                     meta: dict | None = None) -> CaptureEvent:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            if sess.status != "recording":
                raise SessionNotRecording()
            ev = CaptureEvent(session_id=session_id, kind=kind, ts=self._clock(),
                             payload=payload, meta=meta or {})
            s.add(ev)
            s.flush()
            s.refresh(ev)
            s.expunge(ev)
            return ev

    def pause(self, session_id: int) -> CaptureEvent:
        return self.append_event(session_id, kind="pause")

    def resume(self, session_id: int) -> CaptureEvent:
        return self.append_event(session_id, kind="resume")

    def active_elapsed_seconds(self, session_id: int) -> float:
        """实际录制时长 = 总时长减去所有 pause→resume 区间。"""
        with self._db.session() as s:
            sess = self._require(s, session_id)
            start = sess.started_at
            end = sess.ended_at or self._clock()
            marks = [e for e in sess.events if e.kind in ("pause", "resume")]
        total = 0.0
        cursor = start
        paused = False
        for e in marks:
            if e.kind == "pause" and not paused:
                total += (e.ts - cursor).total_seconds()
                paused = True
            elif e.kind == "resume" and paused:
                cursor = e.ts
                paused = False
        if not paused:
            total += (end - cursor).total_seconds()
        return total

    def _require(self, s, session_id: int) -> CaptureSession:
        sess = s.get(CaptureSession, session_id)
        if sess is None:
            raise CaptureSessionNotFound(session_id)
        return sess

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


def _naive(dt: datetime) -> datetime:
    """归一到 naive-UTC。SQLite 取回的 datetime 是 naive,而 clock()/_utcnow 是 tz-aware;
    两者直接相减会 TypeError。算时长前统一抹掉 tzinfo(均按 UTC 看待)。"""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


class CaptureService:
    """会话生命周期 + 带时间戳事件 + 暂停语义 + 暂存目录。clock 可注入便于测试。"""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _utcnow) -> None:
        self._db = db
        self._clock = clock

    # —— 会话 ——
    def start(self, sources: list[str]) -> CaptureSession:
        try:
            with self._db.session() as s:
                # 快路径预检:已有 recording 的就直接拒(避免无谓写入)。
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
                # flush 触发 INSERT;并发下若已有 recording,部分唯一索引会抛 IntegrityError。
                s.flush()
                sess.staging_dir = str(self._db.config.data_dir / "sessions" / str(sess.id))
                Path(sess.staging_dir).mkdir(parents=True, exist_ok=True)
                s.flush()
                s.refresh(sess)
                s.expunge(sess)
                return sess
        except IntegrityError as e:
            # uq_one_recording_session 命中:并发竞态下另一条 recording 已先落库。
            raise ActiveSessionExists() from e

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
            self._rmtree_staging(staging)

    def _rmtree_staging(self, staging: str) -> None:
        """只删 data_dir/sessions/ 之内、且非符号链接的暂存目录。staging_dir 落库值
        若被篡改指向别处(路径穿越 / 符号链接),只跳过删除并记日志,绝不 rmtree。"""
        import logging

        sessions_root = (self._db.config.data_dir / "sessions").resolve()
        p = Path(staging)
        if p.is_symlink():
            logging.getLogger("epictrace").warning(
                "跳过删除符号链接 staging_dir: %s", staging)
            return
        resolved = p.resolve()
        if resolved == sessions_root or sessions_root not in resolved.parents:
            logging.getLogger("epictrace").warning(
                "跳过删除越界 staging_dir(不在 sessions/ 内): %s", staging)
            return
        shutil.rmtree(resolved, ignore_errors=True)

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

    def replace_transcription(self, session_id: int, segments: list[dict]) -> int:
        """用权威重转结果替换该 session 的全部 kind=transcription 事件(会话停止后调,staged 可用,
        不做 recording 检查)。其它事件(note/screenshot/...)不动。返回插入的段数。

        每段的 ts 由 started_at + 音频偏移(start 秒)重建:使时间线按音频时间排序、段落分组(按 ts
        间隔)照常工作。meta 带 source/audio_offset/start/end/words/wav + authoritative 标记(供引用回跳)。"""
        from datetime import timedelta

        with self._db.session() as s:
            sess = self._require(s, session_id)
            base = _naive(sess.started_at)
            for ev in list(sess.events):
                if ev.kind == "transcription":
                    s.delete(ev)
            s.flush()
            inserted = 0
            for seg in segments:
                try:
                    offset = float(seg.get("start", 0.0))
                except (TypeError, ValueError):
                    offset = 0.0
                meta = {k: seg[k] for k in
                        ("source", "audio_offset", "start", "end", "words", "wav") if k in seg}
                meta["authoritative"] = True
                s.add(CaptureEvent(session_id=session_id, kind="transcription",
                                   ts=base + timedelta(seconds=offset),
                                   payload=str(seg.get("text", "")), meta=meta))
                inserted += 1
            s.flush()
            return inserted

    def pause(self, session_id: int) -> CaptureEvent:
        return self.append_event(session_id, kind="pause")

    def resume(self, session_id: int) -> CaptureEvent:
        return self.append_event(session_id, kind="resume")

    def active_elapsed_seconds(self, session_id: int) -> float:
        """实际录制时长 = 总时长减去所有 pause→resume 区间。"""
        with self._db.session() as s:
            sess = self._require(s, session_id)
            # 全部归一到 naive-UTC 再做算术:DB 取回的是 naive,clock()/_utcnow 是 tz-aware。
            start = _naive(sess.started_at)
            end = _naive(sess.ended_at or self._clock())
            marks = [(e.kind, _naive(e.ts)) for e in sess.events
                     if e.kind in ("pause", "resume")]
        total = 0.0
        cursor = start
        paused = False
        for kind, ts in marks:
            if kind == "pause" and not paused:
                total += (ts - cursor).total_seconds()
                paused = True
            elif kind == "resume" and paused:
                cursor = ts
                paused = False
        if not paused:
            total += (end - cursor).total_seconds()
        return total

    def _require(self, s, session_id: int) -> CaptureSession:
        sess = s.get(CaptureSession, session_id)
        if sess is None:
            raise CaptureSessionNotFound(session_id)
        return sess

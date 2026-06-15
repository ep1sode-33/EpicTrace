from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.capture import CaptureService
from epictrace.services.errors import (
    ActiveSessionExists,
    CaptureSessionNotFound,
    SessionNotRecording,
)


class FakeClock:
    """可推进的时钟:每次 now() 返回当前值;advance() 前进。"""
    def __init__(self, start: datetime) -> None:
        self._t = start

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)


def _svc(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    clock = FakeClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
    return CaptureService(db, clock=clock.now), clock


def test_start_creates_recording_session_and_staging_dir(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    sess = svc.start(sources=["note", "screenshot"])
    assert sess.status == "recording"
    assert sess.sources == ["note", "screenshot"]
    assert Path(sess.staging_dir).is_dir()
    assert svc.active_session().id == sess.id


def test_single_active_session(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    svc.start(sources=["note"])
    with pytest.raises(ActiveSessionExists):
        svc.start(sources=["note"])


def test_append_event_uses_clock_and_orders_by_ts(tmp_path: Path):
    svc, clock = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    e1 = svc.append_event(sess.id, kind="note", payload="first")
    clock.advance(5)
    e2 = svc.append_event(sess.id, kind="note", payload="second")
    assert e2.ts > e1.ts
    got = svc.get_session(sess.id)
    assert [e.payload for e in got.events] == ["first", "second"]


def test_append_to_missing_session_raises(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    with pytest.raises(CaptureSessionNotFound):
        svc.append_event(999, kind="note", payload="x")


def test_append_to_stopped_session_raises(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    svc.stop(sess.id)
    with pytest.raises(SessionNotRecording):
        svc.append_event(sess.id, kind="note", payload="x")


def test_stop_sets_staged_and_ended_at(tmp_path: Path):
    svc, clock = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    clock.advance(10)
    stopped = svc.stop(sess.id)
    assert stopped.status == "staged"
    assert stopped.ended_at is not None
    assert svc.active_session() is None


def test_active_elapsed_excludes_paused_intervals(tmp_path: Path):
    svc, clock = _svc(tmp_path)
    sess = svc.start(sources=["note"])  # t0
    clock.advance(10)                   # +10 active
    svc.pause(sess.id)                  # pause at t0+10
    clock.advance(30)                   # paused 30s (excluded)
    svc.resume(sess.id)                 # resume at t0+40
    clock.advance(5)                    # +5 active
    svc.stop(sess.id)                   # end at t0+45
    assert svc.active_elapsed_seconds(sess.id) == pytest.approx(15.0)


def test_rename_and_delete_removes_staging(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    staging = Path(sess.staging_dir)
    (staging / "a.png").write_bytes(b"x")
    svc.rename(sess.id, "新名字")
    assert svc.get_session(sess.id).title == "新名字"
    svc.delete(sess.id)
    assert not staging.exists()
    assert svc.list_sessions() == []

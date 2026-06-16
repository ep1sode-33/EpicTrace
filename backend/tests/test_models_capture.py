from datetime import datetime, timezone
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import CaptureSession, CaptureEvent


def _db(tmp_path: Path) -> Database:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    return db


def test_session_with_events_roundtrip_and_cascade(tmp_path: Path):
    db = _db(tmp_path)
    with db.session() as s:
        sess = CaptureSession(
            title="会话 @x", status="recording",
            staging_dir=str(tmp_path / "sessions" / "1"), sources=["note", "clipboard"],
        )
        s.add(sess)
        s.flush()
        sid = sess.id
        s.add(CaptureEvent(session_id=sid, kind="note", payload="hi",
                           ts=datetime(2026, 6, 15, tzinfo=timezone.utc), meta={}))

    with db.session() as s:
        sess = s.get(CaptureSession, sid)
        assert sess.sources == ["note", "clipboard"]
        assert len(sess.events) == 1
        assert sess.events[0].kind == "note"

    # cascade: 删 session 连带删 events
    with db.session() as s:
        s.delete(s.get(CaptureSession, sid))
    with db.session() as s:
        from sqlalchemy import select
        assert s.execute(select(CaptureEvent)).scalars().all() == []


def test_ingest_record_has_optional_source_session_id(tmp_path: Path):
    from epictrace.models import IngestRecord
    assert IngestRecord.source_session_id is not None  # 列存在

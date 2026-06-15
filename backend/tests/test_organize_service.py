from datetime import datetime, timezone
from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import CaptureEvent, CaptureSession, IngestRecord
from epictrace.services.errors import SessionAlreadyOrganized
from epictrace.services.organize import OrganizeService
from epictrace.services.projects import ProjectService


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    staging = tmp_path / "sessions" / "1"
    staging.mkdir(parents=True)
    (staging / "shot-1.png").write_bytes(b"\x89PNG")
    with db.session() as s:
        sess = CaptureSession(id=1, title="S", status="staged",
                              staging_dir=str(staging), sources=["note", "screenshot"])
        s.add(sess)
        s.add(CaptureEvent(session_id=1, kind="note", payload="virtual memory",
                          ts=datetime(2026, 6, 15, tzinfo=timezone.utc), meta={}))
        s.add(CaptureEvent(session_id=1, kind="screenshot", payload="shot-1.png",
                          ts=datetime(2026, 6, 15, 0, 0, 1, tzinfo=timezone.utc), meta={}))
    return db, proj


def test_organize_materializes_ingests_and_marks_organized(tmp_path: Path):
    db, proj = _setup(tmp_path)
    recs = OrganizeService(db).organize(session_id=1, project_id=proj.id)

    # 文本事件 → notes.md 入库,提取文本含原文;截图 → 图片落库,extracted_text 为空
    by_name = {Path(r.stored_path).name: r for r in recs}
    assert any(n.startswith("notes") and n.endswith(".md") for n in by_name)
    notes_rec = next(r for n, r in by_name.items() if n.startswith("notes"))
    assert "virtual memory" in notes_rec.extracted_text
    assert notes_rec.ingest_method == "session"
    assert notes_rec.source_session_id == 1
    shot_rec = next(r for n, r in by_name.items() if n.startswith("shot-1"))
    assert shot_rec.extracted_text == ""
    # 入库文件落在 Project 文件夹
    assert Path(notes_rec.stored_path).parent == Path(proj.folder_path)

    with db.session() as s:
        assert s.get(CaptureSession, 1).status == "organized"
        assert s.query(IngestRecord).count() == 2


def test_organize_twice_raises(tmp_path: Path):
    db, proj = _setup(tmp_path)
    OrganizeService(db).organize(session_id=1, project_id=proj.id)
    with pytest.raises(SessionAlreadyOrganized):
        OrganizeService(db).organize(session_id=1, project_id=proj.id)

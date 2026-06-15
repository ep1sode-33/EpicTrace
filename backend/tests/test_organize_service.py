from datetime import datetime, timezone
from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import CaptureEvent, CaptureSession, IngestRecord
from epictrace.services.errors import SessionAlreadyOrganized, SessionNotStaged
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


def test_organize_recording_session_raises_not_staged(tmp_path: Path):
    """FIX 5:录制中(recording)的 session 不应被归类。"""
    db, proj = _setup(tmp_path)
    with db.session() as s:
        s.get(CaptureSession, 1).status = "recording"
    with pytest.raises(SessionNotStaged):
        OrganizeService(db).organize(session_id=1, project_id=proj.id)


def test_organize_skips_path_traversal_screenshot(tmp_path: Path):
    """FIX 2:截图相对路径来自事件 payload(任意字符串)。指向 staging 之外的
    (路径穿越 / 绝对路径)一律跳过,不入库。"""
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    staging = tmp_path / "sessions" / "1"
    staging.mkdir(parents=True)
    (staging / "ok.png").write_bytes(b"\x89PNG")
    # staging 外放一个「目标」文件,确认穿越路径不会把它拽进来。
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    with db.session() as s:
        s.add(CaptureSession(id=1, title="S", status="staged",
                             staging_dir=str(staging), sources=["screenshot"]))
        # 合法的 staging 内截图
        s.add(CaptureEvent(session_id=1, kind="screenshot", payload="ok.png",
                          ts=datetime(2026, 6, 15, tzinfo=timezone.utc), meta={}))
        # 路径穿越:试图逃出 staging
        s.add(CaptureEvent(session_id=1, kind="screenshot", payload="../../secret.txt",
                          ts=datetime(2026, 6, 15, 0, 0, 1, tzinfo=timezone.utc), meta={}))
        # 绝对路径
        s.add(CaptureEvent(session_id=1, kind="screenshot", payload=str(outside),
                          ts=datetime(2026, 6, 15, 0, 0, 2, tzinfo=timezone.utc), meta={}))

    recs = OrganizeService(db).organize(session_id=1, project_id=proj.id)
    names = [Path(r.stored_path).name for r in recs]
    # 仅合法的 ok.png 入库;secret.txt 绝不入库
    assert any(n.startswith("ok") for n in names)
    assert not any("secret" in n for n in names)
    assert len(recs) == 1


def test_organize_retry_after_partial_failure_no_duplicates(tmp_path: Path):
    """FIX 6:归类前先清掉本 session 的旧 IngestRecord(干净重来),
    部分失败后重试不会产生重复记录。这里通过把已 organize 的会话翻回 staged 再跑一次模拟。"""
    db, proj = _setup(tmp_path)
    OrganizeService(db).organize(session_id=1, project_id=proj.id)
    with db.session() as s:
        first_count = s.query(IngestRecord).filter_by(source_session_id=1).count()
        # 翻回 staged 以允许重跑(模拟「上次部分失败需重试」)。
        s.get(CaptureSession, 1).status = "staged"
    assert first_count == 2

    OrganizeService(db).organize(session_id=1, project_id=proj.id)
    with db.session() as s:
        # 没有重复:仍是 2 条(旧的被清掉后重建),而非 4 条。
        assert s.query(IngestRecord).filter_by(source_session_id=1).count() == 2

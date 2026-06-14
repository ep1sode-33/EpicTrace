from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import IngestRecord, Project
from epictrace.services.source import SourceService


def test_source_reextracts_text_for_record(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    f = tmp_path / "note.md"; f.write_text("虚拟内存与页表", encoding="utf-8")
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        r = IngestRecord(project_id=p.id, original_filename="note.md", stored_path=str(f),
                         content_hash="x", size_bytes=f.stat().st_size, mtime=f.stat().st_mtime,
                         ingest_method="folder_scan", extracted_text="", indexed=True)
        s.add(r); s.flush(); rid = r.id
    out = SourceService(db).get_text(rid)
    assert out["filename"] == "note.md"
    assert out["text"] == "虚拟内存与页表"


def test_source_unknown_record_raises(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    import pytest
    with pytest.raises(ValueError):
        SourceService(db).get_text(99999)


def test_source_prefers_cached_extracted_text(tmp_path: Path, monkeypatch):
    """有缓存的 extracted_text 时,来源查看器直接用缓存,绝不重跑 MinerU(慢且会与索引脱节)。"""
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    f = tmp_path / "paper.pdf"; f.write_bytes(b"%PDF-1.4 fake")
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        r = IngestRecord(project_id=p.id, original_filename="paper.pdf", stored_path=str(f),
                         content_hash="x", size_bytes=f.stat().st_size, mtime=f.stat().st_mtime,
                         ingest_method="file_direct", extracted_text="缓存的提取文本", indexed=True)
        s.add(r); s.flush(); rid = r.id

    def _boom(*a, **k):
        raise AssertionError("get_processor must NOT be called when cached text exists")

    monkeypatch.setattr("epictrace.services.source.get_processor", _boom)
    out = SourceService(db).get_text(rid)
    assert out["text"] == "缓存的提取文本"
    assert out["filename"] == "paper.pdf"


def test_source_reextracts_when_cache_empty(tmp_path: Path):
    """无缓存(空 extracted_text)时,才回退现提取。"""
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    f = tmp_path / "note.md"; f.write_text("现场提取内容", encoding="utf-8")
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        r = IngestRecord(project_id=p.id, original_filename="note.md", stored_path=str(f),
                         content_hash="x", size_bytes=f.stat().st_size, mtime=f.stat().st_mtime,
                         ingest_method="folder_scan", extracted_text="", indexed=True)
        s.add(r); s.flush(); rid = r.id
    out = SourceService(db).get_text(rid)
    assert out["text"] == "现场提取内容"


def test_source_route_maps_extraction_not_ready(tmp_path: Path, monkeypatch):
    """残留的现提取路径若抛 ExtractionEngineNotReady,路由应给干净的 409 而非 500。"""
    from fastapi.testclient import TestClient

    from epictrace.api.app import create_app
    from epictrace.media.errors import ExtractionEngineNotReady

    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    f = tmp_path / "paper.pdf"; f.write_bytes(b"%PDF-1.4 fake")
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        # extracted_text 为空 → 会走现提取路径
        r = IngestRecord(project_id=p.id, original_filename="paper.pdf", stored_path=str(f),
                         content_hash="x", size_bytes=f.stat().st_size, mtime=f.stat().st_mtime,
                         ingest_method="file_direct", extracted_text="", indexed=True)
        s.add(r); s.flush(); rid = r.id

    class _Proc:
        def process(self, _path):
            raise ExtractionEngineNotReady("请先在设置中安装高质量提取引擎")

    monkeypatch.setattr("epictrace.services.source.get_processor", lambda path, config: _Proc())
    client = TestClient(create_app(db=db))
    resp = client.get(f"/api/source/{rid}")
    assert resp.status_code == 409
    assert "高质量提取引擎" in resp.json()["detail"]

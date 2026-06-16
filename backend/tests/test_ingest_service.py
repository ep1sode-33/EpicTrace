import hashlib
from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.errors import (
    InvalidSourcePath,
    ProjectNotFound,
    SourceFileNotFound,
)
from epictrace.services.ingest import IngestService
from epictrace.services.projects import ProjectService


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    return db, proj


def test_ingest_copies_file_and_records_metadata(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "src" / "note.md"
    src.parent.mkdir()
    src.write_text("# vm\nvirtual memory", encoding="utf-8")

    svc = IngestService(db)
    rec = svc.ingest_file(
        project_id=proj.id,
        source_path=str(src),
        ingest_method="file_direct",
        description="5/13 CS2506 PPT",
    )

    stored = Path(rec.stored_path)
    assert stored.exists()
    assert stored.parent == Path(proj.folder_path)        # 复制进 Project 文件夹
    assert rec.content_hash == hashlib.sha256(src.read_bytes()).hexdigest()
    assert rec.size_bytes == src.stat().st_size
    assert rec.ingest_method == "file_direct"
    assert rec.description == "5/13 CS2506 PPT"
    assert "virtual memory" in rec.extracted_text   # 文本被提取


def test_ingest_unknown_type_leaves_text_empty(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "a.bin"
    src.write_bytes(b"\x00\x01")
    rec = IngestService(db).ingest_file(
        project_id=proj.id, source_path=str(src), ingest_method="file_direct", description=""
    )
    assert rec.extracted_text == ""
    assert Path(rec.stored_path).exists()


def test_ingest_avoids_overwriting_same_name(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "dup.txt"
    src.write_text("a", encoding="utf-8")
    svc = IngestService(db)
    r1 = svc.ingest_file(project_id=proj.id, source_path=str(src), ingest_method="file_direct", description="")
    r2 = svc.ingest_file(project_id=proj.id, source_path=str(src), ingest_method="file_direct", description="")
    assert r1.stored_path != r2.stored_path   # 重名不覆盖
    assert Path(r1.stored_path).exists() and Path(r2.stored_path).exists()


def test_list_for_project(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    svc = IngestService(db)
    svc.ingest_file(project_id=proj.id, source_path=str(src), ingest_method="file_direct", description="")
    assert len(svc.list_for_project(proj.id)) == 1


def test_ingest_missing_project_raises(tmp_path: Path):
    db, _ = _setup(tmp_path)
    src = tmp_path / "f.txt"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(ProjectNotFound):
        IngestService(db).ingest_file(
            project_id=99999,
            source_path=str(src),
            ingest_method="file_direct",
            description="",
        )


def test_ingest_missing_source_raises(tmp_path: Path):
    db, proj = _setup(tmp_path)
    with pytest.raises(SourceFileNotFound):
        IngestService(db).ingest_file(
            project_id=proj.id,
            source_path=str(tmp_path / "nonexistent.txt"),
            ingest_method="file_direct",
            description="",
        )


def test_ingest_source_is_directory_raises(tmp_path: Path):
    db, proj = _setup(tmp_path)
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(InvalidSourcePath):
        IngestService(db).ingest_file(
            project_id=proj.id,
            source_path=str(d),
            ingest_method="file_direct",
            description="",
        )


def test_ingest_cleans_up_orphan_on_extraction_failure(tmp_path: Path, monkeypatch):
    db, proj = _setup(tmp_path)
    src = tmp_path / "src" / "data.txt"
    src.parent.mkdir()
    src.write_text("hello", encoding="utf-8")

    class _BadProc:
        def process(self, _path):
            raise RuntimeError("boom")

    monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda _path, _config: _BadProc())

    folder = Path(proj.folder_path)
    with pytest.raises(RuntimeError):
        IngestService(db).ingest_file(
            project_id=proj.id,
            source_path=str(src),
            ingest_method="file_direct",
            description="",
        )

    # No orphaned copy should remain in the project folder
    copied_files = [p for p in folder.iterdir() if p.is_file()]
    assert copied_files == [], f"Orphan files found: {copied_files}"


def test_ingest_pdf_persists_provenance_sidecar(tmp_path: Path, monkeypatch):
    db, proj = _setup(tmp_path)
    src = tmp_path / "src" / "paper.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF-1.4 fake")

    from epictrace.interfaces.media import MediaResult

    content = [{"type": "text", "text": "hi", "page_idx": 0}]

    class _PdfProc:
        def supports(self, _path):
            return True

        def process(self, _path):
            return MediaResult(text="# extracted", metadata={
                "backend": "mineru-hybrid", "content_list": content, "pages": 1})

    monkeypatch.setattr(
        "epictrace.services.ingest.get_processor",
        lambda path, config: _PdfProc(),
    )
    rec = IngestService(db).ingest_file(
        project_id=proj.id, source_path=str(src),
        ingest_method="file_direct", description="",
    )
    sidecar = Path(tmp_path) / "provenance" / f"ingest-{rec.id}.json"
    assert sidecar.exists()
    import json
    assert json.loads(sidecar.read_text(encoding="utf-8")) == content
    assert rec.extracted_text == "# extracted"


def test_ingest_succeeds_even_if_provenance_write_fails(tmp_path: Path, monkeypatch):
    """provenance(content_list sidecar)是派生/可选缓存:写失败绝不能回滚入库(删文件)。"""
    db, proj = _setup(tmp_path)
    src = tmp_path / "src" / "paper.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF-1.4 fake")

    from epictrace.interfaces.media import MediaResult

    class _PdfProc:
        def supports(self, _path):
            return True

        def process(self, _path):
            return MediaResult(text="# extracted", metadata={
                "backend": "mineru-hybrid",
                "content_list": [{"type": "text", "text": "hi", "page_idx": 0}],
                "pages": 1})

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda path, config: _PdfProc())
    monkeypatch.setattr("epictrace.services.ingest.write_provenance", _boom)

    rec = IngestService(db).ingest_file(
        project_id=proj.id, source_path=str(src),
        ingest_method="file_direct", description="",
    )
    # 入库成功:record 持久化、复制的文件保留、提取文本在
    assert rec.extracted_text == "# extracted"
    assert Path(rec.stored_path).exists()
    sidecar = Path(tmp_path) / "provenance" / f"ingest-{rec.id}.json"
    assert not sidecar.exists()  # provenance 写失败 → 没落盘,但不影响入库


def test_ingest_records_source_session_id(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "note.md"
    src.write_text("hello", encoding="utf-8")
    rec = IngestService(db).ingest_file(
        project_id=proj.id, source_path=str(src),
        ingest_method="session", description="", source_session_id=42,
    )
    assert rec.ingest_method == "session"
    assert rec.source_session_id == 42

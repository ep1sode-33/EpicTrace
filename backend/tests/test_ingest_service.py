import hashlib
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
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

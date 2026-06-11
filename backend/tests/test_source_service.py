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

from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import IngestRecord, Project


def test_project_and_ingest_record_persist(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    with db.session() as s:
        proj = Project(title="CS 2506", folder_path=str(tmp_path / "CS 2506"))
        s.add(proj)
        s.flush()
        rec = IngestRecord(
            project_id=proj.id,
            original_filename="lecture.md",
            stored_path=str(tmp_path / "CS 2506" / "lecture.md"),
            content_hash="abc123",
            size_bytes=10,
            mtime=1.5,
            ingest_method="file_direct",
            description="virtual memory",
            extracted_text="hello",
        )
        s.add(rec)
        s.flush()
        assert proj.id is not None
        assert rec.id is not None
        assert rec.project_id == proj.id

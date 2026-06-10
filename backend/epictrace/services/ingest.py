from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import IngestRecord, Project


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_dest(folder: Path, filename: str) -> Path:
    dest = folder / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


class IngestService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ingest_file(
        self, project_id: int, source_path: str, ingest_method: str, description: str
    ) -> IngestRecord:
        src = Path(source_path)
        with self._db.session() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id} not found")
            folder = Path(project.folder_path)
            folder.mkdir(parents=True, exist_ok=True)

            dest = _unique_dest(folder, src.name)
            shutil.copy2(src, dest)

            proc = get_processor(dest)
            extracted = proc.process(dest).text if proc is not None else ""

            rec = IngestRecord(
                project_id=project_id,
                original_filename=src.name,
                stored_path=str(dest),
                content_hash=_sha256(dest),
                size_bytes=dest.stat().st_size,
                mtime=dest.stat().st_mtime,
                ingest_method=ingest_method,
                description=description,
                extracted_text=extracted,
            )
            s.add(rec)
            s.flush()
            s.refresh(rec)
            s.expunge(rec)
            return rec

    def list_for_project(self, project_id: int) -> list[IngestRecord]:
        with self._db.session() as s:
            rows = (
                s.execute(
                    select(IngestRecord)
                    .where(IngestRecord.project_id == project_id)
                    .order_by(IngestRecord.created_at)
                )
                .scalars()
                .all()
            )
            for r in rows:
                s.expunge(r)
            return list(rows)

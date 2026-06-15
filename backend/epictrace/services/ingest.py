from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.media.provenance import write_provenance
from epictrace.models import IngestRecord, Project
from epictrace.services.errors import (
    InvalidSourcePath,
    ProjectNotFound,
    SourceFileNotFound,
    SourceUnreadable,
)


_log = logging.getLogger("epictrace")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_dest(folder: Path, filename: str) -> Path:
    dest = folder / filename
    if not dest.exists() and not dest.is_symlink():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        i += 1


class IngestService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ingest_file(
        self, project_id: int, source_path: str, ingest_method: str, description: str,
        source_session_id: int | None = None,
    ) -> IngestRecord:
        src = Path(source_path)
        if not src.exists():
            raise SourceFileNotFound(source_path)
        elif not src.is_file():
            raise InvalidSourcePath(source_path)

        with self._db.session() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            folder = Path(project.folder_path)
            folder.mkdir(parents=True, exist_ok=True)

            dest = _unique_dest(folder, src.name)
            try:
                shutil.copy2(src, dest)
            except PermissionError as e:
                raise SourceUnreadable(source_path) from e

            try:
                proc = get_processor(dest, self._db.config)
                result = proc.process(dest) if proc is not None else None
                extracted = result.text if result is not None else ""

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
                    source_session_id=source_session_id,
                )
                s.add(rec)
                s.flush()
                s.refresh(rec)
                # provenance(content_list sidecar)是派生/可选缓存(重跑 MinerU 可重建):
                # 写失败绝不能回滚入库,所以单独窄 try/except,不让它落进下方的清理-重抛块。
                if result is not None and result.metadata.get("content_list"):
                    try:
                        write_provenance(
                            self._db.config.data_dir, "ingest", rec.id,
                            result.metadata["content_list"],
                        )
                    except Exception:  # noqa: BLE001 — 派生缓存写失败仅记日志,入库照常成功
                        _log.warning(
                            "write_provenance failed for ingest %s; "
                            "skipping sidecar (extracted text is unaffected)",
                            rec.id, exc_info=True,
                        )
                s.expunge(rec)
                return rec
            except Exception:
                dest.unlink(missing_ok=True)
                raise

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

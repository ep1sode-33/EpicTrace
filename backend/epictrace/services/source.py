from __future__ import annotations

from pathlib import Path

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import IngestRecord


class SourceService:
    """来源查看器后端:按 ingest_record_id 取回原始文件并用 MediaProcessor 重新提取文本。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_text(self, ingest_record_id: int) -> dict:
        with self._db.session() as s:
            rec = s.get(IngestRecord, ingest_record_id)
            if rec is None:
                raise ValueError("ingest record not found")
            path = Path(rec.stored_path)
            filename = rec.original_filename
        proc = get_processor(path)
        text = proc.process(path).text if proc is not None else ""
        return {"filename": filename, "path": str(path), "text": text}

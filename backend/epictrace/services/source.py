from __future__ import annotations

from pathlib import Path

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import ConversationReference, IngestRecord


class SourceService:
    """来源查看器后端:按 ingest_record_id 取回原始文件文本。

    优先用入库时持久化的 extracted_text(与索引同源,且免去每次点引用都重跑 MinerU 的慢与脱节);
    仅当缓存为空时才回退到现场提取。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_text(self, ingest_record_id: int) -> dict:
        with self._db.session() as s:
            rec = s.get(IngestRecord, ingest_record_id)
            if rec is None:
                raise ValueError("ingest record not found")
            path = Path(rec.stored_path)
            filename = rec.original_filename
            text = rec.extracted_text or ""
        # 缓存命中即用,绝不重跑 MinerU;仅缓存为空时才现场提取(最后手段)。
        if not text:
            proc = get_processor(path, self._db.config)
            text = proc.process(path).text if proc is not None else ""
        return {"filename": filename, "path": str(path), "text": text}

    def get_attachment_text(self, reference_id: int) -> dict:
        """外部附件引用的来源:优先用缓存的提取文本;缺失则按 source_path 现提取。"""
        with self._db.session() as s:
            ref = s.get(ConversationReference, reference_id)
            if ref is None:
                raise ValueError("reference not found")
            name = ref.display_name
            path = ref.source_path or ""
            text = ref.extracted_text or ""
        if not text and path and Path(path).exists():
            proc = get_processor(Path(path), self._db.config)
            text = proc.process(Path(path)).text if proc is not None else ""
        return {"filename": name, "path": path, "text": text}

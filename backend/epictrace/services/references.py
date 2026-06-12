from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import Conversation, ConversationReference, IngestRecord
from epictrace.services.budget import estimate_tokens, fits_fulltext


def _to_dict(r: ConversationReference) -> dict:
    return {
        "id": r.id, "conversation_id": r.conversation_id, "kind": r.kind,
        "display_name": r.display_name, "source_path": r.source_path,
        "ingest_record_id": r.ingest_record_id, "extracted_text": r.extracted_text,
        "mode": r.mode, "text_chars": r.text_chars, "detached": r.detached,
        "created_at": r.created_at,
    }


class ReferenceService:
    """会话级“对话引用”管理:外部文件现场提取+缓存、内部文件复用项目索引;按 context_window
    做 size-gate(小→fulltext / 外部大→deferred / 内部大→focus)。外部不向量化、不入库。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def _used_fulltext_tokens(self, conversation_id: int) -> int:
        return sum(estimate_tokens(r.get("extracted_text") or "")
                   for r in self.list_active(conversation_id) if r["mode"] == "fulltext")

    def add_external(self, conversation_id: int, path: str, context_window: int) -> dict:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise ValueError("file not found")
        proc = get_processor(p)
        if proc is None:
            raise ValueError("unsupported file type")
        try:
            text = proc.process(p).text
        except Exception as e:  # noqa: BLE001 — 提取失败转成可读的 400(由路由映射)
            raise ValueError(f"extract failed: {e}")
        if not text.strip():
            raise ValueError("empty file")
        used = self._used_fulltext_tokens(conversation_id)
        mode = "fulltext" if fits_fulltext(text, context_window, used) else "deferred"
        with self._db.session() as s:
            ref = ConversationReference(
                conversation_id=conversation_id, kind="external", display_name=p.name,
                source_path=str(p), extracted_text=text, text_chars=len(text), mode=mode,
            )
            s.add(ref); s.flush(); s.refresh(ref)
            return _to_dict(ref)

    def add_internal(self, conversation_id: int, ingest_record_id: int, context_window: int) -> dict:
        with self._db.session() as s:
            conv = s.get(Conversation, conversation_id)
            if conv is None:
                raise ValueError("conversation not found")
            rec = s.get(IngestRecord, ingest_record_id)
            if rec is None:
                raise ValueError("ingest record not found")
            if rec.project_id != conv.project_id:
                raise ValueError("ingest record belongs to a different project")
            path = Path(rec.stored_path); name = rec.original_filename
        proc = get_processor(path)
        text = ""
        if proc is not None:
            try:
                text = proc.process(path).text
            except Exception:  # noqa: BLE001 — 提取失败 → 退化为 focus(复用现成向量)
                text = ""
        # 内部:小→fulltext(缓存整段);大或无法提取→focus(只记 ingest_record_id,复用现成向量)
        used = self._used_fulltext_tokens(conversation_id)
        fulltext = bool(text.strip()) and fits_fulltext(text, context_window, used)
        mode = "fulltext" if fulltext else "focus"
        with self._db.session() as s:
            ref = ConversationReference(
                conversation_id=conversation_id, kind="internal", display_name=name,
                ingest_record_id=ingest_record_id,
                extracted_text=(text if fulltext else None),
                text_chars=len(text), mode=mode,
            )
            s.add(ref); s.flush(); s.refresh(ref)
            return _to_dict(ref)

    def detach(self, conversation_id: int, reference_id: int) -> None:
        with self._db.session() as s:
            ref = s.get(ConversationReference, reference_id)
            if ref is not None and ref.conversation_id == conversation_id:
                ref.detached = True

    def list_active(self, conversation_id: int) -> list[dict]:
        with self._db.session() as s:
            rows = s.execute(
                select(ConversationReference).where(
                    ConversationReference.conversation_id == conversation_id,
                    ConversationReference.detached.is_(False),
                ).order_by(ConversationReference.id)
            ).scalars().all()
            return [_to_dict(r) for r in rows]

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.indexing.chunker import chunk_text
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
    做 size-gate(小→fulltext / 外部大→indexed / 内部大→focus)。给了 embedder+attachment_store
    时,外部大文件会切块+embed 进会话级临时集合(indexed);否则仅登记为 deferred。"""

    def __init__(self, db: Database, embedder=None, attachment_store=None) -> None:
        self._db = db
        self._embedder = embedder
        self._attachment_store = attachment_store

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
            out = _to_dict(ref)
            ref_id = ref.id
        # 大文件:尝试切块+embed 进会话级临时集合(失败保持 deferred,不阻塞)。
        if mode == "deferred" and self._embedder is not None and self._attachment_store is not None:
            if self._index_attachment(conversation_id, ref_id, text):
                with self._db.session() as s:
                    r = s.get(ConversationReference, ref_id)
                    if r is not None:
                        r.mode = "indexed"
                out["mode"] = "indexed"
        return out

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

    def _index_attachment(self, conversation_id: int, reference_id: int, text: str) -> bool:
        """切块 → embed → upsert 进临时集合。成功 True,失败 False(调用方保持 deferred)。"""
        try:
            chunks = chunk_text(text)
            if not chunks:
                return False
            vectors = self._embedder.embed([c.text for c in chunks])
            self._attachment_store.upsert([
                {"vector": vec, "text": c.text, "conversation_id": conversation_id,
                 "reference_id": reference_id, "char_start": c.char_start, "char_end": c.char_end,
                 "source_type": "attachment", "embed_model_id": self._embedder.model_id}
                for c, vec in zip(chunks, vectors)
            ])
            return True
        except Exception:  # noqa: BLE001 — 索引失败回退 deferred
            return False

    def detach(self, conversation_id: int, reference_id: int) -> None:
        owned = False
        with self._db.session() as s:
            ref = s.get(ConversationReference, reference_id)
            if ref is not None and ref.conversation_id == conversation_id:
                ref.detached = True
                owned = True
        # 仅在归属校验通过时清理临时向量,且按 conversation_id + reference_id 双重限定;
        # 清理失败不应影响解挂(残留向量无害——检索按活跃引用过滤)。
        if owned and self._attachment_store is not None:
            try:
                self._attachment_store.delete({"conversation_id": conversation_id,
                                               "reference_id": reference_id})
            except Exception:  # noqa: BLE001
                pass

    def list_active(self, conversation_id: int) -> list[dict]:
        with self._db.session() as s:
            rows = s.execute(
                select(ConversationReference).where(
                    ConversationReference.conversation_id == conversation_id,
                    ConversationReference.detached.is_(False),
                ).order_by(ConversationReference.id)
            ).scalars().all()
            return [_to_dict(r) for r in rows]

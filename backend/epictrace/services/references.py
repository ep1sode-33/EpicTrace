from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.indexing.chunker import chunk_text
from epictrace.media import get_processor
from epictrace.media.mineru import MinerUMediaProcessor
from epictrace.media.provenance import write_provenance
from epictrace.models import Conversation, ConversationReference, IngestRecord
from epictrace.services.budget import estimate_tokens, fits_fulltext
from epictrace.services.settings import SettingsService

_log = logging.getLogger("epictrace")


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

    def __init__(self, db: Database, embedder=None, attachment_store=None,
                 provisioner=None) -> None:
        self._db = db
        self._embedder = embedder
        self._attachment_store = attachment_store
        self._provisioner = provisioner

    def _used_fulltext_tokens(self, conversation_id: int) -> int:
        return sum(estimate_tokens(r.get("extracted_text") or "")
                   for r in self.list_active(conversation_id) if r["mode"] == "fulltext")

    def _ensure_models_ready(self, progress_cb=None) -> None:
        """提取前的「门」:provisioner 为 installed_no_models 或 downloading_models 时,
        阻塞到模型就绪(ensure_models_ready 内部:未下则下,正在下则等),失败/超时上抛。
        无 provisioner / ready / not_installed → no-op(ready 直接提取;not_installed 由 process 抛错)。

        关键(对应并发 bug):仅在 installed_no_models 触发会导致第二个并发 caller 看到
        downloading_models 时跳过、抢先提取 → 模型未就绪提取失败。这里把 downloading_models
        也交给 ensure_models_ready 阻塞等待,直到就绪才返回。"""
        prov = self._provisioner
        if prov is None:
            return
        if getattr(prov, "state", None) in ("installed_no_models", "downloading_models"):
            ext = SettingsService(self._db.config).get_extraction_settings()
            prov.ensure_models_ready(
                model_source=ext["model_source"], progress_cb=progress_cb
            )

    def add_external(self, conversation_id: int, path: str, context_window: int,
                     progress_cb=None, cancel=None) -> dict:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise ValueError("file not found")
        proc = get_processor(p, self._db.config)
        if proc is None:
            raise ValueError("unsupported file type")
        try:
            # 仅富文档(MinerU)需要模型;文本/代码/数据文件走 TextMediaProcessor,
            # 即使 installed_no_models 也不该触发(几 GB 的)模型下载(FIX 2)。
            # 富文档且「装了包但没下模型 / 正在下」→ 阻塞到模型就绪再提取(FIX 1)。
            # not_installed / ready 不在此处理:not_installed 由 process() 抛
            # ExtractionEngineNotReady,ready 直接提取。
            if isinstance(proc, MinerUMediaProcessor):
                self._ensure_models_ready(progress_cb)
            result = proc.process(p, progress_cb=progress_cb, cancel=cancel)
        except Exception as e:  # noqa: BLE001 — 模型确保/提取失败都转成可读的 400(由路由映射)
            raise ValueError(f"extract failed: {e}")
        text = result.text
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
        # provenance sidecar 派生/可选:ref 已 commit,写失败仅记日志,不向调用方传播。
        if result.metadata.get("content_list"):
            try:
                write_provenance(
                    self._db.config.data_dir, "reference", ref_id,
                    result.metadata["content_list"],
                )
            except Exception:  # noqa: BLE001 — 派生缓存写失败不影响已登记的引用
                _log.warning(
                    "write_provenance failed for reference %s; "
                    "skipping sidecar (extracted text is unaffected)",
                    ref_id, exc_info=True,
                )
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
        proc = get_processor(path, self._db.config)
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

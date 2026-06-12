# Plan 5 — 大外部附件的会话级临时 RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Plan 4 里被标 `deferred`(不可用)的大外部附件可用——挂载时切块+embed 进一个会话级、用完即清的临时向量集合,聊天检索轮按 `conversation_id`+`reference_id` 检索 top-k 进【资料】,引用精确到 chunk 并跳回原文。

**Architecture:** 复用 Plan 2/3/4 的 `Chunker`、`BgeM3Embedder`、dense/sparse/RRF/`BgeReranker`、`RetrievedChunk`、引用/SourceViewer 全链路。新增:`MilvusLiteStore` 参数化 collection(临时 `attachment_chunks`)、`deps.get_attachment_store`、`ReferenceService` 的索引分支(`deferred`→`indexed`)+ 解挂清理、`retrieval/attachment.py` 的 `AttachmentRetriever`、`ChatService` 合并附件检索、删对话清理。后端 TDD;前端只加 `indexed` 模式标签 + 索引中提示。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Milvus Lite / FlagEmbedding / jieba+rank_bm25 / LangGraph;React 19 / Vite / Tailwind / shadcn;pytest(Fake LLM/Embedder/Reranker/VectorStore)。

**设计来源:** `docs/superpowers/specs/2026-06-12-epictrace-plan-5-ephemeral-attachment-rag-design.md`(决策 `docs/decisions/2026-06-11-attachment-phase-plan-and-tech-choices.md` D14)。

**约定:** 提交用 ep1sode-33 git 身份,message 结尾附 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。后端命令 `cd backend && .venv/bin/pytest ...`;前端 `cd frontend && npm run build`。**先开 feature 分支再动手**(`feat/plan-5-attachment-rag`;收尾用 superpowers:finishing-a-development-branch)。

---

## File Structure

**后端 — 新建**
- `backend/epictrace/retrieval/attachment.py` — `AttachmentRetriever`:在临时集合上 dense+sparse→rerank,按会话+引用过滤。
- 测试:`tests/test_vectorstore_collections.py`、`tests/test_references_indexing.py`、`tests/test_attachment_retrieve.py`、`tests/test_chat_attachment_rag.py`、`tests/test_api_attachment_cleanup.py`。

**后端 — 修改**
- `vectorstore/milvus_lite.py`(参数化 collection + scalars;新增通用 `delete(filter)`/`list_by(filter)` + `_ATTACHMENT_SCALARS`)、`interfaces/vector_store.py`(ABC 加 `delete`/`list_by`)、`tests/fakes.py`(FakeVectorStore 加 `delete`/`list_by`)、`api/deps.py`(`get_attachment_store`)、`services/references.py`(索引分支 + 清理 + 构造注入)、`services/chat.py`(合并附件检索)、`api/routers/references.py`(注入 embedder+attachment store)、`api/routers/conversations.py`(ChatService 注入 attachment retriever + 删对话清理)、`api/app.py`(`app.state.attachment_store` 槽)。

**前端 — 修改**
- `lib/api.ts`(`ConversationReference.mode` 加 `"indexed"`)、`components/ReferencePanel.tsx`(`MODE_LABEL` 加 `indexed`)、`views/ProjectsConversationView.tsx`(attachExternal 期间「正在索引附件…」提示)。

---

## Task 1: `MilvusLiteStore` 参数化 collection + 通用 delete/list_by

**Files:**
- Modify: `backend/epictrace/vectorstore/milvus_lite.py`、`backend/epictrace/interfaces/vector_store.py`、`backend/tests/fakes.py`
- Test: `backend/tests/test_vectorstore_collections.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_vectorstore_collections.py
from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore, _ATTACHMENT_SCALARS

DIM = 1024


def _arec(cid: int, rid: int, text: str) -> dict:
    return {"vector": [0.1] * DIM, "text": text, "conversation_id": cid, "reference_id": rid,
            "char_start": 0, "char_end": len(text), "source_type": "attachment",
            "embed_model_id": "fake"}


def test_attachment_collection_roundtrip_filter_and_cleanup(tmp_path: Path):
    db = str(tmp_path / "v.db")
    store = MilvusLiteStore(db_path=db, dim=DIM, collection="attachment_chunks",
                            scalars=_ATTACHMENT_SCALARS)
    store.upsert([_arec(1, 10, "页表"), _arec(1, 20, "缓存"), _arec(2, 30, "无关")])
    # 按 conversation_id + reference_id IN 过滤 list_by(给稀疏检索喂语料)
    rows = store.list_by({"conversation_id": 1, "reference_id": [10, 20]})
    assert {r["reference_id"] for r in rows} == {10, 20}
    # 向量检索同样能过滤
    hits = store.query([0.1] * DIM, filter={"conversation_id": 1, "reference_id": [10]}, k=10)
    assert [h["reference_id"] for h in hits] == [10]
    # 通用 delete(filter):按 reference_id 删
    store.delete({"reference_id": 10})
    assert {r["reference_id"] for r in store.list_by({"conversation_id": 1})} == {20}
    # 按 conversation_id 删
    store.delete({"conversation_id": 1})
    assert store.list_by({"conversation_id": 1}) == []
    store.close()


def test_default_chunks_collection_still_works(tmp_path: Path):
    s = MilvusLiteStore(db_path=str(tmp_path / "c.db"), dim=DIM)  # 默认 chunks + _SCALARS
    s.upsert([{"vector": [0.1] * DIM, "text": "x", "ingest_record_id": 1, "project_id": 7,
               "char_start": 0, "char_end": 1, "source_type": "folder_scan", "embed_model_id": "f"}])
    assert len(s.list_by_project(7)) == 1
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_vectorstore_collections.py -q`
Expected: FAIL（`_ATTACHMENT_SCALARS` 不存在 / `MilvusLiteStore` 不接受 collection/scalars、无 `delete`/`list_by`）

- [ ] **Step 3: 重写 `milvus_lite.py`**

完整替换 `backend/epictrace/vectorstore/milvus_lite.py`:

```python
from __future__ import annotations

import logging

from pymilvus import DataType, MilvusClient

from epictrace.interfaces.vector_store import VectorStore

_log = logging.getLogger("epictrace")

_COLLECTION = "chunks"
_LIST_LIMIT = 16384
# 项目永久 chunks 的字段。
_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "ingest_record_id": (DataType.INT64, {}),
    "project_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
}
# 会话级临时附件 chunks 的字段(按 conversation_id + reference_id 过滤/清理)。
_ATTACHMENT_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "conversation_id": (DataType.INT64, {}),
    "reference_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
}


class MilvusLiteStore(VectorStore):
    def __init__(self, db_path: str, dim: int = 1024, collection: str = _COLLECTION,
                 scalars: dict | None = None) -> None:
        self._client = MilvusClient(db_path)
        self._dim = dim
        self._collection = collection
        self._scalars = scalars if scalars is not None else _SCALARS
        if not self._client.has_collection(collection):
            schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
            for name, (dtype, kw) in self._scalars.items():
                schema.add_field(name, dtype, **kw)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector", index_type="HNSW", metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self._client.create_collection(collection, schema=schema, index_params=index_params)
        # 幂等加载(否则对已存在 collection 的 search/query 报 'released')。
        self._client.load_collection(collection)

    def close(self) -> None:
        self._client.close()

    def upsert(self, records: list[dict]) -> None:
        if not records:
            return
        self._client.insert(self._collection, records)

    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        expr = self._build_expr(filter)
        res = self._client.search(
            self._collection, data=[vector], limit=k, filter=expr or "",
            output_fields=list(self._scalars.keys()),
        )
        return [hit["entity"] for hit in res[0]]

    @staticmethod
    def _build_expr(filter: dict | None) -> str | None:
        if not filter:
            return None
        parts = []
        for key, val in filter.items():
            if isinstance(val, (list, tuple)):
                parts.append(f"{key} in {list(val)}")
            elif isinstance(val, str):
                parts.append(f"{key} == {val!r}")
            else:
                parts.append(f"{key} == {val}")
        return " and ".join(parts)

    def list_by(self, filter: dict) -> list[dict]:
        """按任意 scalar 过滤拉全量行(给 BM25 稀疏检索喂语料)。命中上限即告警(可能截断)。"""
        rows = self._client.query(
            self._collection, filter=self._build_expr(filter) or "",
            output_fields=list(self._scalars.keys()), limit=_LIST_LIMIT,
        )
        if len(rows) == _LIST_LIMIT:
            _log.warning("list_by(%s) 命中 %d 行上限,可能被截断。", filter, _LIST_LIMIT)
        return rows

    def delete(self, filter: dict) -> None:
        self._client.delete(self._collection, filter=self._build_expr(filter) or "")

    # —— 项目 chunks 专用便捷封装(保持既有调用方不变) ——
    def delete_by_record(self, ingest_record_id: int) -> None:
        self.delete({"ingest_record_id": ingest_record_id})

    def delete_by_project(self, project_id: int) -> None:
        self.delete({"project_id": project_id})

    def list_by_project(self, project_id: int) -> list[dict]:
        return self.list_by({"project_id": project_id})
```

- [ ] **Step 4: 在 ABC 加抽象方法** — `backend/epictrace/interfaces/vector_store.py`,在 `list_by_project` 后加:

```python
    @abstractmethod
    def list_by(self, filter: dict) -> list[dict]: ...

    @abstractmethod
    def delete(self, filter: dict) -> None: ...
```

- [ ] **Step 5: FakeVectorStore 加 `list_by`/`delete`** — `backend/tests/fakes.py` 的 `FakeVectorStore` 加(复用其 query 的过滤语义):

```python
    def _match(self, r: dict, filter: dict) -> bool:
        for key, val in filter.items():
            rv = r.get(key)
            if isinstance(val, (list, tuple)):
                if rv not in val:
                    return False
            elif rv != val:
                return False
        return True

    def list_by(self, filter: dict) -> list[dict]:
        return [r for r in self.records if self._match(r, filter)]

    def delete(self, filter: dict) -> None:
        self.records = [r for r in self.records if not self._match(r, filter)]
```
(并把现有 `query` 内联的过滤改为复用 `self._match(r, filter)`,以及把 `delete_by_record`/`delete_by_project` 改为调用 `self.delete({...})` 以记录到 `deleted_records`/`deleted_projects` —— 保留这两个列表的 append 以兼容既有断言:)

```python
    def delete_by_record(self, ingest_record_id: int) -> None:
        self.deleted_records.append(ingest_record_id)
        self.delete({"ingest_record_id": ingest_record_id})

    def delete_by_project(self, project_id: int) -> None:
        self.deleted_projects.append(project_id)
        self.delete({"project_id": project_id})
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_vectorstore_collections.py tests/test_vectorstore_milvus.py tests/test_vectorstore_in_filter.py tests/test_vectorstore_list.py tests/test_vectorstore_reload.py -q`
Expected: PASS（既有向量库测试全绿）

- [ ] **Step 7: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/vectorstore/milvus_lite.py backend/epictrace/interfaces/vector_store.py backend/tests/fakes.py backend/tests/test_vectorstore_collections.py
git commit -m "$(printf 'feat(vectorstore): parametrize collection/scalars + generic delete/list_by\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: `ReferenceService` 大外部文件 → 索引进临时集合(`deferred`→`indexed`)+ 解挂清理

**Files:**
- Modify: `backend/epictrace/services/references.py`
- Test: `backend/tests/test_references_indexing.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_references_indexing.py
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Project
from epictrace.services.references import ReferenceService
from tests.fakes import FakeEmbedder, FakeVectorStore

TINY = 10  # 预算极小 → 大文件


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def _w(tmp_path, name, text):
    f = tmp_path / name; f.write_text(text, encoding="utf-8"); return str(f)


def test_large_external_is_indexed_into_attachment_store(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "页表把虚拟地址映射到物理地址。" * 50), TINY)
    assert ref["mode"] == "indexed"
    # 切块后入临时集合,带 conversation_id + reference_id + 偏移
    recs = store.list_by({"conversation_id": cid, "reference_id": ref["id"]})
    assert len(recs) >= 1
    r0 = recs[0]
    assert r0["conversation_id"] == cid and r0["reference_id"] == ref["id"]
    assert "char_start" in r0 and "char_end" in r0 and r0["source_type"] == "attachment"


def test_indexing_failure_falls_back_to_deferred(tmp_path: Path):
    db, cid = _setup(tmp_path)
    class _BoomEmbedder(FakeEmbedder):
        def embed(self, texts): raise RuntimeError("embed boom")
    svc = ReferenceService(db, embedder=_BoomEmbedder(), attachment_store=FakeVectorStore())
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "内容" * 100), TINY)
    assert ref["mode"] == "deferred"  # 索引失败 → 回退,不阻塞


def test_detach_cleans_attachment_vectors(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "内容内容" * 100), TINY)
    assert store.list_by({"reference_id": ref["id"]})
    svc.detach(cid, ref["id"])
    assert store.list_by({"reference_id": ref["id"]}) == []


def test_small_external_still_fulltext_no_indexing(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "s.md", "短"), context_window=1_000_000)
    assert ref["mode"] == "fulltext" and store.records == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_references_indexing.py -q`
Expected: FAIL（`ReferenceService` 不接受 embedder/attachment_store;大文件仍是 deferred）

- [ ] **Step 3: 实现** — 改 `backend/epictrace/services/references.py`:

(a) 顶部加导入:
```python
from epictrace.indexing.chunker import chunk_text
```

(b) `__init__` 增可选依赖:
```python
    def __init__(self, db: Database, embedder=None, attachment_store=None) -> None:
        self._db = db
        self._embedder = embedder
        self._attachment_store = attachment_store
```

(c) `add_external` 的大文件分支:能索引就索引(`indexed`),否则/失败 `deferred`。替换原 `mode = ...` 到 `return` 段:
```python
        used = self._used_fulltext_tokens(conversation_id)
        if fits_fulltext(text, context_window, used):
            mode = "fulltext"
        else:
            mode = "deferred"  # 默认;若能成功索引则改 indexed
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
```

(d) 新私有方法(放在 `detach` 之前):
```python
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
```

(e) `detach` 加临时向量清理:
```python
    def detach(self, conversation_id: int, reference_id: int) -> None:
        with self._db.session() as s:
            ref = s.get(ConversationReference, reference_id)
            if ref is not None and ref.conversation_id == conversation_id:
                ref.detached = True
        if self._attachment_store is not None:
            self._attachment_store.delete({"reference_id": reference_id})
```

(更新类 docstring:外部大文件现在「索引进会话级临时集合(indexed)」。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_references_indexing.py tests/test_references_service.py tests/test_references_robustness.py -q`
Expected: PASS（既有 references 测试仍绿——它们构造 `ReferenceService(db)`,embedder/store 为 None,大文件保持 deferred,与原断言一致）

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/services/references.py backend/tests/test_references_indexing.py
git commit -m "$(printf 'feat(references): index large external attachments into ephemeral store (deferred->indexed)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: `AttachmentRetriever`(临时集合上的 hybrid 检索)

**Files:**
- Create: `backend/epictrace/retrieval/attachment.py`
- Test: `backend/tests/test_attachment_retrieve.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_attachment_retrieve.py
from epictrace.retrieval.attachment import AttachmentRetriever
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _seed(store, cid):
    for rid, text in [(10, "页表 映射 地址"), (10, "缓存 一致性"), (20, "别的引用 页表")]:
        store.upsert([{"vector": [0.0] * 1024, "text": text, "conversation_id": cid,
                       "reference_id": rid, "char_start": 0, "char_end": len(text),
                       "source_type": "attachment", "embed_model_id": "fake"}])


def test_retrieve_scoped_to_conversation_and_references():
    store = FakeVectorStore(); _seed(store, cid=1)
    r = AttachmentRetriever(FakeEmbedder(), store, FakeReranker())
    hits = r.retrieve(conversation_id=1, reference_ids=[10], query="页表", k=6)
    assert hits and all(h.source_kind == "attachment" for h in hits)
    assert all(h.reference_id == 10 for h in hits)          # 只命中 ref 10、本会话
    assert all(h.char_start is not None for h in hits)      # 带偏移(供精确跳回)


def test_empty_when_no_reference_ids():
    store = FakeVectorStore(); _seed(store, cid=1)
    r = AttachmentRetriever(FakeEmbedder(), store, FakeReranker())
    assert r.retrieve(conversation_id=1, reference_ids=[], query="页表", k=6) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_attachment_retrieve.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现** — 创建 `backend/epictrace/retrieval/attachment.py`:

```python
from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from epictrace.retrieval.fuse import rrf_fuse
from epictrace.retrieval.types import RetrievedChunk


def _row_to_chunk(row: dict, score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        text=row["text"], ingest_record_id=0, project_id=0,
        char_start=row["char_start"], char_end=row["char_end"],
        source_type="attachment", score=score,
        source_kind="attachment", reference_id=row["reference_id"],
    )


def _tok(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


class AttachmentRetriever:
    """对会话级临时集合做 dense+sparse→RRF→rerank,按 conversation_id + reference_id 过滤。
    与项目 HybridRetriever 同形,但作用于附件向量、产出 source_kind=attachment 的 chunk。"""

    def __init__(self, embedder, store, reranker) -> None:
        self._embedder = embedder
        self._store = store
        self._reranker = reranker

    def retrieve(self, *, conversation_id: int, reference_ids: list[int], query: str,
                 k: int = 6, dense_n: int = 30, fuse_m: int = 20) -> list[RetrievedChunk]:
        if not reference_ids:
            return []
        flt = {"conversation_id": conversation_id, "reference_id": list(reference_ids)}
        vec = self._embedder.embed([query])[0]
        dense_rows = self._store.query(vec, filter=flt, k=dense_n)
        dense = [_row_to_chunk(r, score=1.0 / (i + 1)) for i, r in enumerate(dense_rows)]
        # sparse:对该过滤集的全量行跑 BM25
        rows = self._store.list_by(flt)
        sparse: list[RetrievedChunk] = []
        if rows:
            bm25 = BM25Okapi([_tok(r["text"]) for r in rows])
            scores = bm25.get_scores(_tok(query))
            ranked = sorted(zip(rows, scores), key=lambda rs: rs[1], reverse=True)[:dense_n]
            sparse = [_row_to_chunk(r, score=float(s)) for r, s in ranked if s > 0]
        fused = rrf_fuse([dense, sparse], k=fuse_m)
        if not fused:
            return []
        return self._reranker.rerank(query, fused, top_k=k)
```

注:`RetrievedChunk.key()` = `(ingest_record_id, char_start, char_end)`,附件 chunk 的 `ingest_record_id` 恒为 0,故 RRF 去重靠 `(0, char_start, char_end)`——同一引用内 chunk 偏移唯一,够用。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_attachment_retrieve.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/retrieval/attachment.py backend/tests/test_attachment_retrieve.py
git commit -m "$(printf 'feat(retrieval): AttachmentRetriever over ephemeral attachment vectors\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: `ChatService` 合并附件检索

**Files:**
- Modify: `backend/epictrace/services/chat.py`
- Test: `backend/tests/test_chat_attachment_rag.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_attachment_rag.py
import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, ConversationReference, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeLLM


class _EmptyRetriever:
    def retrieve(self, *, project_id, query, **kwargs):
        return []


class _FakeAttachmentRetriever:
    def __init__(self): self.calls = []
    def retrieve(self, *, conversation_id, reference_ids, query, k=6):
        self.calls.append((conversation_id, tuple(reference_ids)))
        return [RetrievedChunk(text="附件相关片段", ingest_record_id=0, project_id=0,
                               char_start=5, char_end=11, source_type="attachment",
                               source_kind="attachment", reference_id=reference_ids[0])]


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def _add_indexed_ref(db, cid) -> int:
    with db.session() as s:
        ref = ConversationReference(conversation_id=cid, kind="external", display_name="big.md",
                                    source_path="/x/big.md", extracted_text="…", text_chars=1,
                                    mode="indexed")
        s.add(ref); s.flush(); return ref.id


class _Refs:
    def __init__(self, db): self._db = db
    def list_active(self, cid):
        from epictrace.services.references import ReferenceService
        return ReferenceService(self._db).list_active(cid)


def test_indexed_ref_pulls_attachment_chunks_and_cites_them(tmp_path: Path):
    db, cid = _setup(tmp_path)
    rid = _add_indexed_ref(db, cid)
    attach = _FakeAttachmentRetriever()
    svc = ChatService(db, FakeLLM(route="retrieve", grade="sufficient", answer="见附件[1]。"),
                      _EmptyRetriever(), references=_Refs(db), attachment_retriever=attach)
    events = list(svc.stream_answer(cid, "这个文件讲了什么"))
    assert attach.calls == [(cid, (rid,))]
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["source_kind"] == "attachment" and cites[0]["reference_id"] == rid
    assert cites[0]["char_start"] == 5 and cites[0]["char_end"] == 11   # 片段级偏移


def test_no_attachment_retriever_or_no_indexed_ref_is_noop(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(route="direct", answer="你好"), _EmptyRetriever())  # 默认 None
    events = list(svc.stream_answer(cid, "你好"))
    assert json.loads(next(e for e in events if e["event"] == "citations")["data"]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_chat_attachment_rag.py -q`
Expected: FAIL（`ChatService` 不接受 `attachment_retriever`,不拉附件)

- [ ] **Step 3: 实现** — 改 `backend/epictrace/services/chat.py`:

(a) `__init__` 增可选 `attachment_retriever`:
```python
    def __init__(self, db: Database, llm, retriever, references=None,
                 attachment_retriever=None) -> None:
        self._db = db
        self._llm = llm
        self._retriever = retriever
        self._references = references
        self._attachment_retriever = attachment_retriever
```

(b) `_run_turn` 内,把 refs 拆分时加上 `indexed` 外部引用,并在合并 chunks 时追加附件检索结果。替换原 `fulltext_refs`/`focus_ids`/`chunks` 三行:
```python
            refs = self._references.list_active(conversation_id) if self._references else []
            fulltext_refs = [r for r in refs if r["mode"] == "fulltext"]
            focus_ids = [r["ingest_record_id"] for r in refs
                         if r["mode"] == "focus" and r.get("ingest_record_id")]
            indexed_ext_ids = [r["id"] for r in refs
                               if r["mode"] == "indexed" and r["kind"] == "external"]
            graph = build_rag_graph(self._llm, self._retriever)
            state = graph.invoke({"project_id": self._project_id(conversation_id),
                                  "question": question, "query": question, "history": history,
                                  "iterations": 0, "focus_ids": focus_ids})
            # 全文引用恒在最前;其后接项目检索;再接附件临时 RAG 检索(有活跃 indexed 引用时)。
            attach_chunks = []
            if indexed_ext_ids and self._attachment_retriever is not None:
                attach_chunks = self._attachment_retriever.retrieve(
                    conversation_id=conversation_id, reference_ids=indexed_ext_ids, query=question)
            chunks = [_ref_chunk(r) for r in fulltext_refs] + state.get("chunks", []) + attach_chunks
```
(其余不变——`build_citations(answer, chunks)` 已带 `source_kind`/`reference_id`。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_chat_attachment_rag.py tests/test_chat_service.py tests/test_chat_references.py -q`
Expected: PASS（既有 ChatService 测试仍绿——attachment_retriever 默认 None)

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/services/chat.py backend/tests/test_chat_attachment_rag.py
git commit -m "$(printf 'feat(chat): merge ephemeral attachment retrieval into context\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: API 接线 + 删对话清理

**Files:**
- Modify: `backend/epictrace/api/deps.py`、`backend/epictrace/api/app.py`、`backend/epictrace/api/routers/references.py`、`backend/epictrace/api/routers/conversations.py`
- Test: `backend/tests/test_api_attachment_cleanup.py`

- [ ] **Step 1: 写失败测试**(用注入了 Fake 的 client——见实现里 app.state 槽)

```python
# backend/tests/test_api_attachment_cleanup.py
from pathlib import Path

from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    store = FakeVectorStore()
    app = create_app(db=db, embedder=FakeEmbedder(), reranker=FakeReranker())
    app.state.attachment_store = store  # 注入临时附件 store(避免起真 Milvus/模型)
    return TestClient(app), store


def _proj_conv(client, tmp_path):
    folder = tmp_path / "p"; folder.mkdir()
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/conversations", json={"title": "t"}).json()["id"]
    return cid


def test_large_external_indexed_via_api_then_cleaned_on_detach(client_and_store_unused=None, tmp_path: Path = None):
    client, store = _client(tmp_path)
    # 配一个 context_window 极小的 profile → 文件判大
    client.post("/api/settings/profiles", json={"name": "A", "base_url": "http://x",
                "api_key": "k", "model": "m", "context_window": 8})
    cid = _proj_conv(client, tmp_path)
    f = tmp_path / "big.md"; f.write_text("页表把虚拟地址映射到物理地址。" * 30, encoding="utf-8")
    r = client.post(f"/api/conversations/{cid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 201 and r.json()["mode"] == "indexed"
    rid = r.json()["id"]
    assert store.list_by({"reference_id": rid})              # 已索引进临时集合
    assert client.delete(f"/api/conversations/{cid}/references/{rid}").status_code == 204
    assert store.list_by({"reference_id": rid}) == []        # 解挂即清


def test_delete_conversation_cleans_attachment_vectors(tmp_path: Path):
    client, store = _client(tmp_path)
    client.post("/api/settings/profiles", json={"name": "A", "base_url": "http://x",
                "api_key": "k", "model": "m", "context_window": 8})
    cid = _proj_conv(client, tmp_path)
    f = tmp_path / "big.md"; f.write_text("内容内容内容。" * 50, encoding="utf-8")
    rid = client.post(f"/api/conversations/{cid}/references",
                      json={"kind": "external", "source_path": str(f)}).json()["id"]
    assert store.list_by({"conversation_id": cid})
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert store.list_by({"conversation_id": cid}) == []
```

(注:pytest 会把 `tmp_path` 注入;第一个测试的多余形参 `client_and_store_unused=None` 仅为放 `tmp_path` 在后,可删,直接用 `def test_...(tmp_path):`。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_api_attachment_cleanup.py -q`
Expected: FAIL（路由没注入 embedder/attachment store;删对话不清向量;`add_external` 拿不到 store → 大文件停在 deferred)

- [ ] **Step 3: 实现**

(a) `api/app.py` 加 `app.state.attachment_store` 槽 + 接受注入。在 `app.state.vector_store = vector_store` 一行附近加:
```python
    app.state.attachment_store = None  # 会话级临时附件向量(注入或延迟构造,见 deps.get_attachment_store)
```
并给 `create_app` 签名加可选参 `attachment_store=None`,赋 `app.state.attachment_store = attachment_store`。

(b) `api/deps.py` 加:
```python
def get_attachment_store(request: Request):
    """会话级临时附件向量库(attachment_chunks collection,与项目 chunks 同一 db、不同 collection)。
    与 get_vector_store 同样保证"先暖 embedder+reranker 再起 Milvus"(macOS fork 段错误)。"""
    store = getattr(request.app.state, "attachment_store", None)
    if store is not None:
        return store
    with _vector_store_lock:
        store = request.app.state.attachment_store
        if store is None:
            get_embedder(request).warmup()
            get_reranker(request).warmup()
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore, _ATTACHMENT_SCALARS

            store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024,
                                    collection="attachment_chunks", scalars=_ATTACHMENT_SCALARS)
            request.app.state.attachment_store = store
    return store
```

(c) `api/routers/references.py` — `add_reference` 给 `ReferenceService` 注入 embedder + attachment store;`detach_reference` 用已就绪的临时 store 清理。改这两个路由:
```python
from epictrace.api.deps import get_db, get_embedder, get_attachment_store
...
@router.post(...)  # add_reference
def add_reference(cid, payload, request, db=Depends(get_db)):
    _require_conv(db, cid)
    svc = ReferenceService(db, embedder=get_embedder(request),
                           attachment_store=get_attachment_store(request))
    cw = _context_window(request)
    try:
        ...（其余不变）

@router.delete(...)  # detach_reference
def detach_reference(cid, rid, request: Request, db=Depends(get_db)):
    _require_conv(db, cid)
    # 仅用"已构造"的临时 store 清理(未构造说明本会话没建过附件向量;跨会话遗留无害、由查询过滤掉)。
    store = getattr(request.app.state, "attachment_store", None)
    ReferenceService(db, attachment_store=store).detach(cid, rid)
```
(`detach_reference` 签名加 `request: Request`。)

(d) `api/routers/conversations.py`:
- 三处 `ChatService(db, llm, get_retriever(request), references=ReferenceService(db))` → 注入 attachment retriever 与带依赖的 ReferenceService。顶部加:
```python
from epictrace.api.deps import get_db, get_llm, get_retriever, get_embedder, get_reranker, get_attachment_store
from epictrace.retrieval.attachment import AttachmentRetriever
```
  并加一个小工厂:
```python
def _chat_service(request: Request, db: Database) -> ChatService:
    llm = get_llm(request)
    if llm is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "对话模型未配置:请在设置里填写 OpenAI-Compatible 端点")
    attach = AttachmentRetriever(get_embedder(request), get_attachment_store(request),
                                 get_reranker(request))
    refs = ReferenceService(db, embedder=get_embedder(request),
                            attachment_store=get_attachment_store(request))
    return ChatService(db, llm, get_retriever(request), references=refs, attachment_retriever=attach)
```
  send_message / edit_message / regenerate_message 改为先 `_require_conv` 后 `svc = _chat_service(request, db)`(把原先各自的 llm-None 检查并入工厂)。**注意**保留原有的 message/编辑校验逻辑;仅替换 `ChatService(...)` 构造与 llm 检查。
- `delete_conversation` 加临时向量清理:
```python
@router.delete("/conversations/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(cid: int, request: Request, db: Database = Depends(get_db)):
    with db.session() as s:
        c = s.get(Conversation, cid)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        s.delete(c)  # messages/references 经 cascade 删除
    store = getattr(request.app.state, "attachment_store", None)
    if store is not None:
        store.delete({"conversation_id": cid})   # 清该会话的临时附件向量
```
(`delete_conversation` 签名加 `request: Request`。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/william/Desktop/EpicTrace/backend && .venv/bin/pytest tests/test_api_attachment_cleanup.py tests/test_api_references.py tests/test_api_chat.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/api/deps.py backend/epictrace/api/app.py backend/epictrace/api/routers/references.py backend/epictrace/api/routers/conversations.py backend/tests/test_api_attachment_cleanup.py
git commit -m "$(printf 'feat(api): wire attachment store/retriever; index on attach, clean on detach/delete\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: 前端 `indexed` 模式标签 + 索引中提示

**Files:**
- Modify: `frontend/src/lib/api.ts`、`frontend/src/components/ReferencePanel.tsx`、`frontend/src/views/ProjectsConversationView.tsx`

- [ ] **Step 1: 类型 + 标签**

`lib/api.ts` 的 `ConversationReference.mode` 联合加 `"indexed"`:
```typescript
  mode: "fulltext" | "focus" | "indexed" | "deferred";
```
`components/ReferencePanel.tsx` 的 `MODE_LABEL` 加 `indexed`(并把 `deferred` 文案改成回退态):
```tsx
const MODE_LABEL: Record<ConversationReference["mode"], string> = {
  fulltext: "全文已载入",
  focus: "已索引聚焦",
  indexed: "已索引检索",
  deferred: "未能索引",
};
```

- [ ] **Step 2: 索引中提示**

`views/ProjectsConversationView.tsx` 的 `attachExternal`:挂载期间(尤其大文件要 embed)给个轻量提示。在 `attachExternal` 开头/结尾包一个状态(复用已有 `attachError` 同位置渲染,或加一个 `attaching` 布尔):
```tsx
const [attaching, setAttaching] = useState(false);
const attachExternal = async (paths: string[]) => {
  const cid = await ensureConversation();
  setAttaching(true);
  const failures: string[] = [];
  for (const p of paths) {
    try { await api.addExternalReference(cid, p); }
    catch (e) { const name = p.split("/").pop() || p;
      failures.push(`${name}(${e instanceof Error ? e.message : String(e)})`); }
  }
  setAttaching(false);
  setAttachError(failures.length ? `部分文件未能添加:${failures.join("；")}` : null);
  await refreshRefs(cid);
};
```
在引用侧栏顶部或 attachError 上方,`attaching` 为真时渲染一行:
```tsx
{attaching && (
  <p className="mx-auto w-full max-w-2xl px-6 text-xs text-muted-foreground">正在索引附件…</p>
)}
```

- [ ] **Step 3: 验证 + 提交**

Run: `cd /Users/william/Desktop/EpicTrace/frontend && npm run build`
Expected: build 成功(tsc + vite,无类型错误)

```bash
cd /Users/william/Desktop/EpicTrace
git add frontend/src/lib/api.ts frontend/src/components/ReferencePanel.tsx frontend/src/views/ProjectsConversationView.tsx
git commit -m "$(printf 'feat(web): indexed mode label + indexing-in-progress hint\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## 收尾
- [ ] **全量后端**: `cd backend && .venv/bin/pytest -q`(期望全绿;slow 真模型默认跳过)
- [ ] **前端构建**: `cd frontend && npm run build`
- [ ] **代号扫描**: `grep -rniE "conflux" docs/superpowers backend/epictrace frontend/src shell`(应无)
- [ ] **收尾**: superpowers:finishing-a-development-branch 合并/提 PR。

---

## Self-Review

**Spec 覆盖:** `deferred`→`indexed`(T2)、`attachment_chunks` 临时集合(T1)、挂载时切块+embed(T2)、`get_attachment_store` 暖机顺序(T5)、检索轮 `AttachmentRetriever`(T3)合并(T4)、chunk 级 attachment 引用(T3/T4,复用 Plan 4 的 source_kind/reference_id + SourceViewer)、解挂/删对话清理(T2/T5)、索引失败回退 deferred(T2)、前端 indexed 标签 + 索引中(T6)、macOS 段错误(T5 暖机)。**均有任务。**

**类型一致性:** `MilvusLiteStore(collection,scalars)` + `list_by`/`delete`(T1)在 T2/T3/T5 一致;`ReferenceService(db, embedder, attachment_store)`(T2)在 T5 一致;`AttachmentRetriever.retrieve(*, conversation_id, reference_ids, query, k)`(T3)在 T4/T5 一致;`ChatService(..., attachment_retriever=)`(T4)在 T5 一致;`mode` 取值 `fulltext|focus|indexed|deferred` 前后端一致(T2/T6)。

**无占位:** 每步含真实测试 + 实现代码;前端以 build 为门。

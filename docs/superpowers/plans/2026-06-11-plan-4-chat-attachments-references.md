# Plan 4 — 现代对话体验 + 对话引用(外部附件全文 + 内部文件聚焦)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让对话能引用文件——外部拖/选进来的临时文件(全文进上下文,大文件留给 Plan 5)和项目内已索引文件(全文或聚焦检索),引用可跳回原文,外部附件不入库、不向量化;并把对话外壳打磨到现代桌面 LLM 手感。

**Architecture:** 后端新增会话级 `conversation_references` 表 + `ReferenceService`(提取/缓存/size-gate);`ChatService` 组装时把"全文引用"作为 chunk 注入【资料】、把"聚焦引用"作为 `ingest_record_id IN {…}` 过滤透传进现有 RAG 图;引用 chunk 带 `source_kind`(project|attachment)以驱动来源跳回。前端加折叠两栏「本对话引用」面板 + Composer 附件入口(拖拽/粘贴/选择) + 流式 markdown 缓冲。size-gate 阈值随 LLM Profile 的 `context_window` 动态算。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy(SQLite) / LangGraph / Milvus Lite / FlagEmbedding;React 19 / Vite / Tailwind v4 / shadcn;pytest(后端 TDD,Fake LLM/Embedder/Reranker/VectorStore);前端以 `npm run build` 为验收门。

**设计来源:** `docs/superpowers/specs/2026-06-11-epictrace-plan-4-chat-attachments-references-design.md`(决策见 `docs/decisions/2026-06-11-attachment-phase-plan-4nd-tech-choices.md`)。

**约定:** 提交用 ep1sode-33 git 身份,commit message 结尾附 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` 行(下方各 commit 步骤只写主题,统一加此 trailer)。后端命令在 `backend/` 下用项目 venv 跑(`cd backend && .venv/bin/pytest ...`)。前端命令在 `frontend/` 下跑。**先开 feature 分支再动手**(收尾用 superpowers:finishing-a-development-branch)。

---

## File Structure

**后端 — 新建**
- `backend/epictrace/services/budget.py` — 纯函数:token 估算 + 全文预算 + 是否放得下。
- `backend/epictrace/services/references.py` — `ReferenceService`:引用 CRUD + 提取 + size-gate。
- `backend/epictrace/api/routers/references.py` — 引用 REST(增/列/解挂)。
- 测试:`tests/test_budget.py`、`tests/test_references_service.py`、`tests/test_api_references.py`、`tests/test_vectorstore_in_filter.py`、`tests/test_focus_retrieval.py`、`tests/test_chat_references.py`、`tests/test_citations_source_kind.py`、`tests/test_source_attachment.py`。

**后端 — 修改**
- `models.py`(`ConversationReference` + 关系)、`schemas.py`(Reference*/Profile context_window)、`services/settings.py`(context_window)、`retrieval/types.py`(source_kind/reference_id)、`agent/citations.py`、`vectorstore/milvus_lite.py`(IN filter)、`retrieval/dense.py`/`sparse.py`/`pipeline.py`(focus)、`agent/state.py`/`graph.py`(focus_ids)、`services/chat.py`(引用组装)、`services/source.py`(attachment 分支)、`api/routers/conversations.py`(注入 references)、`api/routers/settings.py`、`api/routers/source.py`、`api/app.py`(挂 references 路由)、`shell/run.py`(多选)。

**前端 — 新建**
- `frontend/src/components/ReferencePanel.tsx` — 折叠两栏引用面板。

**前端 — 修改**
- `lib/api.ts`(Reference 类型/CRUD/getAttachmentSource/context_window/pickFiles)、`lib/pickers.ts`、`components/Composer.tsx`、`components/SourceViewer.tsx`、`components/SettingsModal.tsx`、`components/AssistantMarkdown.tsx`、`views/ProjectsConversationView.tsx`。

---

# Phase 1 — 后端数据与预算

### Task 1: `ConversationReference` 模型

**Files:**
- Modify: `backend/epictrace/models.py`
- Test: `backend/tests/test_models_reference.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_models_reference.py
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, ConversationReference, Project


def _db(tmp_path: Path) -> Database:
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all(); return db


def test_reference_persists_and_cascades_with_conversation(tmp_path: Path):
    db = _db(tmp_path)
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        s.add(ConversationReference(
            conversation_id=c.id, kind="external", display_name="报告.pdf",
            source_path="/x/报告.pdf", extracted_text="正文", text_chars=2, mode="fulltext",
        ))
        cid = c.id
    with db.session() as s:
        refs = s.query(ConversationReference).filter_by(conversation_id=cid).all()
        assert len(refs) == 1 and refs[0].detached is False and refs[0].mode == "fulltext"
        s.delete(s.get(Conversation, cid))            # 删会话级联删引用
    with db.session() as s:
        assert s.query(ConversationReference).count() == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_models_reference.py -q`
Expected: FAIL（`ImportError: cannot import name 'ConversationReference'`）

- [ ] **Step 3: 加模型**

在 `backend/epictrace/models.py` 末尾追加(并给 `Conversation` 加 `references` 关系):

```python
class ConversationReference(Base):
    __tablename__ = "conversation_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))                 # external | internal
    display_name: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str | None] = mapped_column(String(1024), default=None)   # external
    ingest_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingest_records.id"), default=None                            # internal
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)        # external 缓存
    text_chars: Mapped[int] = mapped_column(default=0)
    mode: Mapped[str] = mapped_column(String(16))                # fulltext | focus | deferred
    detached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="references")
```

在 `Conversation` 类里(`messages` 关系之后)加:

```python
    references: Mapped[list["ConversationReference"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="ConversationReference.id",
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_models_reference.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/models.py backend/tests/test_models_reference.py
git commit -m "feat(references): add ConversationReference model"
```

---

### Task 2: size-gate 预算纯函数

**Files:**
- Create: `backend/epictrace/services/budget.py`
- Test: `backend/tests/test_budget.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_budget.py
from epictrace.services.budget import estimate_tokens, fulltext_budget, fits_fulltext


def test_estimate_tokens_is_conservative_char_based():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 2          # 4 字符 / 2 ≈ 2 token(向上取整)
    assert estimate_tokens("abcde") == 3


def test_fulltext_budget_is_half_window():
    assert fulltext_budget(32768) == 16384
    assert fulltext_budget(0) == 0


def test_fits_fulltext_respects_budget_and_used():
    win = 1000                                   # 预算 = 500 token ≈ 1000 字符
    assert fits_fulltext("a" * 800, win) is True            # ~400 token ≤ 500
    assert fits_fulltext("a" * 1200, win) is False          # ~600 token > 500
    # 已用预算累加:再来 ~400 token 会超
    assert fits_fulltext("a" * 400, win, used_tokens=400) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_budget.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# backend/epictrace/services/budget.py
from __future__ import annotations

import math

# 保守的中英混合估算:宁可高估 token 数(少塞文件)。约 2 字符/token。
CHARS_PER_TOKEN = 2.0
# 全文注入最多占模型上下文窗口的一半,余下留给系统提示 / 历史 / 项目 RAG / 答案头寸。
FULLTEXT_FRACTION = 0.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def fulltext_budget(context_window: int) -> int:
    return max(0, int(context_window * FULLTEXT_FRACTION))


def fits_fulltext(text: str, context_window: int, used_tokens: int = 0) -> bool:
    return used_tokens + estimate_tokens(text) <= fulltext_budget(context_window)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_budget.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/budget.py backend/tests/test_budget.py
git commit -m "feat(references): add size-gate budget helpers"
```

---

### Task 3: LLM Profile 的 `context_window`

**Files:**
- Modify: `backend/epictrace/services/settings.py`
- Test: `backend/tests/test_settings.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_settings.py` 末尾追加:

```python
def test_context_window_defaults_and_roundtrips(tmp_path: Path):
    svc = _svc(tmp_path)
    pid = svc.create_profile(name="A", base_url="http://x", api_key="k", model="m")
    # 未传 → 默认 32768
    assert svc.get_active_profile()["context_window"] == 32768
    assert svc.get_chat_llm().context_window == 32768
    assert svc.public_view()["profiles"][0]["context_window"] == 32768
    # 可更新
    svc.update_profile(pid, context_window=128000)
    assert svc.get_chat_llm().context_window == 128000


def test_create_profile_accepts_explicit_context_window(tmp_path: Path):
    svc = _svc(tmp_path)
    svc.create_profile(name="A", base_url="http://x", api_key="k", model="m", context_window=8192)
    assert svc.get_chat_llm().context_window == 8192
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_settings.py -q`
Expected: FAIL（`ChatLLMSettings` 无 `context_window` / `create_profile` 不接受该参数）

- [ ] **Step 3: 实现**

在 `services/settings.py`:

(a) `ChatLLMSettings` 加字段:
```python
@dataclass
class ChatLLMSettings:
    base_url: str
    api_key: str
    model: str
    context_window: int = 32768
```

(b) `get_chat_llm` 读取:
```python
        return ChatLLMSettings(
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
            model=p.get("model", ""),
            context_window=int(p.get("context_window", 32768)),
        )
```

(c) `create_profile` 增参 + 落盘:
```python
    def create_profile(self, name: str, base_url: str, api_key: str, model: str,
                       context_window: int = 32768) -> str:
        data = self._load()
        pid = _short_id()
        existing_ids = {p.get("id") for p in data["profiles"]}
        while pid in existing_ids:
            pid = _short_id()
        data["profiles"].append({
            "id": pid, "name": name, "base_url": base_url, "api_key": api_key,
            "model": model, "context_window": context_window,
        })
        if data["active_profile_id"] is None:
            data["active_profile_id"] = pid
        self._write(data)
        return pid
```

(d) `update_profile` 增参(在签名加 `context_window: int | None = None`,并在循环里):
```python
                if context_window is not None:
                    p["context_window"] = context_window
```

(e) `public_view` 的 profile 字典加 `"context_window": int(p.get("context_window", 32768))`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_settings.py -q`
Expected: PASS（含原有用例)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/settings.py backend/tests/test_settings.py
git commit -m "feat(settings): add per-profile context_window"
```

---

# Phase 2 — 后端检索/上下文管线

### Task 4: `RetrievedChunk` 加 `source_kind`/`reference_id` + 引用透传

**Files:**
- Modify: `backend/epictrace/retrieval/types.py`、`backend/epictrace/agent/citations.py`
- Test: `backend/tests/test_citations_source_kind.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_citations_source_kind.py
from epictrace.agent.citations import build_citations
from epictrace.retrieval.types import RetrievedChunk


def test_chunk_defaults_source_kind_project():
    c = RetrievedChunk(text="x", ingest_record_id=1, project_id=1,
                       char_start=0, char_end=1, source_type="folder_scan")
    assert c.source_kind == "project" and c.reference_id is None


def test_build_citations_includes_source_kind_and_reference_id():
    chunks = [
        RetrievedChunk(text="项目片段", ingest_record_id=7, project_id=1,
                       char_start=0, char_end=4, source_type="folder_scan"),
        RetrievedChunk(text="附件全文", ingest_record_id=0, project_id=0,
                       char_start=0, char_end=4, source_type="attachment",
                       source_kind="attachment", reference_id=42),
    ]
    out = build_citations("用了[1]和[2]", chunks)
    assert out[0]["source_kind"] == "project" and out[0]["reference_id"] is None
    assert out[1]["source_kind"] == "attachment" and out[1]["reference_id"] == 42
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_citations_source_kind.py -q`
Expected: FAIL（无 `source_kind` 字段）

- [ ] **Step 3: 实现**

(a) `retrieval/types.py` — `RetrievedChunk` 加两个带默认值的字段(放在 `score` 之后,保持现有位置参数不破):
```python
    score: float = 0.0
    source_kind: str = "project"          # project | attachment
    reference_id: int | None = None
```

(b) `agent/citations.py` — `build_citations` 的 dict 追加两键:
```python
            out.append({
                "n": n, "ingest_record_id": c.ingest_record_id,
                "char_start": c.char_start, "char_end": c.char_end,
                "source_type": c.source_type,
                "source_kind": c.source_kind,
                "reference_id": c.reference_id,
                "snippet": c.text[:_SNIPPET],
            })
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_citations_source_kind.py tests/test_citations.py -q`
Expected: PASS（原 `test_citations.py` 仍绿）

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/retrieval/types.py backend/epictrace/agent/citations.py backend/tests/test_citations_source_kind.py
git commit -m "feat(retrieval): carry source_kind/reference_id on chunks and citations"
```

---

### Task 5: `VectorStore.query` 支持 IN 过滤

**Files:**
- Modify: `backend/epictrace/vectorstore/milvus_lite.py`
- Test: `backend/tests/test_vectorstore_in_filter.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_vectorstore_in_filter.py
from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _rec(rid: int, ing: int, text: str) -> dict:
    return {"vector": [0.1] * DIM, "text": text, "ingest_record_id": ing, "project_id": 1,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
            "embed_model_id": "fake"}


def test_query_filters_by_ingest_record_id_in_list(tmp_path: Path):
    s = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    s.upsert([_rec(1, 10, "甲"), _rec(2, 20, "乙"), _rec(3, 30, "丙")])
    hits = s.query([0.1] * DIM, filter={"project_id": 1, "ingest_record_id": [10, 30]}, k=10)
    got = sorted(h["ingest_record_id"] for h in hits)
    assert got == [10, 30]                       # 只命中聚焦的两个文件
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_vectorstore_in_filter.py -q`
Expected: FAIL（list 值被 `==` 拼成非法表达式 → 报错或命中 0）

- [ ] **Step 3: 实现**

在 `vectorstore/milvus_lite.py` 的 `query` 里,把表达式构造改为支持 list → `in`:

```python
    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        expr = self._build_expr(filter)
        res = self._client.search(
            _COLLECTION, data=[vector], limit=k, filter=expr or "",
            output_fields=list(_SCALARS.keys()),
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_vectorstore_in_filter.py tests/test_vectorstore_milvus.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/vectorstore/milvus_lite.py backend/tests/test_vectorstore_in_filter.py
git commit -m "feat(vectorstore): support IN filter on query"
```

---

### Task 6: dense/sparse/HybridRetriever 的聚焦参数

**Files:**
- Modify: `backend/epictrace/retrieval/dense.py`、`sparse.py`、`pipeline.py`
- Test: `backend/tests/test_focus_retrieval.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_focus_retrieval.py
from epictrace.retrieval.dense import dense_search
from epictrace.retrieval.sparse import sparse_search
from epictrace.retrieval.pipeline import HybridRetriever
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _rows(store):
    for ing, text in [(10, "页表 映射"), (20, "缓存 一致性"), (30, "页表 替换")]:
        store.upsert([{"vector": [0.0] * 1024, "text": text, "ingest_record_id": ing,
                       "project_id": 1, "char_start": 0, "char_end": len(text),
                       "source_type": "folder_scan"}])


def test_dense_search_scopes_to_focus_ids():
    store = FakeVectorStore(); _rows(store)
    hits = dense_search(FakeEmbedder(), store, project_id=1, query="页表", k=10,
                        ingest_record_ids=[10])
    assert {h.ingest_record_id for h in hits} == {10}


def test_sparse_search_scopes_to_focus_ids():
    store = FakeVectorStore(); _rows(store)
    hits = sparse_search(store, project_id=1, query="页表", k=10, ingest_record_ids=[30])
    assert all(h.ingest_record_id == 30 for h in hits)


def test_hybrid_retriever_threads_focus_ids():
    store = FakeVectorStore(); _rows(store)
    r = HybridRetriever(FakeEmbedder(), store, FakeReranker())
    hits = r.retrieve(project_id=1, query="页表", ingest_record_ids=[10, 30])
    assert {h.ingest_record_id for h in hits} <= {10, 30}
```

注:`FakeVectorStore.query` 现按 `==` 比较过滤,需让它也支持 list 值(见 Step 3(d))。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_focus_retrieval.py -q`
Expected: FAIL（函数不接受 `ingest_record_ids`)

- [ ] **Step 3: 实现**

(a) `retrieval/dense.py`:
```python
def dense_search(embedder: EmbeddingProvider, store: VectorStore, *, project_id: int,
                 query: str, k: int = 30, ingest_record_ids: list[int] | None = None) -> list[RetrievedChunk]:
    vec = embedder.embed([query])[0]
    flt: dict = {"project_id": project_id}
    if ingest_record_ids:
        flt["ingest_record_id"] = list(ingest_record_ids)
    rows = store.query(vec, filter=flt, k=k)
    return [RetrievedChunk.from_row(r, score=1.0 / (i + 1)) for i, r in enumerate(rows)]
```

(b) `retrieval/sparse.py`(在取到 rows 后按集合过滤):
```python
def sparse_search(store: VectorStore, *, project_id: int, query: str, k: int = 30,
                  ingest_record_ids: list[int] | None = None) -> list[RetrievedChunk]:
    rows = store.list_by_project(project_id)
    if ingest_record_ids:
        idset = set(ingest_record_ids)
        rows = [r for r in rows if r.get("ingest_record_id") in idset]
    if not rows:
        return []
    corpus = [_tok(r["text"]) for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tok(query))
    ranked = sorted(zip(rows, scores), key=lambda rs: rs[1], reverse=True)[:k]
    return [RetrievedChunk.from_row(r, score=float(s)) for r, s in ranked if s > 0]
```

(c) `retrieval/pipeline.py`:
```python
    def retrieve(self, *, project_id: int, query: str, k: int = 6,
                 dense_n: int = 30, fuse_m: int = 20,
                 ingest_record_ids: list[int] | None = None) -> list[RetrievedChunk]:
        dense = dense_search(self._embedder, self._store, project_id=project_id, query=query,
                             k=dense_n, ingest_record_ids=ingest_record_ids)
        sparse = sparse_search(self._store, project_id=project_id, query=query,
                               k=dense_n, ingest_record_ids=ingest_record_ids)
        fused = rrf_fuse([dense, sparse], k=fuse_m)
        if not fused:
            return []
        return self._reranker.rerank(query, fused, top_k=k)
```

(d) `tests/fakes.py` 的 `FakeVectorStore.query` 支持 list 值(向后兼容标量):
```python
    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        rows = self.records
        if filter:
            def ok(r):
                for key, val in filter.items():
                    rv = r.get(key)
                    if isinstance(val, (list, tuple)):
                        if rv not in val:
                            return False
                    elif rv != val:
                        return False
                return True
            rows = [r for r in rows if ok(r)]
        return rows[:k]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_focus_retrieval.py tests/test_dense.py tests/test_sparse.py tests/test_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/retrieval/dense.py backend/epictrace/retrieval/sparse.py backend/epictrace/retrieval/pipeline.py backend/tests/test_focus_retrieval.py backend/tests/fakes.py
git commit -m "feat(retrieval): optional ingest_record_ids focus through dense/sparse/hybrid"
```

---

### Task 7: 图把 `focus_ids` 透传给 retrieve

**Files:**
- Modify: `backend/epictrace/agent/state.py`、`backend/epictrace/agent/graph.py`
- Test: `backend/tests/test_graph_focus.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_graph_focus.py
from epictrace.agent.graph import build_rag_graph
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeLLM


class _SpyRetriever:
    def __init__(self): self.last_kwargs = None
    def retrieve(self, *, project_id, query, **kwargs):
        self.last_kwargs = kwargs
        return [RetrievedChunk(text="x", ingest_record_id=10, project_id=project_id,
                               char_start=0, char_end=1, source_type="folder_scan")]


def test_graph_passes_focus_ids_to_retriever():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="retrieve", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "q", "query": "q", "history": [],
              "iterations": 0, "focus_ids": [10, 30]})
    assert spy.last_kwargs == {"ingest_record_ids": [10, 30]}


def test_graph_omits_kwarg_when_no_focus():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="retrieve", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "q", "query": "q", "history": [],
              "iterations": 0, "focus_ids": []})
    assert spy.last_kwargs == {}                  # 无聚焦 → 不传 kwarg(兼容老 retriever)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_graph_focus.py -q`
Expected: FAIL（retrieve 节点未透传)

- [ ] **Step 3: 实现**

(a) `agent/state.py` 的 `AgentState` 加:
```python
    focus_ids: list[int]   # ChatService 写:pin 的内部文件(聚焦检索);空/缺省=全项目
```

(b) `agent/graph.py` 的 `retrieve` 节点:
```python
    def retrieve(state: AgentState) -> AgentState:
        focus = state.get("focus_ids")
        kwargs = {"ingest_record_ids": focus} if focus else {}
        chunks = retriever.retrieve(project_id=state["project_id"], query=state["query"], **kwargs)
        return {"chunks": chunks}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_graph_focus.py tests/test_graph.py -q`
Expected: PASS（原 `test_graph.py` 仍绿——老 fake 不传 focus）

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/agent/state.py backend/epictrace/agent/graph.py backend/tests/test_graph_focus.py
git commit -m "feat(agent): thread focus_ids into retrieve node"
```

---

### Task 8: `ReferenceService`

**Files:**
- Create: `backend/epictrace/services/references.py`
- Test: `backend/tests/test_references_service.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_references_service.py
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, IngestRecord, Project
from epictrace.services.references import ReferenceService

BIG_WIN = 1_000_000     # 预算极大 → 一定 fulltext
TINY_WIN = 10           # 预算极小 → 外部 deferred / 内部 focus


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid, pid = c.id, p.id
    return db, cid, pid


def _write(tmp_path, name, text):
    f = tmp_path / name; f.write_text(text, encoding="utf-8"); return str(f)


def test_add_external_small_is_fulltext_and_caches_text(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "note.md", "页表把虚拟地址映射到物理地址")
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    assert ref["kind"] == "external" and ref["mode"] == "fulltext"
    assert ref["display_name"] == "note.md"
    active = svc.list_active(cid)
    assert len(active) == 1 and active[0]["extracted_text"].startswith("页表")


def test_add_external_too_big_is_deferred(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "big.md", "字" * 500)
    ref = ReferenceService(db).add_external(cid, path, context_window=TINY_WIN)
    assert ref["mode"] == "deferred"


def test_add_external_rejects_empty_and_unsupported(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    import pytest
    empty = _write(tmp_path, "empty.md", "   ")
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, empty, context_window=BIG_WIN)
    weird = _write(tmp_path, "x.unknownext", "data")
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, weird, context_window=BIG_WIN)


def test_add_internal_small_fulltext_large_focus(tmp_path: Path):
    db, cid, pid = _setup(tmp_path)
    small = _write(tmp_path, "small.md", "短内容")
    with db.session() as s:
        rec = IngestRecord(project_id=pid, original_filename="small.md", stored_path=small,
                           content_hash="h", size_bytes=9, mtime=0.0, ingest_method="folder_scan",
                           extracted_text="短内容", indexed=True)
        s.add(rec); s.flush(); rid = rec.id
    svc = ReferenceService(db)
    ref = svc.add_internal(cid, rid, context_window=BIG_WIN)
    assert ref["kind"] == "internal" and ref["mode"] == "fulltext" and ref["ingest_record_id"] == rid
    ref2 = svc.add_internal(cid, rid, context_window=TINY_WIN)
    assert ref2["mode"] == "focus"


def test_detach_drops_from_active(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "n.md", "内容内容")
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    svc.detach(ref["id"])
    assert svc.list_active(cid) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_references_service.py -q`
Expected: FAIL（模块不存在)

- [ ] **Step 3: 实现**

```python
# backend/epictrace/services/references.py
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import ConversationReference, IngestRecord
from epictrace.services.budget import fits_fulltext


def _to_dict(r: ConversationReference) -> dict:
    return {
        "id": r.id, "conversation_id": r.conversation_id, "kind": r.kind,
        "display_name": r.display_name, "source_path": r.source_path,
        "ingest_record_id": r.ingest_record_id, "extracted_text": r.extracted_text,
        "mode": r.mode, "text_chars": r.text_chars, "detached": r.detached,
        "created_at": r.created_at,
    }


class ReferenceService:
    """会话级"对话引用"管理:外部文件现场提取+缓存、内部文件复用项目索引;按 context_window
    做 size-gate(小→fulltext / 外部大→deferred / 内部大→focus)。外部不向量化、不入库。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add_external(self, conversation_id: int, path: str, context_window: int) -> dict:
        p = Path(path)
        proc = get_processor(p)
        if proc is None:
            raise ValueError("unsupported file type")
        text = proc.process(p).text
        if not text.strip():
            raise ValueError("empty file")
        mode = "fulltext" if fits_fulltext(text, context_window) else "deferred"
        with self._db.session() as s:
            ref = ConversationReference(
                conversation_id=conversation_id, kind="external", display_name=p.name,
                source_path=str(p), extracted_text=text, text_chars=len(text), mode=mode,
            )
            s.add(ref); s.flush(); s.refresh(ref)
            return _to_dict(ref)

    def add_internal(self, conversation_id: int, ingest_record_id: int, context_window: int) -> dict:
        with self._db.session() as s:
            rec = s.get(IngestRecord, ingest_record_id)
            if rec is None:
                raise ValueError("ingest record not found")
            path = Path(rec.stored_path); name = rec.original_filename
        proc = get_processor(path)
        text = proc.process(path).text if proc is not None else ""
        # 内部:小→fulltext(缓存整段);大或无法提取→focus(只记 ingest_record_id,复用现成向量)
        fulltext = bool(text.strip()) and fits_fulltext(text, context_window)
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

    def detach(self, reference_id: int) -> None:
        with self._db.session() as s:
            ref = s.get(ConversationReference, reference_id)
            if ref is not None:
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_references_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/references.py backend/tests/test_references_service.py
git commit -m "feat(references): ReferenceService extract/size-gate/detach"
```

---

### Task 9: `ChatService` 组装引用

**Files:**
- Modify: `backend/epictrace/services/chat.py`
- Test: `backend/tests/test_chat_references.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_references.py
import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from epictrace.services.references import ReferenceService
from tests.fakes import FakeLLM


class _NoChunkRetriever:
    def retrieve(self, *, project_id, query, **kwargs):
        self.last_kwargs = kwargs
        return []


class _FocusSpyRetriever:
    def __init__(self): self.last_kwargs = None
    def retrieve(self, *, project_id, query, **kwargs):
        self.last_kwargs = kwargs
        return [RetrievedChunk(text="项目片段", ingest_record_id=99, project_id=project_id,
                               char_start=0, char_end=4, source_type="folder_scan")]


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def test_fulltext_external_ref_injected_even_on_direct_route(tmp_path: Path):
    # route=direct(无项目检索),但有全文外部引用 → 仍带【资料】、答案可引用、引用为 attachment。
    db, cid = _setup(tmp_path)
    f = tmp_path / "note.md"; f.write_text("页表把虚拟地址映射到物理地址", encoding="utf-8")
    refs = ReferenceService(db); ref = refs.add_external(cid, str(f), context_window=1_000_000)
    llm = FakeLLM(route="direct", answer="见资料[1]。")
    svc = ChatService(db, llm, _NoChunkRetriever(), references=refs)
    events = list(svc.stream_answer(cid, "讲讲这个文件"))
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["source_kind"] == "attachment" and cites[0]["reference_id"] == ref["id"]
    sent = llm.stream_messages[-1]
    assert "页表把虚拟地址" in sent[-1]["content"]      # 全文进了【资料】


def test_focus_internal_ref_passes_ingest_ids_to_retriever(tmp_path: Path):
    db, cid = _setup(tmp_path)
    # 直接造一个 focus 引用(避开真实 IngestRecord 提取):用 add_internal 的 tiny 窗口。
    from epictrace.models import IngestRecord
    with db.session() as s:
        rec = IngestRecord(project_id=1, original_filename="f.md", stored_path=str(tmp_path / "f.md"),
                           content_hash="h", size_bytes=1, mtime=0.0, ingest_method="folder_scan",
                           extracted_text="x", indexed=True)
        (tmp_path / "f.md").write_text("一些较长的内容" * 50, encoding="utf-8")
        s.add(rec); s.flush(); rid = rec.id
    refs = ReferenceService(db); refs.add_internal(cid, rid, context_window=10)   # → focus
    spy = _FocusSpyRetriever()
    svc = ChatService(db, FakeLLM(route="retrieve", grade="sufficient", answer="答[1]。"), spy, references=refs)
    list(svc.stream_answer(cid, "聚焦提问"))
    assert spy.last_kwargs.get("ingest_record_ids") == [rid]


def test_no_references_behaves_like_plan3(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(route="direct", answer="你好"), _NoChunkRetriever())  # references 默认 None
    events = list(svc.stream_answer(cid, "你好"))
    assert json.loads(next(e for e in events if e["event"] == "citations")["data"]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_chat_references.py -q`
Expected: FAIL（`ChatService.__init__` 不接受 `references`)

- [ ] **Step 3: 实现**

在 `services/chat.py`:

(a) 顶部加导入与一个模块级辅助:
```python
from epictrace.retrieval.types import RetrievedChunk


def _ref_chunk(r: dict) -> RetrievedChunk:
    """把"全文引用"包成单个 chunk 注入【资料】。外部→attachment(跳回外部文件),
    内部→project(跳回项目文件,带 ingest_record_id);char 区间覆盖整段(文件级引用)。"""
    text = r.get("extracted_text") or ""
    is_ext = r["kind"] == "external"
    return RetrievedChunk(
        text=text, ingest_record_id=r.get("ingest_record_id") or 0, project_id=0,
        char_start=0, char_end=len(text),
        source_type="attachment" if is_ext else "folder_scan",
        source_kind="attachment" if is_ext else "project",
        reference_id=r["id"],
    )
```

(b) `__init__` 增可选 `references`:
```python
    def __init__(self, db: Database, llm, retriever, references=None) -> None:
        self._db = db
        self._llm = llm
        self._retriever = retriever
        self._references = references
```

(c) `_run_turn` 在 try 内、构图前取引用并组装(替换原 graph.invoke + chunks 段落):
```python
        try:
            refs = self._references.list_active(conversation_id) if self._references else []
            fulltext_refs = [r for r in refs if r["mode"] == "fulltext"]
            focus_ids = [r["ingest_record_id"] for r in refs
                         if r["mode"] == "focus" and r.get("ingest_record_id")]
            graph = build_rag_graph(self._llm, self._retriever)
            state = graph.invoke({"project_id": self._project_id(conversation_id),
                                  "question": question, "query": question, "history": history,
                                  "iterations": 0, "focus_ids": focus_ids})
            # 全文引用恒在最前(无论 route);其后接项目/聚焦检索结果。
            chunks = [_ref_chunk(r) for r in fulltext_refs] + state.get("chunks", [])

            yield {"event": "status", "data": "生成中"}
            if chunks:
                sys_prompt = GENERATE_SYS
                user_content = f"问题:{question}\n\n【资料】\n{format_chunks(chunks)}"
            else:
                sys_prompt = CHAT_SYS
                user_content = question
            messages = [{"role": "system", "content": sys_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_content})
            parts: list[str] = []
            for tok in self._llm.stream(messages):
                parts.append(tok)
                yield {"event": "token", "data": tok}
        except Exception as exc:  # noqa: BLE001
            yield {"event": "error", "data": str(exc)}
            return
```

(其余 `answer`/`citations`/落库不变——`build_citations(answer, chunks)` 现在的 `chunks` 已含引用 chunk。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_chat_references.py tests/test_chat_service.py -q`
Expected: PASS（Plan 3 的 `test_chat_service.py` 仍绿——references=None)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/chat.py backend/tests/test_chat_references.py
git commit -m "feat(chat): assemble conversation references into context"
```

---

# Phase 3 — 后端 API

### Task 10: 引用 schemas + 路由 + 接线进对话

**Files:**
- Modify: `backend/epictrace/schemas.py`、`backend/epictrace/api/app.py`、`backend/epictrace/api/routers/conversations.py`
- Create: `backend/epictrace/api/routers/references.py`
- Test: `backend/tests/test_api_references.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_api_references.py
from pathlib import Path


def _project_conv(client, tmp_path):
    folder = tmp_path / "proj"; folder.mkdir()
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/conversations", json={"title": "t"}).json()["id"]
    return pid, cid


def test_add_external_reference_and_list(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "note.md"; f.write_text("页表内容", encoding="utf-8")
    r = client.post(f"/api/conversations/{cid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "external" and body["mode"] == "fulltext"
    assert "extracted_text" not in body                  # 列表/详情不回传整段缓存
    listed = client.get(f"/api/conversations/{cid}/references").json()
    assert len(listed) == 1 and listed[0]["display_name"] == "note.md"


def test_detach_reference(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "n.md"; f.write_text("内容内容", encoding="utf-8")
    rid = client.post(f"/api/conversations/{cid}/references",
                      json={"kind": "external", "source_path": str(f)}).json()["id"]
    assert client.delete(f"/api/conversations/{cid}/references/{rid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}/references").json() == []


def test_add_reference_bad_file_is_400(client, tmp_path: Path):
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "empty.md"; f.write_text("   ", encoding="utf-8")
    r = client.post(f"/api/conversations/{cid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_api_references.py -q`
Expected: FAIL（路由不存在 → 404)

- [ ] **Step 3: 实现**

(a) `schemas.py` 追加:
```python
class ReferenceCreate(BaseModel):
    kind: Literal["external", "internal"]
    source_path: str | None = None       # external 必填
    ingest_record_id: int | None = None  # internal 必填


class ReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    conversation_id: int
    kind: str
    display_name: str
    source_path: str | None = None
    ingest_record_id: int | None = None
    mode: str
    text_chars: int
    detached: bool
    created_at: datetime
```

(b) 新 `api/routers/references.py`:
```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.models import Conversation
from epictrace.schemas import ReferenceCreate, ReferenceOut
from epictrace.services.references import ReferenceService
from epictrace.services.settings import SettingsService

router = APIRouter(tags=["references"])  # /api 由 app 工厂统一挂载


def _require_conv(db: Database, cid: int) -> None:
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")


def _context_window(request: Request) -> int:
    chat = SettingsService(request.app.state.config).get_chat_llm()
    return chat.context_window if chat else 32768


@router.get("/conversations/{cid}/references", response_model=list[ReferenceOut])
def list_references(cid: int, db: Database = Depends(get_db)):
    _require_conv(db, cid)
    return ReferenceService(db).list_active(cid)   # response_model 自动剔除 extracted_text


@router.post("/conversations/{cid}/references", response_model=ReferenceOut,
             status_code=status.HTTP_201_CREATED)
def add_reference(cid: int, payload: ReferenceCreate, request: Request,
                  db: Database = Depends(get_db)):
    _require_conv(db, cid)
    svc = ReferenceService(db)
    cw = _context_window(request)
    try:
        if payload.kind == "external":
            if not payload.source_path:
                raise ValueError("source_path required")
            return svc.add_external(cid, payload.source_path, cw)
        if payload.ingest_record_id is None:
            raise ValueError("ingest_record_id required")
        return svc.add_internal(cid, payload.ingest_record_id, cw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/conversations/{cid}/references/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def detach_reference(cid: int, rid: int, db: Database = Depends(get_db)):
    _require_conv(db, cid)
    ReferenceService(db).detach(rid)
```

(c) `api/app.py`:在 import 行加 `references`,并在路由挂载处加一行:
```python
from epictrace.api.routers import conversations, files, health, projects, references, settings, source
...
    app.include_router(references.router, prefix="/api")
```

(d) `api/routers/conversations.py`:三处构造 `ChatService` 时注入 `ReferenceService`。顶部加导入:
```python
from epictrace.services.references import ReferenceService
```
把 `ChatService(db, llm, get_retriever(request))` 全部改为
`ChatService(db, llm, get_retriever(request), references=ReferenceService(db))`(send_message / edit_message / regenerate_message 三处)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_api_references.py tests/test_api_chat.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/schemas.py backend/epictrace/api/routers/references.py backend/epictrace/api/app.py backend/epictrace/api/routers/conversations.py backend/tests/test_api_references.py
git commit -m "feat(api): conversation references CRUD + wire into ChatService"
```

---

### Task 11: settings 路由透传 context_window

**Files:**
- Modify: `backend/epictrace/schemas.py`、`backend/epictrace/api/routers/settings.py`
- Test: `backend/tests/test_api_settings.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_profile_context_window_via_api(client):
    client.post("/api/settings/profiles",
                json={"name": "A", "base_url": "http://x", "api_key": "k",
                      "model": "m", "context_window": 8192})
    prof = client.get("/api/settings").json()["profiles"][0]
    assert prof["context_window"] == 8192
    client.put(f"/api/settings/profiles/{prof['id']}", json={"context_window": 128000})
    assert client.get("/api/settings").json()["profiles"][0]["context_window"] == 128000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_api_settings.py::test_profile_context_window_via_api -q`
Expected: FAIL（schema 无 context_window / 路由没透传)

- [ ] **Step 3: 实现**

(a) `schemas.py`:
- `ProfileCreate` 加 `context_window: int = 32768`。
- `ProfileUpdate` 加 `context_window: int | None = None`。
- `ProfileView` 加 `context_window: int`。

(b) `api/routers/settings.py`:
- `create_profile` 调用加 `context_window=payload.context_window`。
- `update_profile` 调用加 `context_window=payload.context_window`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_api_settings.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/schemas.py backend/epictrace/api/routers/settings.py backend/tests/test_api_settings.py
git commit -m "feat(api): expose profile context_window in settings"
```

---

### Task 12: 外部附件来源解析 + 路由

**Files:**
- Modify: `backend/epictrace/services/source.py`、`backend/epictrace/api/routers/source.py`
- Test: `backend/tests/test_source_attachment.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_source_attachment.py
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Project
from epictrace.services.references import ReferenceService
from epictrace.services.source import SourceService


def test_get_attachment_text_returns_cached_external(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush(); cid = c.id
    f = tmp_path / "note.md"; f.write_text("页表把虚拟地址映射到物理地址", encoding="utf-8")
    ref = ReferenceService(db).add_external(cid, str(f), context_window=1_000_000)
    out = SourceService(db).get_attachment_text(ref["id"])
    assert out["filename"] == "note.md" and out["text"].startswith("页表")
    assert out["path"].endswith("note.md")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_source_attachment.py -q`
Expected: FAIL（`SourceService` 无 `get_attachment_text`)

- [ ] **Step 3: 实现**

(a) `services/source.py` 加方法(并 import `ConversationReference`):
```python
from epictrace.models import ConversationReference, IngestRecord
...
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
            proc = get_processor(Path(path))
            text = proc.process(Path(path)).text if proc is not None else ""
        return {"filename": name, "path": path, "text": text}
```

(b) `api/routers/source.py` 加路由:
```python
@router.get("/attachment-source/{reference_id}", response_model=SourceOut)
def get_attachment_source(reference_id: int, db: Database = Depends(get_db)):
    try:
        return SourceOut(**SourceService(db).get_attachment_text(reference_id))
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_source_attachment.py tests/test_source_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/source.py backend/epictrace/api/routers/source.py backend/tests/test_source_attachment.py
git commit -m "feat(source): resolve external attachment source by reference"
```

---

### Task 13: 原生多选文件

**Files:**
- Modify: `shell/run.py`、`frontend/src/lib/pickers.ts`
- Test: 手动(pywebview 原生对话框无单测;保持 `test_shell_reveal.py` 绿)

- [ ] **Step 1: 后端多选**

`shell/run.py` 的 `Api` 加方法(保留 `pick_file` 不动,供文件夹/单选场景):
```python
    def pick_files(self) -> list[str]:
        """多选文件(对话附件用)。返回绝对路径列表;取消则空列表。"""
        if self._window is None:
            return []
        result = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        return list(result) if result else []
```

- [ ] **Step 2: 前端封装**

`frontend/src/lib/pickers.ts`:在 `Window.pywebview.api` 类型里加 `pick_files(): Promise<string[]>;`,并加:
```typescript
/** 多选文件(对话附件)。打包态走 pywebview;开发态回退 prompt 单条路径。 */
export async function pickFiles(): Promise<string[]> {
  if (window.pywebview?.api) return window.pywebview.api.pick_files();
  const one = window.prompt("(开发态)输入文件绝对路径:")?.trim();
  return one ? [one] : [];
}
```

- [ ] **Step 3: 验证 + 提交**

Run: `cd backend && .venv/bin/pytest tests/test_shell_reveal.py -q`（确保未破坏)；`cd frontend && npm run build`
Expected: PASS / build 成功

```bash
git add shell/run.py frontend/src/lib/pickers.ts
git commit -m "feat(shell): native multi-file picker for attachments"
```

---

# Phase 4 — 前端

> 前端无单测运行器;每个任务以 `cd frontend && npm run build`(tsc + vite)为门,并附手动验证点。

### Task 14: `api.ts` — 引用类型/CRUD + context_window + 附件来源

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 加类型**

在 `Citation` 接口加两个可选字段(后端已回传):
```typescript
export interface Citation {
  n: number; ingest_record_id: number; char_start: number; char_end: number;
  snippet: string; source_type: string;
  source_kind?: "project" | "attachment";
  reference_id?: number | null;
}
```
`LLMProfile` 加 `context_window: number;`;`Settings` 不变。加引用类型:
```typescript
export interface ConversationReference {
  id: number; conversation_id: number; kind: "external" | "internal";
  display_name: string; source_path: string | null; ingest_record_id: number | null;
  mode: "fulltext" | "focus" | "deferred"; text_chars: number; detached: boolean; created_at: string;
}
```

- [ ] **Step 2: 加方法**

在 `api` 对象里加(放在 `getSource` 附近):
```typescript
  getAttachmentSource: (referenceId: number) =>
    fetch(`${BASE}/api/attachment-source/${referenceId}`).then(j<SourceText>),
  listReferences: (cid: number) =>
    fetch(`${BASE}/api/conversations/${cid}/references`).then(j<ConversationReference[]>),
  addExternalReference: (cid: number, source_path: string) =>
    fetch(`${BASE}/api/conversations/${cid}/references`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "external", source_path }),
    }).then(j<ConversationReference>),
  addInternalReference: (cid: number, ingest_record_id: number) =>
    fetch(`${BASE}/api/conversations/${cid}/references`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "internal", ingest_record_id }),
    }).then(j<ConversationReference>),
  detachReference: (cid: number, rid: number) =>
    fetch(`${BASE}/api/conversations/${cid}/references/${rid}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 404) throw new Error(`${r.status}: ${r.statusText}`);
    }),
```
`createProfile`/`updateProfile` 的 payload 类型各加可选 `context_window?: number`。

- [ ] **Step 3: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(web): references API client + attachment source + context_window"
```

---

### Task 15: `ReferencePanel` 折叠两栏面板

**Files:**
- Create: `frontend/src/components/ReferencePanel.tsx`

- [ ] **Step 1: 写组件**

```tsx
// frontend/src/components/ReferencePanel.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, FolderInput, Paperclip, X } from "lucide-react";

import { type ConversationReference } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const MODE_LABEL: Record<ConversationReference["mode"], string> = {
  fulltext: "全文已载入",
  focus: "已索引聚焦",
  deferred: "待 Plan 5(文件较大)",
};

/** 折叠式「本对话引用」面板:两栏(外部/内部),每条带模式标签 + 解挂。空则不渲染。 */
export function ReferencePanel({
  references,
  onDetach,
  onAddInternal,
}: {
  references: ConversationReference[];
  onDetach: (rid: number) => void;
  onAddInternal: () => void;
}) {
  const [open, setOpen] = useState(true);
  const external = references.filter((r) => r.kind === "external");
  const internal = references.filter((r) => r.kind === "internal");
  if (references.length === 0) return null;

  return (
    <div className="mx-auto w-full max-w-2xl px-6">
      <div className="rounded-xl border border-border/70 bg-muted/30">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground outline-none hover:text-foreground"
        >
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          <Paperclip className="size-3.5" />
          本对话引用 ({references.length})
        </button>
        {open && (
          <div className="flex flex-col gap-3 px-3 pb-3">
            <Zone title="外部文件" icon={<Paperclip className="size-3" />}
                  rows={external} onDetach={onDetach} />
            <Zone title="内部文件" icon={<FileText className="size-3" />}
                  rows={internal} onDetach={onDetach}
                  action={
                    <Button type="button" variant="ghost" size="xs" onClick={onAddInternal}>
                      <FolderInput className="size-3" /> 从项目添加
                    </Button>
                  } />
          </div>
        )}
      </div>
    </div>
  );
}

function Zone({
  title, icon, rows, onDetach, action,
}: {
  title: string;
  icon: React.ReactNode;
  rows: ConversationReference[];
  onDetach: (rid: number) => void;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1 text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground/80">
          {icon} {title}
        </span>
        {action}
      </div>
      {rows.length === 0 ? (
        <p className="px-1 text-xs text-muted-foreground/60">无</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((r) => (
            <li key={r.id}
                className="flex items-center gap-2 rounded-lg border border-border/60 bg-background px-2.5 py-1.5">
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-xs text-foreground" title={r.display_name}>
                {r.display_name}
              </span>
              <span className={cn(
                "shrink-0 rounded px-1.5 py-0.5 text-[0.65rem] font-medium",
                r.mode === "deferred" ? "bg-amber-500/15 text-amber-700" : "bg-muted text-muted-foreground",
              )}>
                {MODE_LABEL[r.mode]}
              </span>
              <button type="button" onClick={() => onDetach(r.id)} aria-label="解挂"
                      className="shrink-0 rounded p-0.5 text-muted-foreground outline-none hover:bg-muted hover:text-foreground">
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功（若 `size="xs"`/`size="icon-xs"` 等变体不存在,改用现有 Button 尺寸变体——参照 `MessageList.tsx` 里在用的 `size="xs"`/`"icon-xs"`)

```bash
git add frontend/src/components/ReferencePanel.tsx
git commit -m "feat(web): two-zone collapsible reference panel"
```

---

### Task 16: Composer 附件入口(选择/拖拽/粘贴)

**Files:**
- Modify: `frontend/src/components/Composer.tsx`

- [ ] **Step 1: 加附件入口**

给 `Composer` 增两个 props 并在输入框左侧加「+」按钮、整框支持拖拽落文件与粘贴文件:

```tsx
import { useRef, useState } from "react";
import { Plus, Settings2, SendHorizontal, Square } from "lucide-react";
import { pickFiles } from "@/lib/pickers";
// ... 其余 import 不变

export function Composer({
  llmConfigured, streaming, onSend, onStop, onOpenSettings, onAttachPaths,
}: {
  llmConfigured: boolean;
  streaming: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
  onOpenSettings: () => void;
  /** 用户通过「+」/拖拽/粘贴选了外部文件(绝对路径列表)。拖拽/粘贴在浏览器拿不到绝对路径时为空。 */
  onAttachPaths: (paths: string[]) => void;
}) {
  const [value, setValue] = useState("");
  const [dragging, setDragging] = useState(false);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  // ... grow / submit 不变

  const pick = async () => {
    const paths = await pickFiles();
    if (paths.length) onAttachPaths(paths);
  };
  // 拖拽:pywebview/桌面端 File 对象常带 .path(绝对路径);拿不到则忽略(回退「+」)。
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragging(false);
    const paths = Array.from(e.dataTransfer.files)
      .map((f) => (f as File & { path?: string }).path)
      .filter((p): p is string => Boolean(p));
    if (paths.length) onAttachPaths(paths);
  };
```

在外层容器(包住输入与按钮的 `div`)加拖拽态与处理器,并在输入框前加「+」按钮:
```tsx
        <div
          onDragOver={(e) => { e.preventDefault(); if (llmConfigured) setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={llmConfigured ? onDrop : undefined}
          className={cn(
            "flex items-end gap-2 rounded-2xl border bg-background p-2 shadow-sm transition-colors",
            llmConfigured
              ? "border-border focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/40"
              : "border-border/70 bg-muted/30",
            dragging && "border-ring ring-3 ring-ring/40",
          )}
        >
          <Button type="button" size="icon" variant="ghost" disabled={!llmConfigured}
                  onClick={pick} aria-label="添加文件" className="mb-px">
            <Plus className="size-4" />
          </Button>
          <textarea
            // ... 原属性不变,onPaste 加:
            onPaste={(e) => {
              const paths = Array.from(e.clipboardData.files)
                .map((f) => (f as File & { path?: string }).path)
                .filter((p): p is string => Boolean(p));
              if (paths.length) { e.preventDefault(); onAttachPaths(paths); }
            }}
          />
          {/* streaming ? Stop : Send 不变 */}
        </div>
```

- [ ] **Step 2: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功

手动验证点(打包态):点「+」弹原生多选;拖文件进框高亮并触发 `onAttachPaths`。

```bash
git add frontend/src/components/Composer.tsx
git commit -m "feat(web): composer attach button + drag/paste file paths"
```

---

### Task 17: SourceViewer 解析外部附件来源

**Files:**
- Modify: `frontend/src/components/SourceViewer.tsx`

- [ ] **Step 1: 按 source_kind 分流取来源**

把拉取来源那段改为:`source_kind === "attachment"` → `getAttachmentSource(reference_id)`,否则 `getSource(ingest_record_id)`:
```tsx
  useEffect(() => {
    if (!citation) return;
    setSource(null); setError(null); setLoading(true);
    let cancelled = false;
    const fetcher =
      citation.source_kind === "attachment" && citation.reference_id != null
        ? api.getAttachmentSource(citation.reference_id)
        : api.getSource(citation.ingest_record_id);
    fetcher
      .then((s) => { if (!cancelled) setSource(s); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [citation]);
```
(高亮/滚动/Finder 逻辑不变;外部全文引用是 0..len → 整篇高亮,符合"文件级引用"。)

- [ ] **Step 2: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功

```bash
git add frontend/src/components/SourceViewer.tsx
git commit -m "feat(web): source viewer resolves attachment citations"
```

---

### Task 18: SettingsModal 加 `context_window` 字段

**Files:**
- Modify: `frontend/src/components/SettingsModal.tsx`

- [ ] **Step 1: 表单加字段**

`FormState` 与 `BLANK` 加 `context_window`(字符串便于输入,提交时转数):
```tsx
type FormState = { name: string; base_url: string; api_key: string; model: string; context_window: string };
const BLANK: FormState = { name: "", base_url: "", api_key: "", model: "", context_window: "32768" };
```
`openEdit` 回填:`context_window: String(p.context_window ?? 32768)`。
`save()` 里 create/update 的 payload 加 `context_window: Number(form.context_window) || 32768`。
`ProfileForm` 在「模型」字段后加一个数字输入 `Field`(沿用现有 `Field`/`Input`):
```tsx
      <Field id="pf-ctx" label="上下文窗口(token)">
        <Input id="pf-ctx" type="number" inputMode="numeric" value={form.context_window}
               disabled={saving} placeholder="如 32768 / 128000"
               className="font-mono text-xs" onChange={set("context_window")} />
      </Field>
```
并在标签下补一行说明:`<p className="text-[0.7rem] text-muted-foreground">决定多大的附件能整篇进上下文(超出则留给后续大文件处理)。</p>`(可放 Field 内或其后)。

- [ ] **Step 2: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功

```bash
git add frontend/src/components/SettingsModal.tsx
git commit -m "feat(web): context_window field in profile form"
```

---

### Task 19: 流式 markdown 消抖 + 空状态建议提示

**Files:**
- Modify: `frontend/src/components/AssistantMarkdown.tsx`、`frontend/src/views/ProjectsConversationView.tsx`

- [ ] **Step 1: 未闭合代码围栏补齐(消抖)**

`AssistantMarkdown.tsx` 在 `ReactMarkdown` 渲染前对 content 做"补全未闭合 ```":
```tsx
/** 流式途中可能出现未闭合的 ``` 代码围栏,会把后续正文误当代码、来回闪。
 * 渲染前若 ``` 数为奇数,临时补一个闭合围栏(只影响渲染,不改原始内容)。 */
function balanceFences(md: string): string {
  const fences = (md.match(/^```/gm) ?? []).length;
  return fences % 2 === 1 ? `${md}\n\`\`\`` : md;
}
```
把 `{content}` 改为 `{balanceFences(content)}`。

- [ ] **Step 2: 空状态建议提示**

在 `ProjectsConversationView.tsx` 的会话区:当某个会话 `messages.length === 0` 且非草稿加载中时,渲染一个居中空状态 + 3 个建议 prompt 按钮(点按即 `send(prompt)`)。在对话主体里 messages 为空的分支加:
```tsx
{messages.length === 0 && !streaming && (
  <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
    <p className="text-sm font-medium text-foreground">开始对话</p>
    <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
      基于项目资料提问,或用「+」附一个文件一起聊。回答会带可跳回原文的来源引用。
    </p>
    <div className="flex flex-wrap justify-center gap-2">
      {["这个项目主要讲了什么?", "帮我总结关键结论", "列出待办/风险点"].map((q) => (
        <button key={q} type="button" disabled={!llmConfigured} onClick={() => send(q)}
                className="rounded-full border border-border/70 bg-background px-3 py-1.5 text-xs text-foreground outline-none hover:bg-muted/50 disabled:opacity-50">
          {q}
        </button>
      ))}
    </div>
  </div>
)}
```
(具体挂载位置:在渲染 `<MessageList>` 的同级条件分支里,messages 为空时显示此块、非空时显示 MessageList。`send`/`streaming`/`llmConfigured` 是该视图已有的状态/函数,见 Task 20。)

- [ ] **Step 3: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功

```bash
git add frontend/src/components/AssistantMarkdown.tsx frontend/src/views/ProjectsConversationView.tsx
git commit -m "feat(web): streaming fence balancing + chat empty state"
```

---

### Task 20: 在会话视图接线引用面板

**Files:**
- Modify: `frontend/src/views/ProjectsConversationView.tsx`

- [ ] **Step 1: 引用状态 + 面板**

在会话组件(管 `messages`/`streaming`/`send` 的那个,见映射的 `Conversation` 子组件)加:
- 状态:`const [references, setReferences] = useState<ConversationReference[]>([]);`
- 拉取:会话切换时 `api.listReferences(cid).then(setReferences)`(草稿无 cid → 空);
- 渲染:在 `<Composer>` 上方插 `<ReferencePanel references={references} onDetach={detach} onAddInternal={openInternalPicker} />`;
- 给 `<Composer>` 传 `onAttachPaths={attachExternal}`。

加三个处理器:
```tsx
const refreshRefs = async (cid: number) => setReferences(await api.listReferences(cid));

// 附外部文件:草稿会话先创建(沿用首发消息的 create-on-first-use),再逐个挂。
const attachExternal = async (paths: string[]) => {
  const cid = await ensureConversation();          // 草稿 → 创建并返回真实 cid(见 Step 2)
  for (const p of paths) {
    try { await api.addExternalReference(cid, p); } catch (e) { /* 单文件失败不阻塞其余 */ }
  }
  await refreshRefs(cid);
};

const detach = async (rid: number) => {
  const cid = conversationId;                       // 已有真实 cid
  if (cid == null) return;
  await api.detachReference(cid, rid);
  await refreshRefs(cid);
};

// 内部文件选择:打开一个列出本项目文件(api.listFiles(projectId))的轻量选择弹窗,
// 选中即 api.addInternalReference(cid, ingest_record_id) 后 refreshRefs。
```

- [ ] **Step 2: 草稿会话 create-on-first-use 复用**

把现有"首次发消息才创建草稿会话"的逻辑抽成一个 `ensureConversation(): Promise<number>`(若已是真实会话直接返回其 id;若草稿则 `api.createConversation(projectId)` 并切换为真实会话、更新侧栏),供 `send()` 与 `attachExternal()` 共用。这样**在发消息前就附文件**也会先把草稿落成真实会话,避免无 cid。

- [ ] **Step 3: 内部文件选择弹窗**

加一个最小弹窗组件(可内联):打开时 `api.listFiles(projectId)` 列出 `indexed` 的文件,点一项 → `ensureConversation()` → `api.addInternalReference(cid, file.id)` → `refreshRefs` → 关闭。未索引文件置灰(只有进了项目索引的才能聚焦)。

- [ ] **Step 4: 验证 + 提交**

Run: `cd frontend && npm run build`
Expected: build 成功

手动验证点(打包态全链路):新建草稿对话 → 「+」附一个小 md → 面板出现「全文已载入」→ 提问 → 回答含 `[n]` → 点 `[n]` 打开来源查看器看到该附件内容 → 解挂后再问不再带它。`@/从项目添加` 一个已索引文件(大文件)→「已索引聚焦」→ 提问命中该文件。

```bash
git add frontend/src/views/ProjectsConversationView.tsx
git commit -m "feat(web): wire reference panel + attach/detach into conversation view"
```

---

## 收尾

- [ ] **全量后端测试**: `cd backend && .venv/bin/pytest -q`（期望全绿;真实模型 slow 测试默认跳过)
- [ ] **前端构建**: `cd frontend && npm run build`
- [ ] **代号扫描**: 确认无前身原型代号(`docs/decisions/` 在 .gitignore,不入 git)
- [ ] **收尾**: 用 superpowers:finishing-a-development-branch 合并/提 PR。

---

## Self-Review(写计划后自查,已校对)

**Spec 覆盖:** 两区面板(Task 15/20)、外部全文/大文件 deferred(Task 8/9)、内部全文/聚焦(Task 6/7/8/9)、不向量化外部(全程无 scratch 向量)、context_window 动态阈值(Task 2/3/11/18)、引用 source_kind 跳回(Task 4/12/17)、对话级存活(引用会话级 + ChatService 自取 list_active,Task 9)、解挂(Task 8/15/20)、UI 打磨 chips/拖拽/粘贴/消抖/空状态(Task 15/16/19)、数据模型 conversation_references + Message 不变(Task 1)、错误/边界(空文件/不支持/大文件/原文件移动:Task 8/12)、为 Plan 5 留口(deferred 引用不进资料,Task 9)。**均有对应任务。**

**类型一致性:** `ConversationReference` 字段、`ReferenceService` 方法名(add_external/add_internal/detach/list_active)、`RetrievedChunk.source_kind/reference_id`、`retrieve(..., ingest_record_ids=...)`、`AgentState.focus_ids`、API 形状(ReferenceCreate/Out、context_window)在前后端各任务间一致。

**无占位:** 各步含真实测试与实现代码;前端以 build 为门并附手动验证点。

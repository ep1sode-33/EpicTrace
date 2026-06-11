# Plan 3: Agentic RAG + Cited Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Backend tasks are TDD with pytest; the frontend task uses the `impeccable` skill and is gated on `npm run build`. Steps use checkbox (`- [ ]`).

**Goal:** 接通「项目与对话」对话框 —— 在项目的 Milvus chunks 上做混合检索+重排,LangGraph 跑 检索→反思→改写→生成(带引用)环,任意 OpenAI-compat LLM 流式作答,点引用进内置来源查看器高亮跳回。

**Architecture:** 自底向上的可测试流水线:`OpenAICompatLLM` + 检索层(dense+sparse→RRF→rerank)+ `HybridRetriever` + LangGraph 图 + `ChatService`(SSE 流 + 落库)+ 来源查看器 + 设置 + 前端。所有编排用假替身(FakeLLM/FakeReranker/FakeEmbedder/FakeVectorStore)单测;真模型走 `EPICTRACE_RUN_SLOW=1` 冒烟。

**Tech Stack:** Python 3.11(venv)· FastAPI · SQLAlchemy · `openai`(OpenAI-compat 客户端)· `jieba` + `rank_bm25`(稀疏)· `FlagEmbedding`(BGE-reranker,已装)· `langgraph` · `sse-starlette`(SSE)· React/Tailwind/shadcn(前端)

**Spec:** `docs/superpowers/specs/2026-06-11-epictrace-plan3-agentic-rag-chat-design.md`
**约定:** 不出现前身代号;git 身份 `ep1sode-33`(plain commit + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` 尾);venv 用 `.venv/bin/<tool>`;桌面 APP 原生思路。
**Branch:** 先建并切到 `feat/plan3-rag-chat`(从 `main`):`git -C /Users/william/Desktop/EpicTrace checkout -b feat/plan3-rag-chat`。

---

## File Structure

```
backend/epictrace/
  llm/__init__.py · llm/openai_compat.py        # OpenAICompatLLM(complete + stream)
  retrieval/__init__.py
  retrieval/types.py                            # RetrievedChunk
  retrieval/dense.py · sparse.py · fuse.py · rerank.py · pipeline.py
  agent/__init__.py · state.py · prompts.py · citations.py · graph.py
  services/chat.py · source.py · settings.py
  models.py (+ Conversation, Message)
  schemas.py (+ DTOs)
  interfaces/vector_store.py (+ list_by_project)
  vectorstore/milvus_lite.py (+ list_by_project)
  api/deps.py (warmup reranker+embedder before Milvus; get_llm/get_retriever)
  api/routers/conversations.py · source.py · settings.py
backend/tests/
  fakes.py (+ FakeLLM, FakeReranker, FakeVectorStore.list_by_project)
  test_openai_compat_llm.py · test_vectorstore_list.py · test_dense.py · test_sparse.py
  test_fuse.py · test_rerank.py · test_pipeline.py · test_citations.py · test_graph.py
  test_chat_service.py · test_source_service.py · test_settings.py
  test_models_conversation.py · test_api_chat.py · test_api_settings.py
  test_rag_real_smoke.py (slow)
frontend/src/ (Composer, MessageList, SourceViewer, SettingsModal, ConversationList + view wiring + lib/api.ts)
```

---

## Task 1: OpenAICompatLLM(任意 OpenAI-compat,complete + stream)

**Files:** Create `backend/epictrace/llm/__init__.py`(空)、`backend/epictrace/llm/openai_compat.py`; Test `backend/tests/test_openai_compat_llm.py`

- [ ] **Step 1: 装依赖** — `cd backend && .venv/bin/pip install openai` 并加入 `pyproject.toml` dependencies。

- [ ] **Step 2: 写失败测试** `backend/tests/test_openai_compat_llm.py`

```python
from epictrace.llm.openai_compat import OpenAICompatLLM


class _FakeChoice:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


def test_complete_sends_messages_and_returns_content(monkeypatch):
    captured = {}
    llm = OpenAICompatLLM(base_url="http://x", api_key="k", model="m")

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResp("hello")

    monkeypatch.setattr(llm._client.chat.completions, "create", fake_create)
    out = llm.complete([{"role": "user", "content": "hi"}])
    assert out == "hello"
    assert captured["model"] == "m"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["stream"] is False


def test_stream_yields_token_deltas(monkeypatch):
    llm = OpenAICompatLLM(base_url="http://x", api_key="k", model="m")

    def fake_create(**kwargs):
        assert kwargs["stream"] is True
        for piece in ["he", "llo"]:
            delta = type("D", (), {"content": piece})
            yield type("C", (), {"choices": [type("Ch", (), {"delta": delta})]})

    monkeypatch.setattr(llm._client.chat.completions, "create", fake_create)
    assert "".join(llm.stream([{"role": "user", "content": "hi"}])) == "hello"
```

- [ ] **Step 3: 运行确认失败** — `cd backend && .venv/bin/pytest tests/test_openai_compat_llm.py -v` → FAIL(ModuleNotFoundError)。

- [ ] **Step 4: 实现** `backend/epictrace/llm/openai_compat.py`

```python
from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from epictrace.interfaces.llm import LLMProvider


class OpenAICompatLLM(LLMProvider):
    """任意 OpenAI-Compatible 端点(DeepSeek/OpenAI/Ollama/vLLM…)。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._model = model
        # 空 key 也允许构造(本地 Ollama 等无需 key);真正调用时才需要有效配置。
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-set")

    def complete(self, messages: list[dict], **kwargs) -> str:
        resp = self._client.chat.completions.create(
            model=self._model, messages=messages, stream=False, **kwargs
        )
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=self._model, messages=messages, stream=True, **kwargs
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                yield delta.content
```

- [ ] **Step 5: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_openai_compat_llm.py -v
git add backend/epictrace/llm/ backend/tests/test_openai_compat_llm.py backend/pyproject.toml
git commit -m "feat(backend): OpenAICompatLLM(任意 OpenAI-compat,complete + stream)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: VectorStore.list_by_project(BM25 语料)

**Files:** Modify `backend/epictrace/interfaces/vector_store.py`、`backend/epictrace/vectorstore/milvus_lite.py`、`backend/tests/fakes.py`; Test `backend/tests/test_vectorstore_list.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_vectorstore_list.py`

```python
from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _rec(pid, rid, text):
    return {"vector": [0.1] * DIM, "text": text, "ingest_record_id": rid, "project_id": pid,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan", "embed_model_id": "fake"}


def test_list_by_project_returns_only_that_project(tmp_path: Path):
    s = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    s.upsert([_rec(7, 1, "alpha"), _rec(7, 2, "beta"), _rec(8, 3, "gamma")])
    rows = s.list_by_project(7)
    assert {r["text"] for r in rows} == {"alpha", "beta"}
    assert all(r["project_id"] == 7 for r in rows)
    assert {"char_start", "char_end", "ingest_record_id"} <= set(rows[0])
```

- [ ] **Step 2: 运行确认失败** — `cd backend && .venv/bin/pytest tests/test_vectorstore_list.py -v` → FAIL(no attribute `list_by_project`)。

- [ ] **Step 3: 实现**

`backend/epictrace/interfaces/vector_store.py` 给 `VectorStore` ABC 增:
```python
    @abstractmethod
    def list_by_project(self, project_id: int) -> list[dict]: ...
```

`backend/epictrace/vectorstore/milvus_lite.py` 增方法(用 milvus `query`,非向量搜索):
```python
    def list_by_project(self, project_id: int) -> list[dict]:
        return self._client.query(
            _COLLECTION,
            filter=f"project_id == {project_id}",
            output_fields=list(_SCALARS.keys()),
            limit=16384,
        )
```

`backend/tests/fakes.py` 给 `FakeVectorStore` 增:
```python
    def list_by_project(self, project_id: int) -> list[dict]:
        return [r for r in self.records if r.get("project_id") == project_id]
```

- [ ] **Step 4: 运行确认通过 + 全套 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_vectorstore_list.py -q && .venv/bin/pytest -q
git add backend/epictrace/interfaces/vector_store.py backend/epictrace/vectorstore/milvus_lite.py backend/tests/fakes.py backend/tests/test_vectorstore_list.py
git commit -m "feat(backend): VectorStore.list_by_project(供 BM25 语料)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: RetrievedChunk 类型 + 稠密检索

**Files:** Create `backend/epictrace/retrieval/__init__.py`(空)、`retrieval/types.py`、`retrieval/dense.py`; Test `backend/tests/test_dense.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_dense.py`

```python
from epictrace.retrieval.dense import dense_search
from tests.fakes import FakeEmbedder, FakeVectorStore


def test_dense_search_embeds_query_and_returns_chunks():
    store = FakeVectorStore()
    store.upsert([
        {"vector": FakeEmbedder().embed(["alpha"])[0], "text": "alpha", "ingest_record_id": 1,
         "project_id": 7, "char_start": 0, "char_end": 5, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    out = dense_search(FakeEmbedder(), store, project_id=7, query="alpha", k=5)
    assert out and out[0].text == "alpha"
    assert out[0].project_id == 7 and out[0].ingest_record_id == 1
```

- [ ] **Step 2: 运行确认失败** — FAIL(ModuleNotFoundError)。

- [ ] **Step 3: 实现**

`backend/epictrace/retrieval/types.py`:
```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    text: str
    ingest_record_id: int
    project_id: int
    char_start: int
    char_end: int
    source_type: str
    score: float = 0.0

    @classmethod
    def from_row(cls, row: dict, score: float = 0.0) -> "RetrievedChunk":
        return cls(
            text=row["text"], ingest_record_id=row["ingest_record_id"], project_id=row["project_id"],
            char_start=row["char_start"], char_end=row["char_end"],
            source_type=row.get("source_type", "folder_scan"), score=score,
        )

    def key(self) -> tuple:
        return (self.ingest_record_id, self.char_start, self.char_end)
```

`backend/epictrace/retrieval/dense.py`:
```python
from __future__ import annotations

from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.types import RetrievedChunk


def dense_search(embedder: EmbeddingProvider, store: VectorStore, *, project_id: int,
                 query: str, k: int = 30) -> list[RetrievedChunk]:
    vec = embedder.embed([query])[0]
    rows = store.query(vec, filter={"project_id": project_id}, k=k)
    return [RetrievedChunk.from_row(r, score=1.0 / (i + 1)) for i, r in enumerate(rows)]
```

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_dense.py -v
git add backend/epictrace/retrieval/ backend/tests/test_dense.py
git commit -m "feat(backend): RetrievedChunk + 稠密检索" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 稀疏检索(jieba + BM25)

**Files:** Create `backend/epictrace/retrieval/sparse.py`; Test `backend/tests/test_sparse.py`

- [ ] **Step 1: 装依赖** — `cd backend && .venv/bin/pip install jieba rank_bm25` 并加入 `pyproject.toml`。

- [ ] **Step 2: 写失败测试** `backend/tests/test_sparse.py`

```python
from epictrace.retrieval.sparse import sparse_search
from tests.fakes import FakeVectorStore


def _rec(rid, text):
    return {"vector": [0.0], "text": text, "ingest_record_id": rid, "project_id": 7,
            "char_start": 0, "char_end": len(text), "source_type": "folder_scan", "embed_model_id": "fake"}


def test_sparse_search_ranks_by_keyword_overlap():
    store = FakeVectorStore()
    store.upsert([_rec(1, "操作系统 虚拟内存 页表"), _rec(2, "数据库 事务 隔离级别"),
                  _rec(3, "虚拟内存 分页 缺页中断")])
    out = sparse_search(store, project_id=7, query="虚拟内存 页表", k=2)
    ids = [c.ingest_record_id for c in out]
    assert 2 not in ids  # 无关项不该进 top-2
    assert set(ids) <= {1, 3}


def test_sparse_search_empty_project_returns_empty():
    assert sparse_search(FakeVectorStore(), project_id=7, query="x", k=5) == []
```

- [ ] **Step 3: 运行确认失败** — FAIL。

- [ ] **Step 4: 实现** `backend/epictrace/retrieval/sparse.py`

```python
from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.types import RetrievedChunk


def _tok(text: str) -> list[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


def sparse_search(store: VectorStore, *, project_id: int, query: str, k: int = 30) -> list[RetrievedChunk]:
    rows = store.list_by_project(project_id)
    if not rows:
        return []
    corpus = [_tok(r["text"]) for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tok(query))
    ranked = sorted(zip(rows, scores), key=lambda rs: rs[1], reverse=True)[:k]
    return [RetrievedChunk.from_row(r, score=float(s)) for r, s in ranked if s > 0]
```

- [ ] **Step 5: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_sparse.py -v
git add backend/epictrace/retrieval/sparse.py backend/tests/test_sparse.py backend/pyproject.toml
git commit -m "feat(backend): 稀疏检索(jieba + BM25)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: RRF 融合

**Files:** Create `backend/epictrace/retrieval/fuse.py`; Test `backend/tests/test_fuse.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_fuse.py`

```python
from epictrace.retrieval.fuse import rrf_fuse
from epictrace.retrieval.types import RetrievedChunk


def _c(rid):
    return RetrievedChunk(text=f"t{rid}", ingest_record_id=rid, project_id=7,
                          char_start=0, char_end=1, source_type="folder_scan")


def test_rrf_rewards_items_ranked_high_in_both_lists():
    dense = [_c(1), _c(2), _c(3)]
    sparse = [_c(2), _c(1), _c(4)]
    fused = rrf_fuse([dense, sparse], k=10)
    # 1 与 2 在两路都靠前 → 应排在仅单路出现的 3/4 之前
    top2 = {c.ingest_record_id for c in fused[:2]}
    assert top2 == {1, 2}


def test_rrf_dedups_by_chunk_key():
    fused = rrf_fuse([[_c(1)], [_c(1)]], k=10)
    assert len(fused) == 1
```

- [ ] **Step 2: 运行确认失败** — FAIL。

- [ ] **Step 3: 实现** `backend/epictrace/retrieval/fuse.py`

```python
from __future__ import annotations

from epictrace.retrieval.types import RetrievedChunk

_K0 = 60  # RRF 常数


def rrf_fuse(ranked_lists: list[list[RetrievedChunk]], k: int = 20) -> list[RetrievedChunk]:
    scores: dict[tuple, float] = {}
    keep: dict[tuple, RetrievedChunk] = {}
    for lst in ranked_lists:
        for rank, chunk in enumerate(lst):
            key = chunk.key()
            scores[key] = scores.get(key, 0.0) + 1.0 / (_K0 + rank)
            keep.setdefault(key, chunk)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    out = []
    for key, score in ordered:
        c = keep[key]
        c.score = score
        out.append(c)
    return out
```

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_fuse.py -v
git add backend/epictrace/retrieval/fuse.py backend/tests/test_fuse.py
git commit -m "feat(backend): RRF 融合" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: BgeReranker + Reranker 协议 + FakeReranker

**Files:** Create `backend/epictrace/retrieval/rerank.py`; Modify `backend/tests/fakes.py`; Test `backend/tests/test_rerank.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_rerank.py`(用 FakeReranker 测协议契约)

```python
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeReranker


def _c(rid, text):
    return RetrievedChunk(text=text, ingest_record_id=rid, project_id=7,
                          char_start=0, char_end=len(text), source_type="folder_scan")


def test_fake_reranker_orders_by_query_substring_and_truncates_top_k():
    chunks = [_c(1, "无关内容"), _c(2, "命中 关键词 命中"), _c(3, "命中 一次")]
    out = FakeReranker().rerank("关键词", chunks, top_k=2)
    assert len(out) == 2
    assert out[0].ingest_record_id == 2  # 命中最多排第一
```

- [ ] **Step 2: 运行确认失败** — FAIL(`FakeReranker` 不存在)。

- [ ] **Step 3: 实现**

`backend/tests/fakes.py` 增:
```python
from epictrace.retrieval.types import RetrievedChunk


class FakeReranker:
    """按 query 子词在 chunk 文本里的命中次数打分;不依赖 torch。"""

    def warmup(self) -> None:
        return None

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int = 6) -> list[RetrievedChunk]:
        terms = [t for t in query.split() if t]

        def score(c: RetrievedChunk) -> int:
            return sum(c.text.count(t) for t in terms)

        return sorted(chunks, key=score, reverse=True)[:top_k]
```

`backend/epictrace/retrieval/rerank.py`(真实现,懒加载 + warmup):
```python
from __future__ import annotations

import threading

from epictrace.retrieval.types import RetrievedChunk


class BgeReranker:
    """BGE-reranker-v2 cross-encoder。懒加载;务必在任何 Milvus/gRPC 之前 warmup
    (torch 加载会 fork,见 macos-embedding-milvus-fork-order)。"""

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from FlagEmbedding import FlagReranker
                    self._model = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
        return self._model

    def warmup(self) -> None:
        self._ensure()

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int = 6) -> list[RetrievedChunk]:
        if not chunks:
            return []
        model = self._ensure()
        scores = model.compute_score([[query, c.text] for c in chunks], normalize=True)
        if not isinstance(scores, list):
            scores = [scores]
        for c, s in zip(chunks, scores):
            c.score = float(s)
        return sorted(chunks, key=lambda c: c.score, reverse=True)[:top_k]
```

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_rerank.py -v
git add backend/epictrace/retrieval/rerank.py backend/tests/fakes.py backend/tests/test_rerank.py
git commit -m "feat(backend): BgeReranker(懒加载+warmup)+ FakeReranker" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: HybridRetriever 流水线

**Files:** Create `backend/epictrace/retrieval/pipeline.py`; Test `backend/tests/test_pipeline.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_pipeline.py`

```python
from epictrace.retrieval.pipeline import HybridRetriever
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _rec(rid, text):
    return {"vector": FakeEmbedder().embed([text])[0], "text": text, "ingest_record_id": rid,
            "project_id": 7, "char_start": 0, "char_end": len(text), "source_type": "folder_scan",
            "embed_model_id": "fake"}


def test_hybrid_retrieve_returns_top_k_reranked():
    store = FakeVectorStore()
    store.upsert([_rec(1, "虚拟内存 页表 分页"), _rec(2, "数据库 事务"), _rec(3, "页表 缺页")])
    r = HybridRetriever(FakeEmbedder(), store, FakeReranker())
    out = r.retrieve(project_id=7, query="页表", k=2)
    assert len(out) == 2
    assert out[0].ingest_record_id in {1, 3}  # 含"页表"的排前
    assert all(hasattr(c, "char_start") for c in out)
```

- [ ] **Step 2: 运行确认失败** — FAIL。

- [ ] **Step 3: 实现** `backend/epictrace/retrieval/pipeline.py`

```python
from __future__ import annotations

from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.dense import dense_search
from epictrace.retrieval.fuse import rrf_fuse
from epictrace.retrieval.sparse import sparse_search
from epictrace.retrieval.types import RetrievedChunk


class HybridRetriever:
    def __init__(self, embedder: EmbeddingProvider, store: VectorStore, reranker) -> None:
        self._embedder = embedder
        self._store = store
        self._reranker = reranker

    def retrieve(self, *, project_id: int, query: str, k: int = 6,
                 dense_n: int = 30, fuse_m: int = 20) -> list[RetrievedChunk]:
        dense = dense_search(self._embedder, self._store, project_id=project_id, query=query, k=dense_n)
        sparse = sparse_search(self._store, project_id=project_id, query=query, k=dense_n)
        fused = rrf_fuse([dense, sparse], k=fuse_m)
        if not fused:
            return []
        return self._reranker.rerank(query, fused, top_k=k)
```

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_pipeline.py -v
git add backend/epictrace/retrieval/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat(backend): HybridRetriever(dense+sparse→RRF→rerank→top-k)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: warmup 纪律扩展(reranker 也在 Milvus 前暖)

**Files:** Modify `backend/epictrace/api/deps.py`; Modify `backend/tests/test_index_real_smoke.py`(slow 回归加 reranker)

- [ ] **Step 1: 实现** — 在 `backend/epictrace/api/deps.py`:
  - 新增延迟单例 `get_reranker(request)`(同 `get_embedder` 模式,`app.state.reranker`,默认 `BgeReranker()`)。
  - 改 `get_vector_store`:在锁内构造 Milvus 之前,**先 `get_embedder(request).warmup()` 再 `get_reranker(request).warmup()`**,然后才建 `MilvusLiteStore`。
  - 新增 `get_llm(request)`:从 `request.app.state` 取已注入的 llm;若 None,用 `SettingsService` 读 `config.chat_llm` 构造 `OpenAICompatLLM`(Task 10/15 接入;此处可先留 `get_llm` 读 app.state.llm,默认 None → 由路由处理"未配置")。
  - 新增 `get_retriever(request)`:`HybridRetriever(get_embedder, get_vector_store, get_reranker)`。

具体(get_vector_store 锁内顺序):
```python
        if store is None:
            get_embedder(request).warmup()
            get_reranker(request).warmup()
            from epictrace.config import AppConfig
            from epictrace.vectorstore.milvus_lite import MilvusLiteStore
            store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024)
            request.app.state.vector_store = store
```

- [ ] **Step 2: slow 回归** — 在 `backend/tests/test_index_real_smoke.py` 末尾加(默认跳过):
```python
def test_hybrid_retrieve_real_models_no_segfault(tmp_path, monkeypatch):
    """真 embedder + 真 reranker + 真 Milvus 同进程检索一条,不应段错误。"""
    from types import SimpleNamespace

    import epictrace.api.deps as deps
    from epictrace.config import AppConfig
    from epictrace.retrieval.pipeline import HybridRetriever

    monkeypatch.setattr(AppConfig, "milvus_path", property(lambda self: str(tmp_path / "v.db")))
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(embedder=None, reranker=None, vector_store=None)))
    store = deps.get_vector_store(req)  # 先暖 embedder+reranker 再起 Milvus
    emb = deps.get_embedder(req)
    store.upsert([{ "vector": emb.embed(["虚拟内存 页表"])[0], "text": "虚拟内存 页表", "ingest_record_id": 1,
                    "project_id": 7, "char_start": 0, "char_end": 6, "source_type": "folder_scan",
                    "embed_model_id": emb.model_id }])
    out = HybridRetriever(emb, store, deps.get_reranker(req)).retrieve(project_id=7, query="页表", k=3)
    assert out and out[0].ingest_record_id == 1  # 进程没崩 + 检到
```

- [ ] **Step 3: 运行(快测试套绿;slow 仍跳过)+ 提交**
```bash
cd backend && .venv/bin/pytest -q
git add backend/epictrace/api/deps.py backend/tests/test_index_real_smoke.py
git commit -m "feat(backend): reranker 纳入 model-before-Milvus warmup;get_reranker/get_llm/get_retriever" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Conversation / Message 模型

**Files:** Modify `backend/epictrace/models.py`; Test `backend/tests/test_models_conversation.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_models_conversation.py`

```python
from pathlib import Path

from sqlalchemy import select

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project


def test_conversation_messages_and_cascade(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="问页表"); s.add(c); s.flush()
        s.add(Message(conversation_id=c.id, role="user", content="页表是啥"))
        s.add(Message(conversation_id=c.id, role="assistant", content="答[1]", citations_json="[]"))
        pid, cid = p.id, c.id
    with db.session() as s:
        msgs = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert len(msgs) == 2 and msgs[0].role == "user"
    # 删项目级联删会话+消息
    with db.session() as s:
        s.delete(s.get(Project, pid))
    with db.session() as s:
        assert s.execute(select(Conversation)).scalars().first() is None
        assert s.execute(select(Message)).scalars().first() is None
```

- [ ] **Step 2: 运行确认失败** — FAIL(ImportError Conversation/Message)。

- [ ] **Step 3: 实现** — 在 `backend/epictrace/models.py` 增(遵循现有 Base/列风格;`Project` 加 conversations 关系并级联):
```python
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(default="新对话")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column()  # user | assistant
    content: Mapped[str] = mapped_column(default="")
    citations_json: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
```
并在 `Project` 上加:`conversations: Mapped[list["Conversation"]] = relationship(cascade="all, delete-orphan")`。(import `ForeignKey`/`datetime` 若缺。)

- [ ] **Step 4: 运行确认通过 + 全套 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_models_conversation.py -q && .venv/bin/pytest -q
git add backend/epictrace/models.py backend/tests/test_models_conversation.py
git commit -m "feat(backend): Conversation/Message 模型(级联删)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 设置服务(任意 OpenAI-compat,持久化)

**Files:** Create `backend/epictrace/services/settings.py`; Modify `backend/epictrace/config.py`(从文件加载);Test `backend/tests/test_settings.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_settings.py`

```python
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService


def test_settings_roundtrip_and_masking(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.update_chat_llm(base_url="http://x", api_key="secret", model="m")
    loaded = svc.get_chat_llm()
    assert loaded.base_url == "http://x" and loaded.model == "m" and loaded.api_key == "secret"
    # 面板回传打码:不泄露明文,但能看出已设
    masked = svc.public_view()
    assert masked["chat_llm"]["api_key_set"] is True
    assert "secret" not in str(masked)


def test_settings_defaults_when_no_file(tmp_path: Path):
    v = SettingsService(AppConfig(data_dir=tmp_path)).public_view()
    assert v["chat_llm"]["api_key_set"] is False
```

- [ ] **Step 2: 运行确认失败** — FAIL。

- [ ] **Step 3: 实现** `backend/epictrace/services/settings.py`

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from epictrace.config import AppConfig


@dataclass
class ChatLLMSettings:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"


class SettingsService:
    """读写 ~/.epictrace/settings.json。本地单用户,明文存盘(桌面 APP)。"""

    def __init__(self, config: AppConfig) -> None:
        self._path = config.data_dir / "settings.json"

    def _read(self) -> dict:
        if self._path.exists():
            return json.loads(self._path.read_text(encoding="utf-8"))
        return {}

    def get_chat_llm(self) -> ChatLLMSettings:
        c = self._read().get("chat_llm", {})
        return ChatLLMSettings(
            base_url=c.get("base_url", ChatLLMSettings.base_url),
            api_key=c.get("api_key", ""),
            model=c.get("model", ChatLLMSettings.model),
        )

    def update_chat_llm(self, *, base_url: str, api_key: str, model: str) -> None:
        data = self._read()
        data["chat_llm"] = {"base_url": base_url, "api_key": api_key, "model": model}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def public_view(self) -> dict:
        c = self.get_chat_llm()
        return {"chat_llm": {"base_url": c.base_url, "model": c.model, "api_key_set": bool(c.api_key)}}
```
(注:`ChatLLMSettings.base_url` 默认值在 dataclass 里用 `c.get(..., "https://api.deepseek.com")` 显式给,避免引用类属性歧义——实现时写成字面量。)

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_settings.py -v
git add backend/epictrace/services/settings.py backend/tests/test_settings.py
git commit -m "feat(backend): SettingsService(任意 OpenAI-compat,~/.epictrace/settings.json,key 打码)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: 引用对齐(citations)

**Files:** Create `backend/epictrace/agent/__init__.py`(空)、`agent/citations.py`; Test `backend/tests/test_citations.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_citations.py`

```python
from epictrace.agent.citations import build_citations
from epictrace.retrieval.types import RetrievedChunk


def _c(rid, text):
    return RetrievedChunk(text=text, ingest_record_id=rid, project_id=7,
                          char_start=10, char_end=10 + len(text), source_type="folder_scan")


def test_build_citations_keeps_only_referenced_numbers():
    chunks = [_c(1, "页表把虚拟地址映射到物理地址"), _c(2, "无关"), _c(3, "缺页中断触发换页")]
    answer = "页表负责地址映射[1]。缺页时会换页[3]。"
    cites = build_citations(answer, chunks)
    ns = {c["n"] for c in cites}
    assert ns == {1, 3}  # 只保留答案里实际出现的 [n],丢弃 [2]
    c1 = next(c for c in cites if c["n"] == 1)
    assert c1["ingest_record_id"] == 1 and c1["char_start"] == 10 and "页表" in c1["snippet"]


def test_build_citations_ignores_out_of_range_numbers():
    cites = build_citations("乱标[9]", [_c(1, "x")])
    assert cites == []
```

- [ ] **Step 2: 运行确认失败** — FAIL。

- [ ] **Step 3: 实现** `backend/epictrace/agent/citations.py`

```python
from __future__ import annotations

import re

from epictrace.retrieval.types import RetrievedChunk

_CITE = re.compile(r"\[(\d+)\]")
_SNIPPET = 160


def build_citations(answer: str, chunks: list[RetrievedChunk]) -> list[dict]:
    """从答案里抽 [n],映射到第 n 个 chunk(1-based);只保留有效且实际出现的。"""
    used = sorted({int(m) for m in _CITE.findall(answer)})
    out = []
    for n in used:
        if 1 <= n <= len(chunks):
            c = chunks[n - 1]
            out.append({
                "n": n, "ingest_record_id": c.ingest_record_id,
                "char_start": c.char_start, "char_end": c.char_end,
                "source_type": c.source_type,
                "snippet": c.text[:_SNIPPET],
            })
    return out
```

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_citations.py -v
git add backend/epictrace/agent/ backend/tests/test_citations.py
git commit -m "feat(backend): 引用对齐(只留答案实际出现的 [n] → chunk 元数据)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: LangGraph 智能体环

**Files:** Create `backend/epictrace/agent/state.py`、`agent/prompts.py`、`agent/graph.py`; Test `backend/tests/test_graph.py`

- [ ] **Step 1: 装依赖** — `cd backend && .venv/bin/pip install langgraph` 并加入 `pyproject.toml`。

- [ ] **Step 2: 写失败测试** `backend/tests/test_graph.py`(用 FakeLLM 编排 grade/answer + 假 retriever)

```python
from epictrace.agent.graph import build_rag_graph
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeLLM


def _chunks(*texts):
    return [RetrievedChunk(text=t, ingest_record_id=i + 1, project_id=7, char_start=0,
                           char_end=len(t), source_type="folder_scan") for i, t in enumerate(texts)]


class _Retriever:
    def __init__(self, by_query): self.by_query = by_query; self.calls = []
    def retrieve(self, *, project_id, query, k=6):
        self.calls.append(query)
        return self.by_query.get(query, [])


def test_sufficient_retrieval_ends_after_one_retrieve():
    retr = _Retriever({"页表是什么": _chunks("页表映射地址")})
    graph = build_rag_graph(FakeLLM(grade="sufficient"), retr)
    out = graph.invoke({"project_id": 7, "question": "页表是什么", "query": "页表是什么",
                        "history": [], "iterations": 0})
    assert len(retr.calls) == 1                       # 足够 → 不改写
    assert out["chunks"][0].text == "页表映射地址"


def test_insufficient_triggers_rewrite_then_retrieves_again():
    retr = _Retriever({"页表是什么": [], "页表 虚拟内存 分页": _chunks("页表与分页")})
    llm = FakeLLM(grade_sequence=["insufficient", "sufficient"], rewrite="页表 虚拟内存 分页")
    graph = build_rag_graph(llm, retr)
    out = graph.invoke({"project_id": 7, "question": "页表是什么", "query": "页表是什么",
                        "history": [], "iterations": 0})
    assert retr.calls == ["页表是什么", "页表 虚拟内存 分页"]
    assert out["chunks"][0].text == "页表与分页"


def test_iteration_cap_stops_retrying():
    retr = _Retriever({})  # 永远检不到
    graph = build_rag_graph(FakeLLM(grade="insufficient", rewrite="x"), retr, max_iterations=2)
    out = graph.invoke({"project_id": 7, "question": "q", "query": "q", "history": [], "iterations": 0})
    assert len(retr.calls) <= 3                       # 初次 + 最多 2 次改写后停
```

- [ ] **Step 3: 运行确认失败** — FAIL。

- [ ] **Step 4: 实现**

`backend/epictrace/agent/state.py`:
```python
from __future__ import annotations

from typing import TypedDict

from epictrace.retrieval.types import RetrievedChunk


class AgentState(TypedDict, total=False):
    project_id: int
    question: str
    query: str
    history: list[dict]
    chunks: list[RetrievedChunk]
    iterations: int
    _grade: str          # grade 节点写、decide 读;不生成答案(交给 ChatService 流式)
```

`backend/epictrace/agent/prompts.py`:
```python
GRADE_SYS = "你判断给定资料是否足以回答问题。只回一个词:sufficient 或 insufficient。"
REWRITE_SYS = "资料不足。基于问题与已有资料的缺口,改写出一个更可能检索到答案的中文查询,只回查询本身。"
GENERATE_SYS = (
    "你是基于资料作答的助手。只用提供的【资料】回答,凡用到某条资料就在句末标注其编号 [n]"
    "(n 为资料序号,可多个);不要编造资料没有的内容;用中文。"
)


def format_chunks(chunks) -> str:
    return "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))
```

`backend/epictrace/agent/graph.py`:
```python
from __future__ import annotations

from langgraph.graph import END, StateGraph

from epictrace.agent.prompts import GRADE_SYS, REWRITE_SYS, format_chunks
from epictrace.agent.state import AgentState


def build_rag_graph(llm, retriever, max_iterations: int = 2):
    """智能体检索环:retrieve → grade(反思充分性)→ 不足则 rewrite→retrieve(有界),
    足够或到上限即结束。终态输出最终 chunks/query;**答案不在图内生成**——由 ChatService
    流式生成唯一一次(避免双重 LLM 调用)。"""

    def retrieve(state: AgentState) -> AgentState:
        chunks = retriever.retrieve(project_id=state["project_id"], query=state["query"])
        return {"chunks": chunks}

    def grade(state: AgentState) -> AgentState:
        if not state.get("chunks"):
            return {"_grade": "insufficient"}
        verdict = llm.complete([
            {"role": "system", "content": GRADE_SYS},
            {"role": "user", "content": f"问题:{state['question']}\n\n资料:\n{format_chunks(state['chunks'])}"},
        ]).strip().lower()
        return {"_grade": "insufficient" if "insufficient" in verdict else "sufficient"}

    def decide(state: AgentState) -> str:
        if state.get("_grade") == "sufficient":
            return "end"
        if state.get("iterations", 0) >= max_iterations:
            return "end"
        return "rewrite"

    def rewrite(state: AgentState) -> AgentState:
        new_q = llm.complete([
            {"role": "system", "content": REWRITE_SYS},
            {"role": "user", "content": f"问题:{state['question']}\n原查询:{state['query']}"},
        ]).strip()
        return {"query": new_q or state["query"], "iterations": state.get("iterations", 0) + 1}

    g = StateGraph(AgentState)
    g.add_node("retrieve", retrieve)
    g.add_node("grade", grade)
    g.add_node("rewrite", rewrite)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", decide, {"end": END, "rewrite": "rewrite"})
    g.add_edge("rewrite", "retrieve")
    return g.compile()
```
> 注:`AgentState` 含 `_grade` 临时键(grade 写、decide 读)。`FakeLLM` 支持 `grade`/`grade_sequence`/`rewrite` 编排(定义见 Task 13,实现 Task 12 时先放进 `tests/fakes.py`)。**图不生成答案**,只定最终 chunks。

- [ ] **Step 5: 实现 FakeLLM + 运行确认通过 + 提交** — 在 `backend/tests/fakes.py` 增 `FakeLLM`(见 Task 13 Step 1 的定义,先放进来),然后:
```bash
cd backend && .venv/bin/pytest tests/test_graph.py -v
git add backend/epictrace/agent/ backend/tests/test_graph.py backend/tests/fakes.py backend/pyproject.toml
git commit -m "feat(backend): LangGraph RAG 环(retrieve→grade→rewrite/generate,有界反思)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: ChatService(跑图 + 流式事件 + 落库)

**Files:** Modify `backend/tests/fakes.py`(`FakeLLM`,若 Task 12 未加则此处加全);Create `backend/epictrace/services/chat.py`; Test `backend/tests/test_chat_service.py`

`FakeLLM` 定义(放 `backend/tests/fakes.py`):
```python
class FakeLLM:
    """可编排:grade(固定)或 grade_sequence(逐次)、rewrite、answer。记录收到的 system 提示以分流。"""

    def __init__(self, *, grade="sufficient", grade_sequence=None, rewrite="改写后的查询", answer="假答案[1]"):
        self._grade = grade
        self._grade_seq = list(grade_sequence) if grade_sequence else None
        self._rewrite = rewrite
        self._answer = answer

    def _route(self, messages):
        sys = messages[0]["content"]
        if "sufficient" in sys:  # GRADE_SYS
            if self._grade_seq:
                return self._grade_seq.pop(0) if self._grade_seq else "sufficient"
            return self._grade
        if "改写" in sys:  # REWRITE_SYS
            return self._rewrite
        return self._answer  # GENERATE_SYS

    def complete(self, messages, **kwargs):
        return self._route(messages)

    def stream(self, messages, **kwargs):
        for ch in self._route(messages):
            yield ch
```

- [ ] **Step 1: 写失败测试** `backend/tests/test_chat_service.py`

```python
import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeLLM


class _Retriever:
    def retrieve(self, *, project_id, query, k=6):
        return [RetrievedChunk(text="页表映射地址", ingest_record_id=1, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def test_stream_emits_events_and_persists(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="地址映射靠页表[1]。"), _Retriever())
    events = list(svc.stream_answer(cid, "页表是什么"))
    kinds = [e["event"] for e in events]
    assert "status" in kinds and "token" in kinds and "citations" in kinds and kinds[-1] == "done"
    answer = "".join(e["data"] for e in events if e["event"] == "token")
    assert "页表" in answer
    cite_evt = next(e for e in events if e["event"] == "citations")
    assert json.loads(cite_evt["data"])[0]["ingest_record_id"] == 1
    # 落库:user + assistant 两条
    from sqlalchemy import select
    with db.session() as s:
        msgs = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].citations_json and "ingest_record_id" in msgs[1].citations_json
```

- [ ] **Step 2: 运行确认失败** — FAIL。

- [ ] **Step 3: 实现** `backend/epictrace/services/chat.py`

```python
from __future__ import annotations

import json
from collections.abc import Iterator

from epictrace.agent.citations import build_citations
from epictrace.agent.graph import build_rag_graph
from epictrace.agent.prompts import GENERATE_SYS, format_chunks
from epictrace.db import Database
from epictrace.models import Conversation, Message


class ChatService:
    def __init__(self, db: Database, llm, retriever) -> None:
        self._db = db
        self._llm = llm
        self._retriever = retriever

    def stream_answer(self, conversation_id: int, question: str) -> Iterator[dict]:
        # 落 user message
        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="user", content=question))

        yield {"event": "status", "data": "检索中"}
        # 跑图到拿到最终 chunks(grade/rewrite 在图里),但生成改为这里流式
        graph = build_rag_graph(self._llm, self._retriever)
        state = graph.invoke({"project_id": self._project_id(conversation_id), "question": question,
                              "query": question, "history": [], "iterations": 0})
        chunks = state.get("chunks", [])

        yield {"event": "status", "data": "生成中"}
        # 流式生成最终答案(用与图内 generate 相同的提示词)
        parts: list[str] = []
        for tok in self._llm.stream([
            {"role": "system", "content": GENERATE_SYS},
            {"role": "user", "content": f"问题:{question}\n\n【资料】\n{format_chunks(chunks)}"},
        ]):
            parts.append(tok)
            yield {"event": "token", "data": tok}
        answer = "".join(parts)

        citations = build_citations(answer, chunks)
        yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}

        with self._db.session() as s:
            s.add(Message(conversation_id=conversation_id, role="assistant", content=answer,
                          citations_json=json.dumps(citations, ensure_ascii=False)))
        yield {"event": "done", "data": ""}

    def _project_id(self, conversation_id: int) -> int:
        with self._db.session() as s:
            c = s.get(Conversation, conversation_id)
            return c.project_id if c else 0
```
> 设计取舍:图负责 检索+反思+改写(确定最终 chunks),最终答案在 ChatService 里**流式**重生成一次(图内 generate 仅用于非流式路径/测试)。FakeLLM 的 `stream` 与 `complete` 返回同一答案,事件序列可测。

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_chat_service.py -v
git add backend/epictrace/services/chat.py backend/tests/test_chat_service.py backend/tests/fakes.py
git commit -m "feat(backend): ChatService(跑图定 chunks + 流式生成 + 引用 + 落库)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: SourceService(来源查看器后端)

**Files:** Create `backend/epictrace/services/source.py`; Test `backend/tests/test_source_service.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_source_service.py`

```python
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import IngestRecord, Project
from epictrace.services.source import SourceService


def test_source_reextracts_text_for_record(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    f = tmp_path / "note.md"; f.write_text("虚拟内存与页表", encoding="utf-8")
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        r = IngestRecord(project_id=p.id, filename="note.md", stored_path=str(f),
                         sha256="x", size_bytes=f.stat().st_size, ingest_method="folder_scan",
                         extracted_text="", indexed=True)
        s.add(r); s.flush(); rid = r.id
    out = SourceService(db).get_text(rid)
    assert out["filename"] == "note.md"
    assert out["text"] == "虚拟内存与页表"


def test_source_unknown_record_raises(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    import pytest
    with pytest.raises(ValueError):
        SourceService(db).get_text(99999)
```
(注:`IngestRecord` 的实际字段名以仓库现有为准,实现时对齐 `stored_path` 等列名。)

- [ ] **Step 2: 运行确认失败** — FAIL。

- [ ] **Step 3: 实现** `backend/epictrace/services/source.py`

```python
from __future__ import annotations

from pathlib import Path

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import IngestRecord


class SourceService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get_text(self, ingest_record_id: int) -> dict:
        with self._db.session() as s:
            rec = s.get(IngestRecord, ingest_record_id)
            if rec is None:
                raise ValueError("ingest record not found")
            path = Path(rec.stored_path)
            filename = rec.filename
        proc = get_processor(path)
        text = proc.process(path).text if proc is not None else ""
        return {"filename": filename, "path": str(path), "text": text}
```

- [ ] **Step 4: 运行确认通过 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_source_service.py -v
git add backend/epictrace/services/source.py backend/tests/test_source_service.py
git commit -m "feat(backend): SourceService(按 record_id 重提取文本供来源查看器)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: API(会话 SSE + source + settings)+ 原生揭示

**Files:** Modify `backend/epictrace/schemas.py`、`backend/epictrace/api/app.py`(挂路由 + app.state.llm/reranker)、`backend/epictrace/api/deps.py`(get_llm 用 SettingsService);Create `backend/epictrace/api/routers/conversations.py`、`source.py`、`settings.py`;Modify `shell/run.py`(reveal_in_finder);Test `backend/tests/test_api_chat.py`、`test_api_settings.py`

- [ ] **Step 1: 装依赖** — `cd backend && .venv/bin/pip install sse-starlette` 并加入 `pyproject.toml`。

- [ ] **Step 2: 写失败测试** `backend/tests/test_api_settings.py`

```python
import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


@pytest.fixture()
def app_client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    return TestClient(create_app(db=db))


def test_settings_put_then_get_masks_key(app_client):
    r = app_client.put("/api/settings", json={"chat_llm": {"base_url": "http://x", "api_key": "secret", "model": "m"}})
    assert r.status_code == 200
    got = app_client.get("/api/settings").json()
    assert got["chat_llm"]["model"] == "m" and got["chat_llm"]["api_key_set"] is True
    assert "secret" not in str(got)
```

`backend/tests/test_api_chat.py`(注入 FakeLLM + FakeReranker + tmp Milvus + FakeEmbedder):
```python
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.retrieval.pipeline import HybridRetriever
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from tests.fakes import FakeEmbedder, FakeLLM, FakeReranker


@pytest.fixture()
def chat_client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    emb = FakeEmbedder()
    retriever = HybridRetriever(emb, store, FakeReranker())
    llm = FakeLLM(grade="sufficient", answer="页表用于地址映射[1]。")
    app = create_app(db=db, embedder=emb, vector_store=store, reranker=FakeReranker(),
                     llm=llm, retriever=retriever)
    return TestClient(app), db, store, emb


def test_chat_flow_creates_conversation_streams_and_cites(chat_client, tmp_path):
    client, db, store, emb = chat_client
    folder = tmp_path / "P"
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    store.upsert([{ "vector": emb.embed(["页表映射地址"])[0], "text": "页表映射地址", "ingest_record_id": 1,
                    "project_id": pid, "char_start": 0, "char_end": 6, "source_type": "folder_scan",
                    "embed_model_id": "fake" }])
    cid = client.post(f"/api/projects/{pid}/conversations", json={}).json()["id"]

    with client.stream("POST", f"/api/conversations/{cid}/messages", json={"content": "页表是什么"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    assert "event: token" in body and "event: citations" in body and "event: done" in body

    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert json.loads(msgs[1]["citations_json"])[0]["ingest_record_id"] == 1


def test_send_message_without_llm_configured_returns_409(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    app = create_app(db=db)  # llm=None 且 settings 无 key
    client = TestClient(app)
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "P")}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/conversations", json={}).json()["id"]
    r = client.request("POST", f"/api/conversations/{cid}/messages", json={"content": "x"})
    assert r.status_code == 409  # 未配置对话模型
```

- [ ] **Step 3: 运行确认失败** — FAIL。

- [ ] **Step 4: 实现**

`backend/epictrace/schemas.py` 增:`ConversationOut`(id/project_id/title/created_at)、`MessageOut`(id/role/content/citations_json/created_at)、`ConversationCreate`(可空 title)、`MessageCreate`(content)、`SourceOut`(filename/path/text)、`SettingsIn`/`SettingsOut`。

`create_app` 增可注入 `reranker=None, llm=None, retriever=None`,挂到 `app.state`(默认 None → deps 懒构造);**并设 `app.state.config = <db 的 AppConfig 或 config 参数,默认 AppConfig()>`**(settings/get_llm 都用它,保证 tmp data_dir 测试隔离);挂载 routers `conversations`/`source`/`settings`(prefix `/api`)。

`api/deps.py` `get_llm(request)`:若 `app.state.llm` 非空用之;否则 `SettingsService(request.app.state.config).get_chat_llm()` → 有 `api_key` 则构造 `OpenAICompatLLM` 并缓存到 `app.state.llm`;无 key 返回 `None`。`get_retriever(request)`:`app.state.retriever` 或用 `HybridRetriever(get_embedder, get_vector_store, get_reranker)` 构造。

`api/routers/conversations.py`:
```python
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from epictrace.api.deps import get_db, get_llm, get_retriever
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project
from epictrace.schemas import ConversationCreate, ConversationOut, MessageCreate, MessageOut
from epictrace.services.chat import ChatService

router = APIRouter(tags=["conversations"])


@router.post("/projects/{project_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(project_id: int, payload: ConversationCreate, db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(404, "project not found")
        c = Conversation(project_id=project_id, title=payload.title or "新对话")
        s.add(c); s.flush(); s.refresh(c)
        return ConversationOut.model_validate(c)


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
def list_conversations(project_id: int, db: Database = Depends(get_db)):
    from sqlalchemy import select
    with db.session() as s:
        rows = s.execute(select(Conversation).where(Conversation.project_id == project_id)
                         .order_by(Conversation.updated_at.desc())).scalars()
        return [ConversationOut.model_validate(c) for c in rows]


@router.get("/conversations/{cid}/messages", response_model=list[MessageOut])
def list_messages(cid: int, db: Database = Depends(get_db)):
    from sqlalchemy import select
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(404, "conversation not found")
        rows = s.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.id)).scalars()
        return [MessageOut.model_validate(m) for m in rows]


@router.post("/conversations/{cid}/messages")
def send_message(cid: int, payload: MessageCreate, request: Request, db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(404, "conversation not found")
    llm = get_llm(request)
    if llm is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "对话模型未配置:请在设置里填写 OpenAI-Compatible 端点")
    svc = ChatService(db, llm, get_retriever(request))

    def gen():
        for e in svc.stream_answer(cid, payload.content):
            yield {"event": e["event"], "data": e["data"]}

    return EventSourceResponse(gen())
```

`api/routers/source.py`:
```python
from fastapi import APIRouter, Depends, HTTPException

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.schemas import SourceOut
from epictrace.services.source import SourceService

router = APIRouter(tags=["source"])


@router.get("/source/{ingest_record_id}", response_model=SourceOut)
def get_source(ingest_record_id: int, db: Database = Depends(get_db)):
    try:
        return SourceOut(**SourceService(db).get_text(ingest_record_id))
    except ValueError:
        raise HTTPException(404, "source not found")
```

`api/routers/settings.py`:
```python
from fastapi import APIRouter, Request

from epictrace.schemas import SettingsIn
from epictrace.services.settings import SettingsService

router = APIRouter(tags=["settings"])


# 用 app.state.config(create_app 注入,测试为 tmp data_dir)而非新建 AppConfig(),保证隔离。
@router.get("/settings")
def get_settings(request: Request):
    return SettingsService(request.app.state.config).public_view()


@router.put("/settings")
def put_settings(payload: SettingsIn, request: Request):
    svc = SettingsService(request.app.state.config)
    svc.update_chat_llm(base_url=payload.chat_llm.base_url, api_key=payload.chat_llm.api_key,
                        model=payload.chat_llm.model)
    request.app.state.llm = None  # 失效缓存,下次按新设置重建
    return svc.public_view()
```

`shell/run.py` 的 `Api` 类增原生揭示:
```python
    def reveal_in_finder(self, path):
        import subprocess
        subprocess.run(["open", "-R", path])
```

- [ ] **Step 5: 运行确认通过 + 全套 + 提交**
```bash
cd backend && .venv/bin/pytest tests/test_api_settings.py tests/test_api_chat.py -q && .venv/bin/pytest -q
git add backend/epictrace/ backend/tests/test_api_chat.py backend/tests/test_api_settings.py shell/run.py backend/pyproject.toml
git commit -m "feat(backend): 会话 SSE / source / settings 路由 + 原生 reveal_in_finder" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: 真模型端到端 slow 冒烟

**Files:** Create `backend/tests/test_rag_real_smoke.py`(默认跳;不需要真 LLM key——只验检索+重排+图编排用 FakeLLM,真 embedder/reranker/Milvus 不崩)

- [ ] **Step 1: 写测试**(标 `EPICTRACE_RUN_SLOW`)
```python
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("EPICTRACE_RUN_SLOW") != "1",
                                reason="真 embedder+reranker;设 EPICTRACE_RUN_SLOW=1")


def test_real_hybrid_retrieve_end_to_end(tmp_path: Path):
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.retrieval.pipeline import HybridRetriever
    from epictrace.retrieval.rerank import BgeReranker
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    emb = BgeM3Embedder(); emb.warmup()
    rer = BgeReranker(); rer.warmup()                      # 两个模型都在 Milvus 前加载
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    for i, t in enumerate(["虚拟内存通过页表把虚拟地址映射到物理地址", "数据库事务的隔离级别", "缺页中断与按需调页"]):
        store.upsert([{ "vector": emb.embed([t])[0], "text": t, "ingest_record_id": i + 1, "project_id": 1,
                        "char_start": 0, "char_end": len(t), "source_type": "folder_scan", "embed_model_id": "bge-m3" }])
    out = HybridRetriever(emb, store, rer).retrieve(project_id=1, query="页表怎么映射地址", k=2)
    assert out and "页表" in out[0].text       # 进程没崩 + 语义最相关排第一
```

- [ ] **Step 2: 运行(默认 SKIPPED;可选真跑)+ 提交**
```bash
cd backend && .venv/bin/pytest tests/test_rag_real_smoke.py -q     # SKIPPED
# 可选:EPICTRACE_RUN_SLOW=1 .venv/bin/pytest tests/test_rag_real_smoke.py -q
git add backend/tests/test_rag_real_smoke.py
git commit -m "test(backend): RAG 真模型端到端 slow 冒烟(检索+重排+Milvus 不段错误)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: 前端接通对话(impeccable)

**Files:** Modify `frontend/src/lib/api.ts`、`frontend/src/lib/pickers.ts`(reveal);Create `frontend/src/components/Composer.tsx`、`MessageList.tsx`、`SourceViewer.tsx`、`SettingsModal.tsx`、`ConversationList.tsx`;Modify `frontend/src/views/ProjectsConversationView.tsx`、`TopBar.tsx`(齿轮入口)

**逻辑契约(固定);视觉用 `impeccable`,保持 chat-first 冷静风 + 桌面原生。**

- `lib/api.ts` 增:
  ```ts
  export interface Citation { n:number; ingest_record_id:number; char_start:number; char_end:number; snippet:string; source_type:string }
  export interface ChatMessage { id:number; role:"user"|"assistant"; content:string; citations_json:string|null }
  listConversations(projectId), createConversation(projectId,title?), listMessages(cid),
  getSource(recordId) -> {filename,path,text},
  getSettings(), putSettings({chat_llm:{base_url,api_key,model}}),
  // SSE:用 fetch + ReadableStream 解析 `event:`/`data:` 行,回调 onStatus/onToken/onCitations/onDone
  sendMessage(cid, content, {onStatus,onToken,onCitations,onDone,onError})
  ```
- `ConversationList`:侧栏(在 ProjectSidebar 选中项目下)列该项目会话,建/选;替换「No chats」占位。
- `Composer`:输入框 + 发送;**无 LLM key 时禁用** + 提示「先在设置里配置对话模型」(齿轮跳设置)。
- `MessageList`:渲染消息;assistant 内容里的 `[n]` → 可点引用 chip;流式时显示状态行(`检索中…/生成中…`)+ 增量 token。
- 点引用 chip → `SourceViewer`(模态):`getSource(record_id)` → 显示 `text`,**高亮 `char_start..char_end`** 并滚动到该处;顶部「在 Finder 中显示」→ `window.pywebview.api.reveal_in_finder(path)`。
- `SettingsModal`(TopBar 齿轮):base_url/api_key/model + 预设占位(DeepSeek/OpenAI/Ollama);保存调 `putSettings`,成功后 Composer 解禁。
- `ProjectsConversationView`:把 chat-first 主区接通——选/建会话 → 历史消息 → Composer 发送走 SSE → 流式渲染。

- [ ] **Step 1**(impeccable)实现上述五个组件 + view/TopBar 接线 + api.ts。
- [ ] **Step 2** `cd frontend && npm run build` 成功。
- [ ] **Step 3** 提交:`git commit -m "feat(frontend): 接通带引用流式对话 + 来源查看器 + 会话历史 + LLM 设置面板"`(+trailer)。

**验收:** 设置里填一个 OpenAI-compat 端点 → 选项目建会话 → 提问 → 看到 `检索中/生成中` + 流式答案 + `[n]` chip → 点 chip 进来源查看器高亮 → Finder 揭示。

---

## Verification(端到端)
1. 后端全绿(假件):`cd backend && .venv/bin/pytest -q`(真模型 slow SKIPPED)。
2. 真模型检索冒烟(可选):`cd backend && EPICTRACE_RUN_SLOW=1 .venv/bin/pytest tests/test_rag_real_smoke.py tests/test_index_real_smoke.py -q`。
3. 前端构建:`cd frontend && npm run build`。
4. 打包态手测:设置填端点 → 建项目→扫描→建立索引 → 项目与对话提问 → 流式带引用 → 点引用高亮跳回 → Finder 揭示。
5. 代号:全仓库(代码/前端/docs/shell)不出现任何前身原型代号(用 `grep -rni` 查对应代号应为空)。

## Self-Review(覆盖 spec)
- ✅ §3 检索:dense(T3)+sparse(T4)+RRF(T5)+rerank(T6)+pipeline(T7);list_by_project(T2)。
- ✅ §4 LangGraph 环(T12);§5 引用(T11);§6 来源查看器(T14 后端 + T17 前端);§7 持久化(T9);§8 设置(T10+T15);§9 API(T15);§10 前端(T17);§11 warmup 扩展(T8);§12 测试(各任务假件 + T16 slow)。
- ✅ §2 OpenAICompatLLM(T1);流式贯穿 ChatService(T13)+SSE(T15)+前端(T17)。
- ⛔ §13 延后:Langfuse、图片/音频、跨项目、session 引用——本期不做。
- 注:**图不生成答案**(只跑 retrieve/grade/rewrite 定最终 chunks);答案由 ChatService 流式生成唯一一次(T13),不双重调用 LLM。`AgentState` 含临时 `_grade` 键。settings/get_llm 用 `app.state.config`(非新建 `AppConfig()`)保证测试隔离。

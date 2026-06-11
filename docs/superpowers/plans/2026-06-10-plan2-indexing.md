# Plan 2: Indexing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Backend tasks are TDD with pytest. The frontend task uses the `frontend-design`/`impeccable` skill for polish and is gated on `npm run build` + a described manual check. Steps use checkbox (`- [ ]`).

**Goal:** 把占位的「建立索引」接通:待索引文件 →(索引时)MediaProcessor 提取(text/pdf/docx/pptx)→ 自写切分器(保字符偏移)→ 进程内 BGE-M3 嵌入 → Milvus Lite 存储 → 文件翻成「已索引」;按项目后台触发 + 进度。

**Architecture:** 后端新增 Chunker、pdf/docx/pptx MediaProcessor、进程内 `BgeM3Embedder`(落地 `EmbeddingProvider`)、`MilvusLiteStore`(落地 `VectorStore`)、`IndexService`(编排 + 后台 job)。ScanService 改为只登记(提取移到索引时)。图片/音频本期跳过(标"需多媒体处理")。

**Tech Stack:** Python 3.11(venv)· FastAPI · SQLAlchemy · pytest · pypdf · python-docx · python-pptx · pymilvus(milvus-lite)· FlagEmbedding(BGE-M3, torch)· React/Tailwind/shadcn(前端接线)

**Spec:** `docs/superpowers/specs/2026-06-10-epictrace-plan2-indexing-design.md`。约定:不出现前身代号;git 身份 `ep1sode-33`(plain commit,无 `-c`,每条带 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` 尾);分支见下;venv 用 `.venv/bin/<tool>`,不用 uv。

**Branch:** 先建并切到 `feat/plan2-indexing`(从 `main`)。`git -C <root> checkout -b feat/plan2-indexing`。

---

## File Structure

```
backend/epictrace/
  indexing/__init__.py
  indexing/chunker.py          # Chunk + chunk_text(char-offset precise)
  media/pdf.py                 # PdfMediaProcessor (pypdf)
  media/docx.py                # DocxMediaProcessor (python-docx)
  media/pptx.py                # PptxMediaProcessor (python-pptx)
  media/__init__.py            # MODIFY: 注册新 processor
  embedding/__init__.py
  embedding/bge_m3.py          # BgeM3Embedder(进程内,落地 EmbeddingProvider)
  vectorstore/__init__.py
  vectorstore/milvus_lite.py   # MilvusLiteStore(落地 VectorStore)
  interfaces/vector_store.py   # MODIFY: + delete_by_record
  services/scan.py             # MODIFY: 只登记(去掉扫描时提取)
  services/index.py            # IndexService + job 状态
  config.py                    # MODIFY: milvus_path property
  schemas.py                   # MODIFY: IndexStatusOut
  api/app.py                   # MODIFY: create_app 接入 embedder + vector_store(可注入)
  api/routers/projects.py      # MODIFY: + POST /{id}/index, GET /{id}/index/status
backend/tests/
  fakes.py                     # FakeEmbedder(确定性 1024 维,测试用)
  test_chunker.py
  test_media_docs.py
  test_scan_service.py         # MODIFY(register-only)
  test_vectorstore_milvus.py
  test_index_service.py
  test_api_index.py
  test_bge_m3_smoke.py         # slow,默认跳过
```

---

## Task 1: Chunker(字符偏移精确)

**Files:** Create `backend/epictrace/indexing/__init__.py`(空)、`backend/epictrace/indexing/chunker.py`; Test `backend/tests/test_chunker.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_chunker.py`

```python
from epictrace.indexing.chunker import Chunk, chunk_text


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []


def test_short_text_single_chunk_exact_offsets():
    t = "hello world"
    chunks = chunk_text(t, target=1800, overlap=200)
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert (c.char_start, c.char_end) == (0, len(t))
    assert t[c.char_start:c.char_end] == c.text


def test_offsets_always_match_source_substring():
    t = ("段落一。" * 200) + "\n\n" + ("paragraph two. " * 200)
    chunks = chunk_text(t, target=400, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert t[c.char_start:c.char_end] == c.text   # 偏移必须对得上原文
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(t)               # 覆盖到结尾


def test_consecutive_chunks_overlap_and_progress():
    t = "x" * 2000
    chunks = chunk_text(t, target=500, overlap=100)
    for a, b in zip(chunks, chunks[1:]):
        assert b.char_start < a.char_end      # 有重叠
        assert b.char_start > a.char_start    # 一直前进,不死循环
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_chunker.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.indexing.chunker`

- [ ] **Step 3: 实现** `backend/epictrace/indexing/chunker.py`

```python
from __future__ import annotations

from dataclasses import dataclass

# 不引入 tokenizer 依赖:用字符数近似 token(~4 字符/token)。
DEFAULT_TARGET = 1800   # ~约 450-512 token
DEFAULT_OVERLAP = 200

# 优先级从高到低的断句点(在窗口尾部就近找一个,避免把句子切碎)。
_BOUNDARIES = ["\n\n", "\n", "。", "! ", "? ", ". ", "!", "?", ";", ";"]


@dataclass(frozen=True)
class Chunk:
    text: str
    char_start: int
    char_end: int


def _find_break(window: str, min_end: int) -> int | None:
    """在 window 内、位置 >= min_end 处,返回某个边界'之后'的索引;找不到返回 None。"""
    best = None
    for b in _BOUNDARIES:
        idx = window.rfind(b)
        if idx != -1 and idx + len(b) >= min_end:
            best = max(best or 0, idx + len(b))
    return best


def chunk_text(
    text: str, target: int = DEFAULT_TARGET, overlap: int = DEFAULT_OVERLAP
) -> list[Chunk]:
    if not text:
        return []
    n = len(text)
    chunks: list[Chunk] = []
    start = 0
    while start < n:
        end = min(start + target, n)
        if end < n:
            window = text[start:end]
            brk = _find_break(window, min_end=overlap)  # 至少要比 overlap 大,避免碎块
            if brk is not None:
                end = start + brk
        chunks.append(Chunk(text=text[start:end], char_start=start, char_end=end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)  # 带重叠前进,保证 start 严格递增
    return chunks
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_chunker.py -v`
Expected: PASS(4 测试)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/indexing/ backend/tests/test_chunker.py
git commit -m "feat(backend): 字符偏移精确的 Chunker" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: pdf/docx/pptx MediaProcessor + 注册表

**Files:** Create `backend/epictrace/media/pdf.py`、`docx.py`、`pptx.py`; Modify `backend/epictrace/media/__init__.py`; Test `backend/tests/test_media_docs.py`

- [ ] **Step 1: 装依赖**

Run:
```bash
cd backend
.venv/bin/pip install pypdf python-docx python-pptx
.venv/bin/pip install reportlab   # 仅测试里用来生成临时 PDF
```
把 `pypdf`、`python-docx`、`python-pptx` 加入 `pyproject.toml` 的 `dependencies`,`reportlab` 加入 `[project.optional-dependencies].dev`。

- [ ] **Step 2: 写失败测试** `backend/tests/test_media_docs.py`

```python
from pathlib import Path

from epictrace.media import get_processor


def test_docx_extraction(tmp_path: Path):
    from docx import Document
    p = tmp_path / "a.docx"
    doc = Document(); doc.add_paragraph("虚拟内存"); doc.add_paragraph("page table"); doc.save(p)
    proc = get_processor(p)
    assert proc is not None
    text = proc.process(p).text
    assert "虚拟内存" in text and "page table" in text


def test_pptx_extraction(tmp_path: Path):
    from pptx import Presentation
    p = tmp_path / "a.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Slide One"
    prs.save(p)
    proc = get_processor(p)
    assert proc is not None
    assert "Slide One" in proc.process(p).text


def test_pdf_extraction(tmp_path: Path):
    from reportlab.pdfgen import canvas
    p = tmp_path / "a.pdf"
    c = canvas.Canvas(str(p)); c.drawString(72, 720, "Hello PDF world"); c.save()
    proc = get_processor(p)
    assert proc is not None
    assert "Hello PDF" in proc.process(p).text


def test_unknown_type_returns_none(tmp_path: Path):
    assert get_processor(tmp_path / "a.png") is None    # 图片本期无 processor
    assert get_processor(tmp_path / "a.mp3") is None     # 音频本期无 processor
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_media_docs.py -v`
Expected: FAIL(get_processor 对 .docx 返回 None)

- [ ] **Step 4: 实现**

`backend/epictrace/media/pdf.py`:
```python
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from epictrace.interfaces.media import MediaProcessor, MediaResult


class PdfMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def process(self, path: Path) -> MediaResult:
        reader = PdfReader(str(path))
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n\n".join(parts)
        return MediaResult(text=text, metadata={"pages": len(reader.pages)})
```

`backend/epictrace/media/docx.py`:
```python
from __future__ import annotations

from pathlib import Path

import docx

from epictrace.interfaces.media import MediaProcessor, MediaResult


class DocxMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def process(self, path: Path) -> MediaResult:
        document = docx.Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs)
        return MediaResult(text=text, metadata={"paragraphs": len(document.paragraphs)})
```

`backend/epictrace/media/pptx.py`:
```python
from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from epictrace.interfaces.media import MediaProcessor, MediaResult


class PptxMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def process(self, path: Path) -> MediaResult:
        prs = Presentation(str(path))
        lines: list[str] = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        run_text = "".join(run.text for run in para.runs) or para.text
                        if run_text:
                            lines.append(run_text)
        return MediaResult(text="\n".join(lines), metadata={"slides": len(prs.slides)})
```

Modify `backend/epictrace/media/__init__.py` 的注册表:
```python
from epictrace.media.text import TextMediaProcessor
from epictrace.media.pdf import PdfMediaProcessor
from epictrace.media.docx import DocxMediaProcessor
from epictrace.media.pptx import PptxMediaProcessor

_PROCESSORS: list[MediaProcessor] = [
    TextMediaProcessor(),
    PdfMediaProcessor(),
    DocxMediaProcessor(),
    PptxMediaProcessor(),
]
```
(其余 `get_processor` 不变。)

- [ ] **Step 5: 运行确认通过 + 提交**

Run: `cd backend && .venv/bin/pytest tests/test_media_docs.py -v`(PASS)
```bash
git add backend/epictrace/media/ backend/tests/test_media_docs.py backend/pyproject.toml
git commit -m "feat(backend): pdf/docx/pptx MediaProcessor + 注册表(图片/音频无 processor)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: ScanService 改为只登记(提取移到索引时)

**Files:** Modify `backend/epictrace/services/scan.py`; Modify `backend/tests/test_scan_service.py`

- [ ] **Step 1: 改测试**(扫描不再提取文本) — 在 `tests/test_scan_service.py` 中,把断言 `extracted_text` 非空的部分改为断言**登记发生但 `extracted_text == ""`**。具体:`test_scan_registers_indexable_files_in_place` 把 `assert "virtual memory" in r.extracted_text` 改为 `assert r.extracted_text == ""`(扫描只登记,不提取)。其余(就地路径、ingest_method、indexed=False、added 计数、rescan、missing)保持。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_scan_service.py -v`
Expected: FAIL(当前扫描仍提取了文本)

- [ ] **Step 3: 实现** — 在 `backend/epictrace/services/scan.py` 的 `scan_and_register` 里,移除扫描时的文本提取:把
```python
                proc = get_processor(p)
                extracted = proc.process(p).text if proc is not None else ""
```
改为不提取,登记时 `extracted_text=""`(提取统一在索引时做)。即 `IngestRecord(..., extracted_text="")`,并删除对 `get_processor`/`process` 的调用与相关 import(若不再使用)。其余逻辑(忽略规则、diff、missing)不变。

- [ ] **Step 4: 运行确认通过 + 全套 + 提交**

Run: `cd backend && .venv/bin/pytest -q`(全绿)
```bash
git add backend/epictrace/services/scan.py backend/tests/test_scan_service.py
git commit -m "refactor(backend): 扫描只登记,文本提取移到索引时" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: MilvusLiteStore(落地 VectorStore)+ delete_by_record

**Files:** Modify `backend/epictrace/interfaces/vector_store.py`; Modify `backend/epictrace/config.py`; Create `backend/epictrace/vectorstore/__init__.py`(空)、`backend/epictrace/vectorstore/milvus_lite.py`; Test `backend/tests/test_vectorstore_milvus.py`

- [ ] **Step 1: 装依赖**

Run: `cd backend && .venv/bin/pip install pymilvus`(含 milvus-lite)。加入 `pyproject.toml` dependencies。

- [ ] **Step 2: 接口加方法 + config 加路径**

`backend/epictrace/interfaces/vector_store.py` 给 `VectorStore` ABC 增一个抽象方法:
```python
    @abstractmethod
    def delete_by_record(self, ingest_record_id: int) -> None: ...
```
`backend/epictrace/config.py` 的 `AppConfig` 加属性:
```python
    @property
    def milvus_path(self) -> str:
        return str(self.data_dir / "epictrace_vectors.db")
```

- [ ] **Step 3: 写失败测试** `backend/tests/test_vectorstore_milvus.py`

```python
from pathlib import Path

from epictrace.vectorstore.milvus_lite import MilvusLiteStore

DIM = 1024


def _vec(seed: float) -> list[float]:
    return [seed] * DIM


def test_upsert_query_roundtrip(tmp_path: Path):
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    store.upsert([
        {"vector": _vec(0.1), "text": "alpha", "ingest_record_id": 1, "project_id": 7,
         "char_start": 0, "char_end": 5, "source_type": "folder_scan", "embed_model_id": "fake"},
        {"vector": _vec(0.9), "text": "omega", "ingest_record_id": 2, "project_id": 7,
         "char_start": 0, "char_end": 5, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    hits = store.query(_vec(0.1), filter={"project_id": 7}, k=1)
    assert len(hits) == 1
    assert hits[0]["text"] == "alpha"


def test_filter_by_project(tmp_path: Path):
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    store.upsert([
        {"vector": _vec(0.5), "text": "p7", "ingest_record_id": 1, "project_id": 7,
         "char_start": 0, "char_end": 2, "source_type": "folder_scan", "embed_model_id": "fake"},
        {"vector": _vec(0.5), "text": "p8", "ingest_record_id": 2, "project_id": 8,
         "char_start": 0, "char_end": 2, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    hits = store.query(_vec(0.5), filter={"project_id": 8}, k=5)
    assert {h["text"] for h in hits} == {"p8"}


def test_delete_by_record(tmp_path: Path):
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=DIM)
    store.upsert([
        {"vector": _vec(0.3), "text": "keep", "ingest_record_id": 1, "project_id": 7,
         "char_start": 0, "char_end": 4, "source_type": "folder_scan", "embed_model_id": "fake"},
        {"vector": _vec(0.3), "text": "gone", "ingest_record_id": 2, "project_id": 7,
         "char_start": 0, "char_end": 4, "source_type": "folder_scan", "embed_model_id": "fake"},
    ])
    store.delete_by_record(2)
    hits = store.query(_vec(0.3), filter={"project_id": 7}, k=10)
    assert {h["text"] for h in hits} == {"keep"}
```

- [ ] **Step 4: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_vectorstore_milvus.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.vectorstore.milvus_lite`

- [ ] **Step 5: 实现** `backend/epictrace/vectorstore/milvus_lite.py`

```python
from __future__ import annotations

from pymilvus import DataType, MilvusClient

from epictrace.interfaces.vector_store import VectorStore

_COLLECTION = "chunks"
# 本期 schema:仅含 folder_scan 文件用得到的字段。session/timestamp/audio 等留给 Plan 4
# (届时重建 collection + 重索引;向量可重建,代价可接受)。
_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "ingest_record_id": (DataType.INT64, {}),
    "project_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
}


class MilvusLiteStore(VectorStore):
    def __init__(self, db_path: str, dim: int = 1024) -> None:
        self._client = MilvusClient(db_path)
        self._dim = dim
        if not self._client.has_collection(_COLLECTION):
            schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
            for name, (dtype, kw) in _SCALARS.items():
                schema.add_field(name, dtype, **kw)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector", index_type="HNSW", metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self._client.create_collection(
                _COLLECTION, schema=schema, index_params=index_params
            )
            self._client.load_collection(_COLLECTION)

    def upsert(self, records: list[dict]) -> None:
        if not records:
            return
        self._client.insert(_COLLECTION, records)

    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        expr = None
        if filter:
            expr = " and ".join(f"{key} == {val!r}" if isinstance(val, str)
                                else f"{key} == {val}" for key, val in filter.items())
        res = self._client.search(
            _COLLECTION, data=[vector], limit=k, filter=expr or "",
            output_fields=list(_SCALARS.keys()),
        )
        return [hit["entity"] for hit in res[0]]

    def delete_by_record(self, ingest_record_id: int) -> None:
        self._client.delete(_COLLECTION, filter=f"ingest_record_id == {ingest_record_id}")
```

- [ ] **Step 6: 运行确认通过 + 提交**

Run: `cd backend && .venv/bin/pytest tests/test_vectorstore_milvus.py -v`(PASS)
> 注:若 pymilvus 的 search 在删除后未即时反映,实现里 `delete` 后调用 `self._client.flush(_COLLECTION)`;测试据此调整。
```bash
git add backend/epictrace/interfaces/vector_store.py backend/epictrace/config.py backend/epictrace/vectorstore/ backend/tests/test_vectorstore_milvus.py backend/pyproject.toml
git commit -m "feat(backend): MilvusLiteStore(落地 VectorStore: upsert/query/delete_by_record)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: IndexService + 后台 job(用 FakeEmbedder + 真 Milvus Lite 测)

**Files:** Create `backend/tests/fakes.py`、`backend/epictrace/services/index.py`; Test `backend/tests/test_index_service.py`

- [ ] **Step 1: FakeEmbedder** `backend/tests/fakes.py`

```python
from epictrace.interfaces.embedding import EmbeddingProvider


class FakeEmbedder(EmbeddingProvider):
    """确定性 1024 维向量,遵守 EmbeddingProvider 契约;不依赖 torch。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = (sum(ord(c) for c in t) % 97) / 97.0
            out.append([h] * self._dim)
        return out

    @property
    def model_id(self) -> str:
        return "fake"
```

- [ ] **Step 2: 写失败测试** `backend/tests/test_index_service.py`

```python
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService
from epictrace.services.index import IndexService
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from tests.fakes import FakeEmbedder


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    folder = Path(proj.folder_path)
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    svc = IndexService(db, embedder=FakeEmbedder(), vector_store=store)
    return db, proj, folder, store, svc


def test_index_extracts_chunks_embeds_and_flips_indexed(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "note.md").write_text("虚拟内存\n\n" + "page table " * 300, encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)

    job = svc.index_project(proj.id)
    assert job.total == 1 and job.done == 1 and job.status == "done"

    # 文件翻成已索引
    from epictrace.services.ingest import IngestService
    recs = IngestService(db).list_for_project(proj.id)
    assert all(r.indexed for r in recs)

    # 向量进了库
    hits = store.query(FakeEmbedder().embed(["page table"])[0], filter={"project_id": proj.id}, k=3)
    assert len(hits) >= 1


def test_index_skips_image_and_audio(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "pic.png").write_bytes(b"\x89PNG\r\n")
    (folder / "snd.mp3").write_bytes(b"ID3")
    ScanService(db).scan_and_register(proj.id)  # 注:.png/.mp3 不在 INDEXABLE_SUFFIXES,扫描就不会登记
    job = svc.index_project(proj.id)
    assert job.total == 0  # 没有可索引文件


def test_index_single_file_failure_is_recorded_not_fatal(tmp_path, monkeypatch):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "a.md").write_text("a", encoding="utf-8")
    (folder / "b.md").write_text("b", encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)
    # 让某个文件提取时抛错
    import epictrace.services.index as idx
    real = idx.get_processor
    def boom(p):
        if p.name == "a.md":
            class P:
                def process(self, _): raise RuntimeError("boom")
                def supports(self, _): return True
            return P()
        return real(p)
    monkeypatch.setattr(idx, "get_processor", boom)
    job = svc.index_project(proj.id)
    assert job.done == 1 and len(job.errors) == 1     # b 成功, a 记错
    assert job.status == "done"


def test_status_for_unknown_project_total_zero(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    job = svc.index_project(99999)
    assert job.total == 0
```

> 注意:`.png/.mp3` 是否在扫描白名单决定 `test_index_skips_image_and_audio` 的写法。当前 `INDEXABLE_SUFFIXES` **不含**图片/音频,所以它们扫描即被忽略、`total==0`。若将来把图片纳入白名单(登记但不索引),再调此测试 + IndexService 的"有 processor 才索引"分支。本期按"无 processor → 不索引"即可。

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_index_service.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.services.index`

- [ ] **Step 4: 实现** `backend/epictrace/services/index.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.indexing.chunker import chunk_text
from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.media import get_processor
from epictrace.models import IngestRecord


@dataclass
class IndexJob:
    project_id: int
    total: int = 0
    done: int = 0
    status: str = "running"          # running | done | error
    errors: list[str] = field(default_factory=list)


class IndexService:
    def __init__(self, db: Database, embedder: EmbeddingProvider, vector_store: VectorStore) -> None:
        self._db = db
        self._embedder = embedder
        self._store = vector_store

    def index_project(self, project_id: int) -> IndexJob:
        # 取该项目待索引、且有可用 processor 的文件
        with self._db.session() as s:
            recs = list(
                s.execute(
                    select(IngestRecord).where(
                        IngestRecord.project_id == project_id,
                        IngestRecord.indexed.is_(False),
                    )
                ).scalars()
            )
            targets = [(r.id, r.stored_path) for r in recs if get_processor(Path(r.stored_path)) is not None]

        job = IndexJob(project_id=project_id, total=len(targets))
        for rec_id, path_str in targets:
            try:
                path = Path(path_str)
                proc = get_processor(path)
                text = proc.process(path).text
                chunks = chunk_text(text)
                if chunks:
                    vectors = self._embedder.embed([c.text for c in chunks])
                    self._store.delete_by_record(rec_id)  # 幂等:重索引先清旧块
                    self._store.upsert([
                        {
                            "vector": vec, "text": c.text,
                            "ingest_record_id": rec_id, "project_id": project_id,
                            "char_start": c.char_start, "char_end": c.char_end,
                            "source_type": "folder_scan",
                            "embed_model_id": self._embedder.model_id,
                        }
                        for c, vec in zip(chunks, vectors)
                    ])
                # 标记已索引
                with self._db.session() as s:
                    r = s.get(IngestRecord, rec_id)
                    if r is not None:
                        r.indexed = True
                job.done += 1
            except Exception as e:  # 单文件失败:记录并继续
                job.errors.append(f"{path_str}: {e}")
        job.status = "done"
        return job
```

- [ ] **Step 5: 运行确认通过 + 提交**

Run: `cd backend && .venv/bin/pytest tests/test_index_service.py -v`(PASS)
```bash
git add backend/tests/fakes.py backend/epictrace/services/index.py backend/tests/test_index_service.py
git commit -m "feat(backend): IndexService(提取→切分→嵌入→入库→翻 indexed;单文件容错;幂等)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: BgeM3Embedder(进程内,真模型)+ slow 冒烟

**Files:** Create `backend/epictrace/embedding/__init__.py`(空)、`backend/epictrace/embedding/bge_m3.py`; Test `backend/tests/test_bge_m3_smoke.py`

- [ ] **Step 1: 装依赖(重,含 torch)**

Run: `cd backend && .venv/bin/pip install FlagEmbedding`(会拉 torch,数 GB,较慢)。加入 `pyproject.toml` dependencies。

- [ ] **Step 2: 实现** `backend/epictrace/embedding/bge_m3.py`

```python
from __future__ import annotations

import threading


class BgeM3Embedder:
    """进程内 BGE-M3(落地 EmbeddingProvider 契约)。懒加载:首次 embed 时下载/加载模型。"""

    _MODEL_ID = "bge-m3"
    _DIM = 1024

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from FlagEmbedding import BGEM3FlagModel
                    self._model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure()
        out = model.encode(texts, return_dense=True, return_sparse=False, return_colbert_vecs=False)
        dense = out["dense_vecs"]
        return [list(map(float, v)) for v in dense]

    @property
    def model_id(self) -> str:
        return self._MODEL_ID
```

- [ ] **Step 3: slow 冒烟测试** `backend/tests/test_bge_m3_smoke.py`

```python
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EPICTRACE_RUN_SLOW") != "1",
    reason="真 BGE-M3 冒烟:需下载模型,设 EPICTRACE_RUN_SLOW=1 才跑",
)


def test_real_bge_m3_embed_store_query_roundtrip(tmp_path):
    """真模型走全链 + 断言维度 == collection 维度(兜契约/维度/归一化漂移)。"""
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    emb = BgeM3Embedder()
    vecs = emb.embed(["虚拟内存如何工作", "完全无关的内容:量子色动力学"])
    assert len(vecs[0]) == 1024                      # 真实维度 == collection 的 1024

    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    store.upsert([
        {"vector": vecs[0], "text": "虚拟内存", "ingest_record_id": 1, "project_id": 1,
         "char_start": 0, "char_end": 4, "source_type": "folder_scan", "embed_model_id": emb.model_id},
        {"vector": vecs[1], "text": "量子色动力学", "ingest_record_id": 2, "project_id": 1,
         "char_start": 0, "char_end": 6, "source_type": "folder_scan", "embed_model_id": emb.model_id},
    ])
    q = emb.embed(["虚拟内存"])[0]
    hits = store.query(q, filter={"project_id": 1}, k=1)
    assert hits[0]["text"] == "虚拟内存"             # 最近的应是语义相近的那条
```

- [ ] **Step 4: 运行(默认跳过)+ 提交**

Run: `cd backend && .venv/bin/pytest tests/test_bge_m3_smoke.py -v`(应 SKIPPED)。可选真跑:`EPICTRACE_RUN_SLOW=1 .venv/bin/pytest tests/test_bge_m3_smoke.py -v`(首次下模型,慢)。
```bash
git add backend/epictrace/embedding/ backend/tests/test_bge_m3_smoke.py backend/pyproject.toml
git commit -m "feat(backend): 进程内 BgeM3Embedder + slow 冒烟(真模型全链+维度断言)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: API(index + status)+ app 工厂接入 embedder/store

**Files:** Modify `backend/epictrace/schemas.py`、`backend/epictrace/api/app.py`、`backend/epictrace/api/routers/projects.py`; Test `backend/tests/test_api_index.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_api_index.py`

`tests/conftest.py` 增一个用 FakeEmbedder + 临时 Milvus 的 client fixture(沿用现有 `client` 的写法,但注入假件):
```python
@pytest.fixture()
def index_client(tmp_path):
    from fastapi.testclient import TestClient
    from epictrace.api.app import create_app
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore
    from tests.fakes import FakeEmbedder
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    app = create_app(db=db, embedder=FakeEmbedder(), vector_store=store)
    return TestClient(app)
```

`tests/test_api_index.py`:
```python
from pathlib import Path


def test_index_endpoint_indexes_pending(index_client, tmp_path):
    folder = tmp_path / "P"
    pid = index_client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("page table " * 200, encoding="utf-8")
    index_client.post(f"/api/projects/{pid}/scan")

    resp = index_client.post(f"/api/projects/{pid}/index")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and body["done"] == 1 and body["status"] == "done"

    files = index_client.get(f"/api/files?project_id={pid}").json()
    assert all(f["indexed"] for f in files)


def test_index_status_unknown_project_404(index_client):
    assert index_client.post("/api/projects/99999/index").status_code == 404
```

> 本期为简单起见,`POST /index` **同步执行**(测试拿到 `done` 直接断言)。后台异步 + 进度轮询的接口形态保留在 §设计,但实现上若同步足够快(假件下)就同步返回最终 job;真模型下前端仍轮询 status(见下)。**实现者:同步执行 IndexService.index_project 并返回 IndexJob;`GET /index/status` 返回最近一次 job(存在 app.state 的简单字典里)。** 若真模型下同步阻塞过久影响体验,改 BackgroundTasks——但本期 API 合约(返回 job 状态 / status 查询)不变。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_api_index.py -v`
Expected: FAIL(`create_app` 不接受 embedder/vector_store;无 /index 路由)

- [ ] **Step 3: 实现**

`backend/epictrace/schemas.py` 加:
```python
class IndexStatusOut(BaseModel):
    project_id: int
    total: int
    done: int
    status: str
    errors: list[str] = []
```

`backend/epictrace/api/app.py` 的 `create_app` 增可注入的 embedder/vector_store(默认构造真件),挂到 `app.state`:
```python
def create_app(db=None, embedder=None, vector_store=None):
    ...
    if db is None:
        db = Database(AppConfig()); db.create_all()
    app.state.db = db
    if embedder is None:
        from epictrace.embedding.bge_m3 import BgeM3Embedder
        embedder = BgeM3Embedder()
    if vector_store is None:
        from epictrace.vectorstore.milvus_lite import MilvusLiteStore
        vector_store = MilvusLiteStore(db_path=AppConfig().milvus_path, dim=1024)
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.index_jobs = {}   # project_id -> IndexJob(最近一次)
    ...
```
(保持 health/projects/files 路由 + CORS + 静态挂载不变;新增 import 时小心循环依赖,真件用函数内延迟 import。)

`backend/epictrace/api/routers/projects.py` 加两个端点:
```python
from epictrace.schemas import IndexStatusOut
from epictrace.services.index import IndexService


def _job_to_out(job) -> IndexStatusOut:
    return IndexStatusOut(project_id=job.project_id, total=job.total, done=job.done,
                          status=job.status, errors=job.errors)


@router.post("/{project_id}/index", response_model=IndexStatusOut)
def index_project(project_id: int, request: Request, db: Database = Depends(get_db)) -> IndexStatusOut:
    from epictrace.models import Project
    with db.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    svc = IndexService(db, request.app.state.embedder, request.app.state.vector_store)
    job = svc.index_project(project_id)
    request.app.state.index_jobs[project_id] = job
    return _job_to_out(job)


@router.get("/{project_id}/index/status", response_model=IndexStatusOut)
def index_status(project_id: int, request: Request) -> IndexStatusOut:
    job = request.app.state.index_jobs.get(project_id)
    if job is None:
        return IndexStatusOut(project_id=project_id, total=0, done=0, status="idle")
    return _job_to_out(job)
```
(`Request` 从 fastapi import。)

- [ ] **Step 4: 运行确认通过 + 全套 + 提交**

Run: `cd backend && .venv/bin/pytest -q`(全绿;真 BGE 冒烟 SKIPPED)
```bash
git add backend/epictrace/schemas.py backend/epictrace/api/ backend/tests/conftest.py backend/tests/test_api_index.py
git commit -m "feat(backend): /projects/{id}/index + /index/status;app 工厂接入可注入 embedder/vector_store" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 前端接通「建立索引」+ 进度(用 frontend-design / impeccable)

**Files:** Modify `frontend/src/lib/api.ts`、`frontend/src/components/PendingList.tsx`、`frontend/src/views/ProcessIngestView.tsx`、`frontend/src/components/FileList.tsx`

**逻辑契约(固定);视觉用 `impeccable` 打磨,保持现有浅色风。**

- `lib/api.ts`:加
  ```ts
  export interface IndexStatus { project_id: number; total: number; done: number; status: string; errors: string[]; }
  // api 对象内:
  indexProject: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/index`, { method: "POST" }).then(j<IndexStatus>),
  indexStatus: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/index/status`).then(j<IndexStatus>),
  ```
- `PendingList.tsx`:每个**项目分组**的行,加一个**「建立索引」按钮**(替换原全局禁用占位)。点击 → `api.indexProject(id)`;期间该组显示进度(`done/total` + 转圈);完成后调用父级回调刷新(文件从待索引消失/翻已索引)。索引中禁用该组按钮。错误用小字提示(`errors.length`)。
- `ProcessIngestView.tsx`:给 `PendingList` 传一个 `onIndexed` 回调 → bump `refreshKey`(重聚合);与现有"扫描中"状态并存。
- `FileList.tsx`:无需逻辑改动(已显示 已索引/待索引 徽章);确认索引后 `refreshKey` 触发会反映翻转。
- **图片/音频**:这些文件本就不在扫描白名单(不会出现在待索引)。若将来纳入,显示「需多媒体处理」徽章——本期无需处理。

- [ ] **Step 1**(impeccable)实现上述:`api.ts` 加方法、`PendingList` 每组「建立索引」+ 进度、`ProcessIngestView` 接 `onIndexed` 刷新。
- [ ] **Step 2** `cd frontend && npm run build` 成功。
- [ ] **Step 3** 提交:`git commit -m "feat(frontend): 接通按项目建立索引 + 进度,完成后刷新待索引/文件状态"`(+trailer)。

**验收:** 建项目→扫描→「建立索引」→ 进度跑完 → 文件从「待索引」翻「已索引」;真模型下首次会下模型(慢)。

---

## Verification(端到端)

1. **后端全绿(假件):** `cd backend && .venv/bin/pytest -q`(BGE 冒烟 SKIPPED)。
2. **真模型冒烟(可选):** `cd backend && EPICTRACE_RUN_SLOW=1 .venv/bin/pytest tests/test_bge_m3_smoke.py -v`(首次下模型)。
3. **前端构建:** `cd frontend && npm run build`。
4. **打包态手测:** 启动 app → 建项目(放几个 .md/.pdf/.docx)→ 扫描 → 「建立索引」→ 进度 → 文件翻「已索引」。首次索引会下 BGE-M3。
5. **代号:** 全仓库(代码/前端/docs)不出现任何前身原型代号(用 `grep -rni` 查对应代号应为空)。

---

## Self-Review(覆盖 spec)

- ✅ MediaProcessor 扩 pdf/docx/pptx(T2)· 扫描只登记/提取移到索引时(T3)。
- ✅ 自写字符偏移切分器(T1)。
- ✅ 进程内 BGE-M3(T6)+ 假件单测 + 真模型冒烟(T5/T6)。
- ✅ MilvusLiteStore 落地 VectorStore + delete_by_record 幂等(T4)。
- ✅ IndexService 编排 + 单文件容错 + 跳过无 processor 类型(T5)。
- ✅ API index + status,app 工厂可注入(T7);前端按项目建立索引 + 进度(T8)。
- ⛔ 本期不做(spec §6):图片(OCR/云 caption + 设置面板)、音频(faster-whisper)、混合检索/Rerank/RAG(Plan 3)、session/timestamp/audio schema 字段(Plan 4 重建 collection 时加)。
- 注:`POST /index` 本期**同步**返回最终 job(假件快);真模型下若阻塞影响体验再改 BackgroundTasks,API 合约不变。

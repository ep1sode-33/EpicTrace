# RAG Eval — Plan 1: Deterministic Retrieval-Eval Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LLM-free inner loop of the RAG evaluation harness — a frozen stratified corpus, a golden set, deterministic retrieval metrics, a `retrieve` runner over the real `HybridRetriever`, and per-slice report + run-vs-run diff — so retrieval params can be swept and compared with zero token cost.

**Architecture:** A manual-run package `backend/scripts/rag_eval/` (mirrors `scripts/asr_eval.py`: not in CI, lazy-imports heavy deps). Pure-function metrics operate on `RetrievedChunk`-shaped objects (duck-typed) against gold **document char ranges**; a runner calls the production `HybridRetriever` at measurement point ① (retriever isolation); a report aggregates by slice. Heavy components (embedder/store/reranker) are injected so the runner is smoke-testable with fakes.

**Tech Stack:** Python 3.11, stdlib (`dataclasses`, `json`, `hashlib`, `argparse`, `math`, `shutil`), pytest. Reuses `epictrace.retrieval.pipeline.HybridRetriever` and `epictrace.retrieval.types.RetrievedChunk`. No new third-party deps.

## Global Constraints

- Python **3.11**; backend venv at `backend/.venv`; run tests with `./.venv/bin/pytest` from `backend/`.
- **Tests must never spawn real models / real ASR workers / network**; heavy deps (FlagEmbedding, Milvus, LLM) are **lazy-imported** inside functions, never at module top level. Smoke tests inject fakes.
- Package lives at `backend/scripts/rag_eval/` (manual-run, **not** collected by CI/pytest by default — no `test_` prefix in that dir).
- Docstrings/comments in **简体中文**; code identifiers/paths/commands in English.
- Source data dirs (e.g. `CS 2505`, `TX AI培训`) are **read-only** — never write into them; only copy out into `backend/eval-data/` (gitignored).
- Checked into git: `backend/tests/fixtures/rag_eval/golden.jsonl`. Gitignored: `backend/eval-data/`, `backend/scripts/rag_eval/runs/`, `backend/scripts/rag_eval/corpus_spec.json`.
- Gold spans are **document char ranges**; a retrieved chunk **hits** gold span `g` iff `chunk.ingest_record_id == g.ingest_record_id` AND char ranges overlap.
- git author is `ep1sode-33`; every commit ends with trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Citation metrics, LLM judge, and golden **synthesis** are out of scope here — they require a generated answer / LLM and belong to **Plan 2**. Plan 1 bootstraps a small hand-authored golden set.

---

### Task 1: Golden data model + JSONL load/save

**Files:**
- Create: `backend/scripts/rag_eval/__init__.py` (empty)
- Create: `backend/scripts/rag_eval/golden.py`
- Test: `backend/tests/test_rag_eval_golden.py`

**Interfaces:**
- Produces: `GoldSpan(ingest_record_id:int, doc_char_start:int, doc_char_end:int)` (frozen); `GoldItem(id:str, question:str, gold_spans:tuple[GoldSpan,...], reference_answer:str, slices:dict, provenance:str, source:str, corpus_version:str)` (frozen); `load_golden(path)->list[GoldItem]`; `save_golden(items, path)->None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_golden.py
from scripts.rag_eval.golden import GoldItem, GoldSpan, load_golden, save_golden


def test_round_trip(tmp_path):
    items = [
        GoldItem(
            id="g0001", question="什么是缓存命中率?",
            gold_spans=(GoldSpan(12, 100, 240),),
            reference_answer="命中数除以总访问数。",
            slices={"domain": "study-cs", "doc_type": "pdf", "lang": "zh", "q_type": "single_hop"},
            provenance="hand", source="own", corpus_version="v1",
        ),
        GoldItem(
            id="g0002", question="multi-hop example",
            gold_spans=(GoldSpan(3, 0, 50), GoldSpan(7, 80, 130)),
            reference_answer="...", slices={"q_type": "multi_hop"},
            provenance="hand", source="own", corpus_version="v1",
        ),
    ]
    p = tmp_path / "golden.jsonl"
    save_golden(items, p)
    loaded = load_golden(p)
    assert loaded == items
    assert loaded[1].gold_spans[1].doc_char_start == 80


def test_load_skips_blank_lines(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('\n', encoding="utf-8")
    assert load_golden(p) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_golden.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.golden`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/golden.py
"""Golden 测试集数据模型 + JSONL 读写。gold 跨度记成源文档(抽取文本)的 char 区间,不绑 chunk。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldSpan:
    ingest_record_id: int
    doc_char_start: int
    doc_char_end: int


@dataclass(frozen=True)
class GoldItem:
    id: str
    question: str
    gold_spans: tuple[GoldSpan, ...]
    reference_answer: str
    slices: dict
    provenance: str        # hand | synthetic
    source: str            # own | benchmark:<name> | synthetic-doc
    corpus_version: str


def _item_to_dict(it: GoldItem) -> dict:
    d = asdict(it)
    d["gold_spans"] = [asdict(s) for s in it.gold_spans]
    return d


def _item_from_dict(d: dict) -> GoldItem:
    spans = tuple(GoldSpan(**s) for s in d["gold_spans"])
    return GoldItem(
        id=d["id"], question=d["question"], gold_spans=spans,
        reference_answer=d.get("reference_answer", ""), slices=d.get("slices", {}),
        provenance=d.get("provenance", "hand"), source=d.get("source", "own"),
        corpus_version=d.get("corpus_version", "v1"),
    )


def save_golden(items: list[GoldItem], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(_item_to_dict(it), ensure_ascii=False) + "\n")


def load_golden(path: str | Path) -> list[GoldItem]:
    out: list[GoldItem] = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(_item_from_dict(json.loads(line)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_golden.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/__init__.py backend/scripts/rag_eval/golden.py backend/tests/test_rag_eval_golden.py
git commit -m "feat(rag-eval): golden data model + JSONL load/save

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Retrieval metrics — hit/overlap, recall@k, MRR

**Files:**
- Create: `backend/scripts/rag_eval/metrics.py`
- Test: `backend/tests/test_rag_eval_metrics.py`

**Interfaces:**
- Consumes: `GoldSpan` (Task 1); `ranked` = list of objects with `.ingest_record_id:int`, `.char_start:int`, `.char_end:int` (duck-typed; production `RetrievedChunk` satisfies it).
- Produces: `overlaps(a0,a1,b0,b1)->bool`; `chunk_hits(chunk, gold_spans)->bool`; `recall_any_at_k(ranked, gold_spans, k)->float`; `recall_coverage_at_k(ranked, gold_spans, k)->float`; `mrr(ranked, gold_spans)->float`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_metrics.py
from collections import namedtuple

from scripts.rag_eval.golden import GoldSpan
from scripts.rag_eval.metrics import (
    chunk_hits, mrr, overlaps, recall_any_at_k, recall_coverage_at_k,
)

C = namedtuple("C", "ingest_record_id char_start char_end")


def test_overlaps_half_open():
    assert overlaps(10, 20, 15, 25) is True
    assert overlaps(10, 20, 20, 30) is False   # 邻接不算重叠(半开区间)
    assert overlaps(10, 20, 5, 11) is True


def test_chunk_hits_requires_same_record():
    g = (GoldSpan(1, 100, 200),)
    assert chunk_hits(C(1, 150, 250), g) is True
    assert chunk_hits(C(2, 150, 250), g) is False   # 文档不同 → 不命中


def test_recall_any_and_coverage():
    gold = (GoldSpan(1, 100, 200), GoldSpan(2, 0, 50))
    ranked = [C(9, 0, 10), C(1, 180, 260), C(5, 0, 10)]   # 命中第 1 条 gold,未命中第 2 条
    assert recall_any_at_k(ranked, gold, k=3) == 1.0
    assert recall_any_at_k(ranked, gold, k=1) == 0.0       # top-1 不含命中
    assert recall_coverage_at_k(ranked, gold, k=3) == 0.5  # 2 条 gold 命中 1 条


def test_mrr():
    gold = (GoldSpan(1, 100, 200),)
    assert mrr([C(9, 0, 1), C(1, 150, 160)], gold) == 0.5   # 第 2 名首次命中
    assert mrr([C(9, 0, 1)], gold) == 0.0                   # 无命中
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.metrics`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/metrics.py
"""检索指标(确定性、免 LLM)。chunk「命中」gold = 同 ingest_record_id 且 char 区间重叠(半开)。"""
from __future__ import annotations


def overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    """半开区间 [a0,a1) 与 [b0,b1) 是否重叠。邻接(a1==b0)不算。"""
    return a0 < b1 and b0 < a1


def chunk_hits(chunk, gold_spans) -> bool:
    return any(
        chunk.ingest_record_id == g.ingest_record_id
        and overlaps(chunk.char_start, chunk.char_end, g.doc_char_start, g.doc_char_end)
        for g in gold_spans
    )


def recall_any_at_k(ranked, gold_spans, k: int) -> float:
    """top-k 内有任一命中 = 1.0 否则 0.0。"""
    return 1.0 if any(chunk_hits(c, gold_spans) for c in ranked[:k]) else 0.0


def recall_coverage_at_k(ranked, gold_spans, k: int) -> float:
    """多跳:top-k 命中的 gold 跨度数 / 总 gold 跨度数。"""
    if not gold_spans:
        return 0.0
    top = ranked[:k]
    covered = sum(
        1 for g in gold_spans
        if any(c.ingest_record_id == g.ingest_record_id
               and overlaps(c.char_start, c.char_end, g.doc_char_start, g.doc_char_end)
               for c in top)
    )
    return covered / len(gold_spans)


def mrr(ranked, gold_spans) -> float:
    """第一个命中 chunk 名次的倒数(rank 从 1 起);无命中 = 0.0。"""
    for i, c in enumerate(ranked, start=1):
        if chunk_hits(c, gold_spans):
            return 1.0 / i
    return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/metrics.py backend/tests/test_rag_eval_metrics.py
git commit -m "feat(rag-eval): retrieval metrics — hit/overlap, recall@k, MRR

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Retrieval metrics — nDCG@k + context precision (plain + ordered)

**Files:**
- Modify: `backend/scripts/rag_eval/metrics.py`
- Test: `backend/tests/test_rag_eval_metrics.py` (append)

**Interfaces:**
- Consumes: `chunk_hits` (Task 2).
- Produces: `ndcg_at_k(ranked, gold_spans, k)->float`; `context_precision_at_k(ranked, gold_spans, k)->float`; `context_precision_ordered_at_k(ranked, gold_spans, k)->float`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_rag_eval_metrics.py
import math

from scripts.rag_eval.metrics import (
    context_precision_at_k, context_precision_ordered_at_k, ndcg_at_k,
)


def test_ndcg_perfect_and_imperfect():
    gold = (GoldSpan(1, 0, 10),)
    # 命中在第 1 名 → nDCG=1.0
    assert ndcg_at_k([C(1, 0, 5), C(9, 0, 1)], gold, k=2) == 1.0
    # 命中在第 2 名 → DCG=1/log2(3), IDCG=1/log2(2)=1 → nDCG=1/log2(3)
    got = ndcg_at_k([C(9, 0, 1), C(1, 0, 5)], gold, k=2)
    assert math.isclose(got, 1.0 / math.log2(3), rel_tol=1e-9)
    assert ndcg_at_k([C(9, 0, 1)], gold, k=2) == 0.0   # 无命中


def test_context_precision_plain_and_ordered():
    gold = (GoldSpan(1, 0, 10), GoldSpan(2, 0, 10))
    ranked = [C(1, 0, 5), C(9, 0, 1), C(2, 0, 5)]      # 命中在第 1、3 名
    # plain: 命中 2 / 取前 3 = 2/3
    assert math.isclose(context_precision_at_k(ranked, gold, k=3), 2 / 3, rel_tol=1e-9)
    # ordered: (precision@1 * 1 + precision@3 * 1) / 命中数 = (1/1 + 2/3) / 2
    assert math.isclose(
        context_precision_ordered_at_k(ranked, gold, k=3), (1.0 + 2 / 3) / 2, rel_tol=1e-9)
    assert context_precision_ordered_at_k([C(9, 0, 1)], gold, k=3) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics.py -q`
Expected: FAIL (`ImportError: cannot import name 'ndcg_at_k'`)

- [ ] **Step 3: Write minimal implementation**

```python
# append to backend/scripts/rag_eval/metrics.py
import math


def ndcg_at_k(ranked, gold_spans, k: int) -> float:
    """二值相关性的 nDCG@k:rel_i = 命中=1 否则 0;IDCG = 把命中全排前面的理想排序。"""
    rels = [1.0 if chunk_hits(c, gold_spans) else 0.0 for c in ranked[:k]]
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(rels))
    ideal = sorted(rels, reverse=True)
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def context_precision_at_k(ranked, gold_spans, k: int) -> float:
    """信噪比:top-k 内命中数 / 实际考察数(min(k, 返回数))。"""
    top = ranked[:k]
    if not top:
        return 0.0
    hits = sum(1 for c in top if chunk_hits(c, gold_spans))
    return hits / len(top)


def context_precision_ordered_at_k(ranked, gold_spans, k: int) -> float:
    """RAGAS 式有序版:命中越靠前得分越高。Σ(precision@i · 命中_i) / 命中总数。"""
    top = ranked[:k]
    hits_so_far = 0
    acc = 0.0
    for i, c in enumerate(top, start=1):
        if chunk_hits(c, gold_spans):
            hits_so_far += 1
            acc += hits_so_far / i
    return acc / hits_so_far if hits_so_far else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/metrics.py backend/tests/test_rag_eval_metrics.py
git commit -m "feat(rag-eval): nDCG@k + context precision (plain + ordered)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: EvalConfig + stable hash

**Files:**
- Create: `backend/scripts/rag_eval/config.py`
- Test: `backend/tests/test_rag_eval_config.py`

**Interfaces:**
- Produces: `EvalConfig` (frozen dataclass) with fields `k:int=6, dense_n:int=30, fuse_m:int=20, rrf_k0:int=60, sparse_enabled:bool=True, chunker_target:int=1800, chunker_overlap:int=200, k_values:tuple[int,...]=(1,3,5,6), label:str=""`; `EvalConfig.config_hash()->str` (12-hex, stable, **excludes** `label`); `EvalConfig.chunker_hash()->str` (depends only on chunker_target/overlap).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_config.py
from scripts.rag_eval.config import EvalConfig


def test_hash_stable_and_excludes_label():
    a = EvalConfig(k=6, label="baseline")
    b = EvalConfig(k=6, label="run-2")
    assert a.config_hash() == b.config_hash()        # label 不影响 hash
    assert len(a.config_hash()) == 12


def test_hash_changes_with_knob():
    assert EvalConfig(k=6).config_hash() != EvalConfig(k=10).config_hash()


def test_chunker_hash_only_depends_on_chunker():
    assert EvalConfig(k=6).chunker_hash() == EvalConfig(k=10).chunker_hash()
    assert EvalConfig(chunker_target=900).chunker_hash() != EvalConfig().chunker_hash()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_config.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.config`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/config.py
"""一次 run 的所有旋钮。config_hash 用于归档 run;chunker_hash 用于索引快照(改切块才重建索引)。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class EvalConfig:
    k: int = 6                      # rerank 后最终 top_k
    dense_n: int = 30               # dense/sparse 各自召回数
    fuse_m: int = 20                # RRF 融合后保留数
    rrf_k0: int = 60                # RRF 常数(记录用;HybridRetriever 当前内部固定 60)
    sparse_enabled: bool = True
    chunker_target: int = 1800
    chunker_overlap: int = 200
    k_values: tuple[int, ...] = (1, 3, 5, 6)   # @k 指标要算哪些 k
    label: str = ""                 # 人读标签,不进 hash

    def _hash(self, payload: dict) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def config_hash(self) -> str:
        d = asdict(self)
        d.pop("label", None)
        d["k_values"] = list(self.k_values)
        return self._hash(d)

    def chunker_hash(self) -> str:
        return self._hash({"t": self.chunker_target, "o": self.chunker_overlap})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_config.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/config.py backend/tests/test_rag_eval_config.py
git commit -m "feat(rag-eval): EvalConfig with stable config/chunker hashes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Corpus build — copy-out + sha256 manifest (originals read-only)

**Files:**
- Create: `backend/scripts/rag_eval/corpus.py`
- Test: `backend/tests/test_rag_eval_corpus.py`

**Interfaces:**
- Produces: `CorpusEntry(src:Path, slices:dict, source:str="own")`; `ManifestRow(rel_path:str, sha256:str, bytes:int, slices:dict, source:str)`; `build_corpus(entries:list[CorpusEntry], dest:Path, corpus_version:str)->list[ManifestRow]` (copies each `src` to `dest/<sha-prefixed flattened name>`, computes sha256, writes `dest/manifest.jsonl`, **never writes to any src**); `load_manifest(path)->list[ManifestRow]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_corpus.py
import hashlib

from scripts.rag_eval.corpus import CorpusEntry, build_corpus, load_manifest


def test_build_copies_out_and_hashes_without_touching_src(tmp_path):
    src_dir = tmp_path / "orig"
    src_dir.mkdir()
    f = src_dir / "lecture.txt"
    f.write_text("缓存命中率 = 命中 / 总访问", encoding="utf-8")
    src_mtime = f.stat().st_mtime

    dest = tmp_path / "eval-data"
    rows = build_corpus(
        [CorpusEntry(src=f, slices={"domain": "study-cs", "doc_type": "txt", "lang": "zh"})],
        dest=dest, corpus_version="v1",
    )
    # 原件未被改动(内容 + mtime)。
    assert f.read_text(encoding="utf-8") == "缓存命中率 = 命中 / 总访问"
    assert f.stat().st_mtime == src_mtime
    # 拷贝 + sha256 正确。
    assert len(rows) == 1
    want = hashlib.sha256(f.read_bytes()).hexdigest()
    assert rows[0].sha256 == want
    assert (dest / rows[0].rel_path).read_bytes() == f.read_bytes()
    # manifest 可回读且等价。
    assert load_manifest(dest / "manifest.jsonl") == rows


def test_manifest_rel_paths_unique_for_same_basename(tmp_path):
    a = tmp_path / "a" / "notes.md"; a.parent.mkdir(parents=True); a.write_text("AAA")
    b = tmp_path / "b" / "notes.md"; b.parent.mkdir(parents=True); b.write_text("BBB")
    rows = build_corpus(
        [CorpusEntry(src=a, slices={}), CorpusEntry(src=b, slices={})],
        dest=tmp_path / "out", corpus_version="v1",
    )
    assert rows[0].rel_path != rows[1].rel_path   # 同名不互相覆盖
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_corpus.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.corpus`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/corpus.py
"""把分层切片从只读源拷到冻结目录 eval-data/ + 算 sha256 manifest。绝不写源目录。"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CorpusEntry:
    src: Path
    slices: dict
    source: str = "own"


@dataclass(frozen=True)
class ManifestRow:
    rel_path: str
    sha256: str
    bytes: int
    slices: dict
    source: str


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def build_corpus(entries: list[CorpusEntry], dest: Path, corpus_version: str) -> list[ManifestRow]:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rows: list[ManifestRow] = []
    for e in entries:
        src = Path(e.src)
        digest = _sha256(src)
        # sha 前缀防同名覆盖;保留原扩展名供 MediaProcessor 识别类型。
        rel = f"{digest[:8]}-{src.name}"
        shutil.copy2(src, dest / rel)   # copy2 只读源、写新文件
        rows.append(ManifestRow(rel_path=rel, sha256=digest,
                                bytes=src.stat().st_size, slices=e.slices, source=e.source))
    manifest = dest / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"corpus_version": corpus_version}, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return rows


def load_manifest(path: str | Path) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    with Path(path).open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or i == 0:        # 第 0 行是 {corpus_version}
                continue
            rows.append(ManifestRow(**json.loads(line)))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_corpus.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/corpus.py backend/tests/test_rag_eval_corpus.py
git commit -m "feat(rag-eval): corpus copy-out + sha256 manifest (read-only sources)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Hand-seed golden fixture (checked-in bootstrap set)

**Files:**
- Create: `backend/tests/fixtures/rag_eval/golden.jsonl`
- Test: `backend/tests/test_rag_eval_golden_fixture.py`

**Interfaces:**
- Consumes: `load_golden` (Task 1).
- Produces: a checked-in `golden.jsonl` with ≥6 hand items spanning slices (single_hop / multi_hop / negation; zh + en) — the bootstrap set Plan 2's synthesis will grow. `ingest_record_id`/offsets here are placeholders to be re-pointed once the real eval index exists (documented in the file's first comment line is not allowed by jsonl — track in the plan, not the file).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_golden_fixture.py
from pathlib import Path

from scripts.rag_eval.golden import load_golden

FIX = Path(__file__).parent / "fixtures" / "rag_eval" / "golden.jsonl"


def test_fixture_loads_and_has_slice_coverage():
    items = load_golden(FIX)
    assert len(items) >= 6
    qtypes = {it.slices.get("q_type") for it in items}
    assert {"single_hop", "multi_hop", "negation"} <= qtypes
    langs = {it.slices.get("lang") for it in items}
    assert {"zh", "en"} <= langs
    # 多跳题至少一条有 ≥2 个 gold 跨度;否定题参考答案为拒答语义。
    assert any(len(it.gold_spans) >= 2 for it in items if it.slices.get("q_type") == "multi_hop")
    assert all(it.id for it in items) and len({it.id for it in items}) == len(items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_golden_fixture.py -q`
Expected: FAIL (`FileNotFoundError` golden.jsonl)

- [ ] **Step 3: Write minimal implementation**

Create `backend/tests/fixtures/rag_eval/golden.jsonl` with these exact lines (offsets are bootstrap placeholders, re-pointed in Task 10's `index` step once the real corpus is indexed):

```json
{"id":"g0001","question":"缓存命中率怎么计算?","gold_spans":[{"ingest_record_id":1,"doc_char_start":0,"doc_char_end":40}],"reference_answer":"命中率 = 命中次数 / 总访问次数。","slices":{"domain":"study-cs","doc_type":"pdf","lang":"zh","q_type":"single_hop"},"provenance":"hand","source":"own","corpus_version":"v1"}
{"id":"g0002","question":"什么是写回(write-back)缓存策略?","gold_spans":[{"ingest_record_id":1,"doc_char_start":200,"doc_char_end":360}],"reference_answer":"写回:数据先写缓存,脏块被替换时才写回主存。","slices":{"domain":"study-cs","doc_type":"pdf","lang":"zh","q_type":"single_hop"},"provenance":"hand","source":"own","corpus_version":"v1"}
{"id":"g0003","question":"What is the difference between a stack and a heap in memory layout?","gold_spans":[{"ingest_record_id":2,"doc_char_start":0,"doc_char_end":180}],"reference_answer":"The stack grows downward for call frames; the heap is dynamically allocated.","slices":{"domain":"study-cs","doc_type":"c","lang":"en","q_type":"single_hop"},"provenance":"hand","source":"own","corpus_version":"v1"}
{"id":"g0004","question":"对比直接映射和组相联缓存在命中率与成本上的取舍。","gold_spans":[{"ingest_record_id":1,"doc_char_start":400,"doc_char_end":560},{"ingest_record_id":3,"doc_char_start":80,"doc_char_end":240}],"reference_answer":"直接映射成本低但冲突多;组相联降冲突但比较器更多、成本更高。","slices":{"domain":"study-cs","doc_type":"pdf","lang":"zh","q_type":"multi_hop"},"provenance":"hand","source":"own","corpus_version":"v1"}
{"id":"g0005","question":"How does pipelining interact with branch prediction across these notes?","gold_spans":[{"ingest_record_id":2,"doc_char_start":200,"doc_char_end":340},{"ingest_record_id":4,"doc_char_start":0,"doc_char_end":160}],"reference_answer":"Pipelining overlaps stages; mispredicted branches flush the pipeline, so prediction reduces stalls.","slices":{"domain":"study-cs","doc_type":"pdf","lang":"en","q_type":"multi_hop"},"provenance":"hand","source":"own","corpus_version":"v1"}
{"id":"g0006","question":"这门课的讲义里有讲到量子纠错码吗?","gold_spans":[],"reference_answer":"资料中没有提到量子纠错码,无法回答。","slices":{"domain":"study-cs","doc_type":"pdf","lang":"zh","q_type":"negation"},"provenance":"hand","source":"own","corpus_version":"v1"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_golden_fixture.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/rag_eval/golden.jsonl backend/tests/test_rag_eval_golden_fixture.py
git commit -m "feat(rag-eval): hand-seed golden fixture spanning slices

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Retrieve runner (point ①) — injectable retriever, write artifacts

**Files:**
- Create: `backend/scripts/rag_eval/runner.py`
- Test: `backend/tests/test_rag_eval_runner.py`

**Interfaces:**
- Consumes: `GoldItem` (Task 1); all metric functions (Tasks 2–3); `EvalConfig` (Task 4). `retriever` = any object with `retrieve(*, project_id, query, k, dense_n, fuse_m)->list[chunk]` (production `HybridRetriever` satisfies it; fake in tests).
- Produces: `run_retrieve(golden, retriever, *, project_id, config)->dict` returning `{"config_hash", "n", "per_question":[...], "by_slice":{...}, "overall":{...}}`; `write_run(result, runs_dir)->Path` (writes `<runs_dir>/<config_hash>-<seq>/{config.json,per_question.jsonl,summary.json}`); each per-question record has `id`, `slices`, and a `metrics` dict keyed `recall_any@{k}`, `recall_cov@{k}`, `ndcg@{k}`, `ctxp@{k}`, `ctxp_ord@{k}` for each k in `config.k_values`, plus `mrr`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_runner.py
from collections import namedtuple

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import GoldItem, GoldSpan
from scripts.rag_eval.runner import run_retrieve, write_run

C = namedtuple("C", "ingest_record_id char_start char_end")


class _FakeRetriever:
    """据问题返回固定排序结果(不碰真模型/Milvus)。"""
    def __init__(self, mapping):
        self._m = mapping

    def retrieve(self, *, project_id, query, k, dense_n, fuse_m):
        return self._m.get(query, [])[:k]


def _golden():
    return [
        GoldItem("g1", "q-hit-top1", (GoldSpan(1, 0, 10),), "", {"q_type": "single_hop"}, "hand", "own", "v1"),
        GoldItem("g2", "q-miss", (GoldSpan(5, 0, 10),), "", {"q_type": "single_hop"}, "hand", "own", "v1"),
    ]


def test_run_retrieve_aggregates(tmp_path):
    retr = _FakeRetriever({
        "q-hit-top1": [C(1, 0, 5), C(9, 0, 1)],
        "q-miss": [C(8, 0, 1), C(7, 0, 1)],
    })
    cfg = EvalConfig(k=6, k_values=(1, 3))
    res = run_retrieve(_golden(), retr, project_id=42, config=cfg)
    assert res["n"] == 2
    assert res["config_hash"] == cfg.config_hash()
    # overall recall_any@1 = (1 命中 + 0) / 2 = 0.5
    assert res["overall"]["recall_any@1"] == 0.5
    # 分片存在 single_hop。
    assert "q_type=single_hop" in res["by_slice"]

    path = write_run(res, tmp_path / "runs")
    assert (path / "summary.json").is_file()
    assert (path / "per_question.jsonl").is_file()
    assert (path / "config.json").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_runner.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.runner`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/runner.py
"""检索器单测 runner(测量点 ①):每题用 raw 问题查 HybridRetriever,算确定性检索指标。免 LLM。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.metrics import (
    context_precision_at_k, context_precision_ordered_at_k, mrr,
    ndcg_at_k, recall_any_at_k, recall_coverage_at_k,
)


def _per_question_metrics(ranked, gold_spans, k_values) -> dict:
    m: dict = {"mrr": mrr(ranked, gold_spans)}
    for k in k_values:
        m[f"recall_any@{k}"] = recall_any_at_k(ranked, gold_spans, k)
        m[f"recall_cov@{k}"] = recall_coverage_at_k(ranked, gold_spans, k)
        m[f"ndcg@{k}"] = ndcg_at_k(ranked, gold_spans, k)
        m[f"ctxp@{k}"] = context_precision_at_k(ranked, gold_spans, k)
        m[f"ctxp_ord@{k}"] = context_precision_ordered_at_k(ranked, gold_spans, k)
    return m


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _aggregate(per_q: list[dict]) -> dict:
    keys = per_q[0]["metrics"].keys() if per_q else []
    return {k: _mean([r["metrics"][k] for r in per_q]) for k in keys}


def run_retrieve(golden, retriever, *, project_id: int, config: EvalConfig) -> dict:
    per_q: list[dict] = []
    for it in golden:
        ranked = retriever.retrieve(project_id=project_id, query=it.question,
                                    k=config.k, dense_n=config.dense_n, fuse_m=config.fuse_m)
        per_q.append({"id": it.id, "slices": it.slices,
                      "metrics": _per_question_metrics(ranked, it.gold_spans, config.k_values)})

    by_slice: dict = {}
    for dim in ("domain", "doc_type", "lang", "q_type"):
        for rec in per_q:
            val = rec["slices"].get(dim)
            if val is None:
                continue
            by_slice.setdefault(f"{dim}={val}", []).append(rec)
    by_slice = {kk: _aggregate(v) for kk, v in by_slice.items()}

    return {"config_hash": config.config_hash(), "n": len(per_q),
            "per_question": per_q, "by_slice": by_slice, "overall": _aggregate(per_q)}


def write_run(result: dict, runs_dir: str | Path) -> Path:
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    base = result["config_hash"]
    seq = len(list(runs_dir.glob(f"{base}-*")))
    out = runs_dir / f"{base}-{seq}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(
        {k: result[k] for k in ("config_hash", "n", "by_slice", "overall")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "per_question.jsonl").open("w", encoding="utf-8") as f:
        for rec in result["per_question"]:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (out / "config.json").write_text(json.dumps({"config_hash": base}, ensure_ascii=False), encoding="utf-8")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_runner.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/runner.py backend/tests/test_rag_eval_runner.py
git commit -m "feat(rag-eval): retrieve runner (point 1) with run artifacts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Report — per-slice tables + run-vs-run diff

**Files:**
- Create: `backend/scripts/rag_eval/report.py`
- Test: `backend/tests/test_rag_eval_report.py`

**Interfaces:**
- Consumes: a `summary` dict shaped like `write_run`'s `summary.json` (`{"config_hash","n","by_slice","overall"}`).
- Produces: `format_report(summary, metrics=None)->str` (markdown: overall row + one row per slice; `metrics` selects columns, default a fixed core set); `diff_runs(summary_a, summary_b, metrics=None)->str` (markdown: per-metric `b-a` deltas for overall + shared slices, with `▲/▼/=` markers).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_report.py
from scripts.rag_eval.report import diff_runs, format_report

SUM_A = {"config_hash": "aaa", "n": 2,
         "overall": {"recall_any@5": 0.50, "mrr": 0.40},
         "by_slice": {"lang=zh": {"recall_any@5": 0.40, "mrr": 0.30}}}
SUM_B = {"config_hash": "bbb", "n": 2,
         "overall": {"recall_any@5": 0.70, "mrr": 0.45},
         "by_slice": {"lang=zh": {"recall_any@5": 0.40, "mrr": 0.50}}}


def test_format_report_has_overall_and_slice():
    out = format_report(SUM_A, metrics=["recall_any@5", "mrr"])
    assert "overall" in out and "lang=zh" in out
    assert "0.50" in out and "0.40" in out


def test_diff_marks_direction():
    out = diff_runs(SUM_A, SUM_B, metrics=["recall_any@5", "mrr"])
    assert "+0.20" in out or "0.20" in out      # overall recall_any@5 升
    assert "▲" in out and "=" in out            # 升 + 持平(zh recall_any@5 不变)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_report.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.report`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/report.py
"""分片报告 + run-vs-run delta(markdown)。"""
from __future__ import annotations

_CORE = ["recall_any@5", "recall_cov@5", "mrr", "ndcg@5", "ctxp_ord@5"]


def _rows(summary: dict) -> dict[str, dict]:
    rows = {"overall": summary["overall"]}
    rows.update(summary.get("by_slice", {}))
    return rows


def format_report(summary: dict, metrics: list[str] | None = None) -> str:
    metrics = metrics or _CORE
    rows = _rows(summary)
    head = "| slice | " + " | ".join(metrics) + " |"
    sep = "|" + "---|" * (len(metrics) + 1)
    lines = [f"# run {summary.get('config_hash','?')} (n={summary.get('n','?')})", "", head, sep]
    for name, mvals in rows.items():
        cells = [f"{mvals.get(m, float('nan')):.2f}" for m in metrics]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _mark(delta: float) -> str:
    return "▲" if delta > 1e-9 else ("▼" if delta < -1e-9 else "=")


def diff_runs(summary_a: dict, summary_b: dict, metrics: list[str] | None = None) -> str:
    metrics = metrics or _CORE
    ra, rb = _rows(summary_a), _rows(summary_b)
    head = "| slice | " + " | ".join(metrics) + " |"
    sep = "|" + "---|" * (len(metrics) + 1)
    lines = [f"# diff {summary_a.get('config_hash','A')} → {summary_b.get('config_hash','B')}",
             "", head, sep]
    for name in ra:
        if name not in rb:
            continue
        cells = []
        for m in metrics:
            d = rb[name].get(m, 0.0) - ra[name].get(m, 0.0)
            cells.append(f"{d:+.2f}{_mark(d)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_report.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/report.py backend/tests/test_rag_eval_report.py
git commit -m "feat(rag-eval): per-slice report + run-vs-run diff

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: CLI wiring (`index` / `build-corpus` / `retrieve` / `report` / `diff`)

**Files:**
- Create: `backend/scripts/rag_eval/cli.py`
- Create: `backend/scripts/rag_eval/wiring.py` (real-component construction, lazy imports)
- Test: `backend/tests/test_rag_eval_cli.py`

**Interfaces:**
- Consumes: `run_retrieve`/`write_run` (Task 7); `format_report`/`diff_runs` (Task 8); `load_golden` (Task 1); `EvalConfig` (Task 4).
- Produces: `main(argv:list[str]|None=None)->int` with subcommands `build-corpus`, `index`, `retrieve`, `report`, `diff`; `wiring.build_retriever(project_id)->HybridRetriever` (lazy-imports embedder/store/reranker, **warms embedder before Milvus** per [[macos-embedding-milvus-fork-order]]). `report`/`diff` read `summary.json` files and print markdown; `retrieve` builds the retriever via `wiring`, runs, writes a run, prints the report.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_cli.py
import json

from scripts.rag_eval.cli import main


def test_report_subcommand_reads_summary(tmp_path, capsys):
    s = tmp_path / "summary.json"
    s.write_text(json.dumps({"config_hash": "abc", "n": 1,
                             "overall": {"recall_any@5": 0.5, "mrr": 0.5},
                             "by_slice": {}}), encoding="utf-8")
    rc = main(["report", "--summary", str(s)])
    assert rc == 0
    assert "run abc" in capsys.readouterr().out


def test_diff_subcommand(tmp_path, capsys):
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    a.write_text(json.dumps({"config_hash": "a", "n": 1, "overall": {"mrr": 0.3}, "by_slice": {}}), encoding="utf-8")
    b.write_text(json.dumps({"config_hash": "b", "n": 1, "overall": {"mrr": 0.6}, "by_slice": {}}), encoding="utf-8")
    rc = main(["diff", "--a", str(a), "--b", str(b), "--metrics", "mrr"])
    assert rc == 0
    assert "diff a → b" in capsys.readouterr().out


def test_unknown_subcommand_returns_nonzero(capsys):
    assert main(["bogus"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_cli.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.cli`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/wiring.py
"""真生产组件装配(懒导入重依赖:FlagEmbedding / Milvus / reranker)。仅 CLI 真跑时调用。"""
from __future__ import annotations


def build_retriever(project_id: int):
    # 懒导入:测试/纯逻辑路径不拉重依赖。
    from epictrace.embedding.bge_m3 import BgeM3Embedder
    from epictrace.retrieval.pipeline import HybridRetriever
    from epictrace.retrieval.rerank import BgeReranker
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore

    embedder = BgeM3Embedder()
    reranker = BgeReranker()
    embedder.warmup()          # 必须在建 Milvus(gRPC)之前 warmup,避免 macOS fork 段错误
    reranker.warmup()
    store = MilvusLiteStore()  # 默认数据目录;eval 索引在该库内,project_id 区隔
    return HybridRetriever(embedder, store, reranker)
```

```python
# backend/scripts/rag_eval/cli.py
"""rag-eval CLI:index / build-corpus / retrieve / report / diff。手动跑,不进 CI。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import load_golden
from scripts.rag_eval.report import diff_runs, format_report
from scripts.rag_eval.runner import run_retrieve, write_run

_RUNS = Path(__file__).parent / "runs"


def _load_summary(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _cmd_report(ns) -> int:
    print(format_report(_load_summary(ns.summary), metrics=ns.metrics))
    return 0


def _cmd_diff(ns) -> int:
    print(diff_runs(_load_summary(ns.a), _load_summary(ns.b), metrics=ns.metrics))
    return 0


def _cmd_retrieve(ns) -> int:
    from scripts.rag_eval.wiring import build_retriever
    golden = load_golden(ns.golden)
    cfg = EvalConfig(k=ns.k, dense_n=ns.dense_n, fuse_m=ns.fuse_m, label=ns.label or "")
    retr = build_retriever(ns.project_id)
    res = run_retrieve(golden, retr, project_id=ns.project_id, config=cfg)
    out = write_run(res, _RUNS)
    print(format_report({k: res[k] for k in ("config_hash", "n", "by_slice", "overall")}))
    print(f"\n[rag-eval] run written to {out}", file=sys.stderr)
    return 0


def _cmd_index(ns) -> int:
    # 真重活:把 eval-data 入库到 eval Project 并建索引。懒导入,手动跑。
    from scripts.rag_eval.indexing import index_eval_corpus  # 见 Task 10 备注
    pid = index_eval_corpus(ns.eval_data, project_name=ns.project_name)
    print(f"[rag-eval] indexed eval corpus into project_id={pid}", file=sys.stderr)
    return 0


def _cmd_build_corpus(ns) -> int:
    from scripts.rag_eval.corpus import build_corpus
    from scripts.rag_eval.corpus_spec import load_entries   # 本地 gitignored spec,见 Task 10 备注
    rows = build_corpus(load_entries(ns.spec), dest=Path(ns.dest), corpus_version=ns.corpus_version)
    print(f"[rag-eval] copied {len(rows)} files into {ns.dest}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rag-eval")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("retrieve"); r.set_defaults(fn=_cmd_retrieve)
    r.add_argument("--golden", required=True); r.add_argument("--project-id", dest="project_id", type=int, required=True)
    r.add_argument("--k", type=int, default=6); r.add_argument("--dense-n", dest="dense_n", type=int, default=30)
    r.add_argument("--fuse-m", dest="fuse_m", type=int, default=20); r.add_argument("--label", default="")

    rep = sub.add_parser("report"); rep.set_defaults(fn=_cmd_report)
    rep.add_argument("--summary", required=True); rep.add_argument("--metrics", nargs="*", default=None)

    d = sub.add_parser("diff"); d.set_defaults(fn=_cmd_diff)
    d.add_argument("--a", required=True); d.add_argument("--b", required=True); d.add_argument("--metrics", nargs="*", default=None)

    idx = sub.add_parser("index"); idx.set_defaults(fn=_cmd_index)
    idx.add_argument("--eval-data", dest="eval_data", required=True); idx.add_argument("--project-name", dest="project_name", default="rag-eval")

    bc = sub.add_parser("build-corpus"); bc.set_defaults(fn=_cmd_build_corpus)
    bc.add_argument("--spec", required=True); bc.add_argument("--dest", required=True); bc.add_argument("--corpus-version", dest="corpus_version", default="v1")

    ns = p.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(ns, "fn", None):
        p.print_usage(sys.stderr)
        return 2
    return ns.fn(ns)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_cli.py -q`
Expected: PASS (3 passed). The `index`/`build-corpus`/`retrieve` paths lazy-import their heavy helpers, so they are not touched by these CLI tests.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/cli.py backend/scripts/rag_eval/wiring.py backend/tests/test_rag_eval_cli.py
git commit -m "feat(rag-eval): CLI (report/diff tested; retrieve/index/build-corpus lazy-wired)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `index` + `build-corpus` helpers, gitignore, end-to-end smoke

**Files:**
- Create: `backend/scripts/rag_eval/indexing.py` (`index_eval_corpus`)
- Create: `backend/scripts/rag_eval/corpus_spec.py` (`load_entries`)
- Create: `backend/scripts/rag_eval/corpus_spec.example.json` (checked-in template)
- Modify: `backend/.gitignore` (or repo root `.gitignore`) — add `eval-data/`, `scripts/rag_eval/runs/`, `scripts/rag_eval/corpus_spec.json`
- Test: `backend/tests/test_rag_eval_end_to_end.py`

**Interfaces:**
- Consumes: `IngestService` + the indexing job (existing); `build_corpus` (Task 5); `run_retrieve`/`write_run`/`format_report` (Tasks 7–8).
- Produces: `index_eval_corpus(eval_data_dir, *, project_name)->int` (creates/locates an eval Project, ingests every file under `eval_data_dir` via `IngestService.ingest_file(ingest_method="rag_eval")`, runs the index job, returns `project_id`); `corpus_spec.load_entries(spec_path)->list[CorpusEntry]` (reads a JSON list of `{glob, slices, source}`, expands globs against the user's read-only source dirs).

- [ ] **Step 1: Write the failing test** (end-to-end over fakes — no real model)

```python
# backend/tests/test_rag_eval_end_to_end.py
"""端到端串起 Task 1-8(注入假检索器),证明 golden → run → report 全链路通。"""
from collections import namedtuple

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import GoldItem, GoldSpan
from scripts.rag_eval.report import format_report
from scripts.rag_eval.runner import run_retrieve, write_run

C = namedtuple("C", "ingest_record_id char_start char_end")


class _FakeRetriever:
    def retrieve(self, *, project_id, query, k, dense_n, fuse_m):
        # q1 命中其 gold(record 1),q2 不命中。
        return {"q1": [C(1, 0, 5)], "q2": [C(9, 0, 1)]}.get(query, [])[:k]


def test_golden_to_run_to_report(tmp_path):
    golden = [
        GoldItem("g1", "q1", (GoldSpan(1, 0, 10),), "", {"lang": "zh", "q_type": "single_hop"}, "hand", "own", "v1"),
        GoldItem("g2", "q2", (GoldSpan(2, 0, 10),), "", {"lang": "en", "q_type": "single_hop"}, "hand", "own", "v1"),
    ]
    res = run_retrieve(golden, _FakeRetriever(), project_id=1, config=EvalConfig(k=6, k_values=(1, 5)))
    assert res["overall"]["recall_any@5"] == 0.5
    out = write_run(res, tmp_path / "runs")
    report = format_report({k: res[k] for k in ("config_hash", "n", "by_slice", "overall")},
                           metrics=["recall_any@5", "mrr"])
    assert "lang=zh" in report and "lang=en" in report
    assert out.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_end_to_end.py -q`
Expected: At this point the imports already exist (Tasks 1–8), so write the test FIRST and confirm it PASSES only after the helper/gitignore files below are added; if it passes immediately, that's fine — its purpose is a regression guard. Run it and note the result.

- [ ] **Step 3: Write the helper + spec + gitignore**

```python
# backend/scripts/rag_eval/corpus_spec.py
"""读本地(gitignored)corpus_spec.json:[{glob, slices, source}] → CorpusEntry 列表(展开 glob)。"""
from __future__ import annotations

import json
from glob import glob
from pathlib import Path

from scripts.rag_eval.corpus import CorpusEntry


def load_entries(spec_path: str | Path) -> list[CorpusEntry]:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    entries: list[CorpusEntry] = []
    for grp in spec:
        for hit in sorted(glob(grp["glob"], recursive=True)):
            p = Path(hit)
            if p.is_file():
                entries.append(CorpusEntry(src=p, slices=grp.get("slices", {}),
                                           source=grp.get("source", "own")))
    return entries
```

```python
# backend/scripts/rag_eval/indexing.py
"""把 eval-data 入库到一个 eval Project 并建索引。真重活,懒导入,手动跑。"""
from __future__ import annotations

from pathlib import Path


def index_eval_corpus(eval_data_dir: str | Path, *, project_name: str = "rag-eval") -> int:
    from epictrace.api.deps import get_db
    from epictrace.services.index import IndexService
    from epictrace.services.ingest import IngestService
    from epictrace.services.projects import ProjectService

    db = next(get_db())
    proj = ProjectService(db).get_or_create(project_name)   # 若无此方法,用 create + 查重等价实现
    ing = IngestService(db)
    for f in sorted(Path(eval_data_dir).glob("*")):
        if f.is_file() and f.name != "manifest.jsonl":
            ing.ingest_file(project_id=proj.id, path=str(f), ingest_method="rag_eval")
    IndexService(db).build_index(proj.id)                   # 复用现有索引 job(真 embedding)
    return proj.id
```

> **Implementer note:** verify the exact method names against current code — `ProjectService` may expose `create`/`get_by_name` rather than `get_or_create`, and `IngestService.ingest_file` / `IndexService.build_index` signatures must be matched verbatim (grep `def ingest_file`, `def build_index`). Adjust the calls to the real signatures; do **not** invent parameters. This helper is **manual-run only** and has no unit test (it needs the real embedding model).

Create `backend/scripts/rag_eval/corpus_spec.example.json`:

```json
[
  {"glob": "/Volumes/Port-Data/Archive/CS 2505/**/*.pdf", "slices": {"domain": "study-cs", "doc_type": "pdf", "lang": "en", "q_type": "single_hop"}, "source": "own"},
  {"glob": "/Users/william/Desktop/TX AI培训/**/*.docx", "slices": {"domain": "work-ai", "doc_type": "docx", "lang": "zh", "q_type": "single_hop"}, "source": "own"}
]
```

Add to `.gitignore` (backend or repo root, matching existing convention):

```
eval-data/
backend/eval-data/
scripts/rag_eval/runs/
backend/scripts/rag_eval/runs/
backend/scripts/rag_eval/corpus_spec.json
```

- [ ] **Step 4: Run the full suite + confirm nothing real is spawned**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_*.py -q`
Expected: PASS (all rag_eval tests green). Then run the whole suite `./.venv/bin/pytest -q` and confirm no regressions and that no rag_eval test imported FlagEmbedding/Milvus (they are lazy-imported only in `wiring.py`/`indexing.py`, which the tests never call).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/indexing.py backend/scripts/rag_eval/corpus_spec.py backend/scripts/rag_eval/corpus_spec.example.json backend/tests/test_rag_eval_end_to_end.py .gitignore
git commit -m "feat(rag-eval): index/build-corpus helpers + gitignore + e2e smoke

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Manual bring-up (run once after Task 10, not a unit test)

These steps produce the **baseline run** (Plan 1's deliverable). They need the real models and the user's read-only source dirs; do them on William's Mac, not in CI:

1. `cp backend/scripts/rag_eval/corpus_spec.example.json backend/scripts/rag_eval/corpus_spec.json` and edit it to point at the real slices (CS 2505/2506/2104/2114, TX AI培训), excluding `mp4`/`m4a`.
2. `./.venv/bin/python -m scripts.rag_eval.cli build-corpus --spec scripts/rag_eval/corpus_spec.json --dest eval-data --corpus-version v1`
3. `./.venv/bin/python -m scripts.rag_eval.cli index --eval-data eval-data` → note the `project_id`.
4. **Re-point the golden fixture offsets**: for each hand item, open the indexed doc's extracted text and set `ingest_record_id` + `doc_char_start/doc_char_end` to the real answer-bearing span (the fixture's placeholder offsets from Task 6 are intentionally provisional). Re-run `tests/test_rag_eval_golden_fixture.py`.
5. `./.venv/bin/python -m scripts.rag_eval.cli retrieve --golden tests/fixtures/rag_eval/golden.jsonl --project-id <pid> --label baseline` → first baseline report.
6. Sweep: re-run `retrieve` with different `--k/--dense-n/--fuse-m`, then `diff --a runs/<baseline>/summary.json --b runs/<variant>/summary.json` to see deltas. This is the tuning loop; it quantifies the §11 weaknesses (e.g. Chinese-doc precision) against the slice report.

---

## Self-Review

**Spec coverage (against `2026-06-21-rag-eval-design.md`):**
- §3 three measurement points / two-tier loop → point ① is Task 7 (`run_retrieve`); points ②③ are Plan 2. Inner loop = `retrieve`; chunker-hash seam = Task 4. ✓
- §4 corpus (stratified, read-only copy-out, manifest) → Task 5 + Task 10 (`build_corpus`, `corpus_spec`, gitignore). ✓
- §5 golden (doc-char-range gold, schema) → Tasks 1, 6. **Synthesis (5.3 steps 3–4) + review CLI (5.5) deferred to Plan 2** (need LLM) — noted in Global Constraints. ✓ (gap is intentional + flagged)
- §6.A retrieval metrics (recall any/cov, MRR, nDCG, ctxp plain+ordered) → Tasks 2, 3. ✓
- §6.B/§6.C/§6.D citation + generation + judge → **Plan 2** (need generated answer / LLM). ✓ (intentional)
- §7 config + two-tier runner → Tasks 4, 7; `run` outer loop → Plan 2. ✓
- §8 report + diff → Task 8 (+ CLI Task 9). ✓
- §9 落地形态 (scripts/rag_eval, reuse real components, lazy import) → Tasks 9, 10. ✓
- §10 self-tests (metric pure-fns, runner smoke with fakes) → Tasks 2,3,7,10. ✓

**Placeholder scan:** No "TBD/handle-errors" placeholders. The golden fixture offsets are explicitly provisional with a named re-point step (manual bring-up #4); the `index_eval_corpus` helper carries a verify-signatures implementer note rather than invented params — both are deliberate, not vague.

**Type consistency:** metric functions take `(ranked, gold_spans, k)` consistently; `run_retrieve` metric keys (`recall_any@{k}`, `recall_cov@{k}`, `ndcg@{k}`, `ctxp@{k}`, `ctxp_ord@{k}`, `mrr`) are produced in Task 7 and consumed by Task 8's `_CORE`/`metrics` selection; `EvalConfig.config_hash()` used by runner + write_run; `GoldItem`/`GoldSpan` fields identical across Tasks 1/6/7/10.

**Known follow-ups for Plan 2:** Anthropic judge client; LLM golden synthesis + auto-filters + `review-golden` CLI; citation metrics (validity/accuracy/faithfulness); generation metrics (faithfulness/relevancy/correctness/refusal); `run` outer loop with judge cache; Cohen's-kappa calibration; optional DeepSeek second-judge.

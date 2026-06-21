# RAG Eval — Plan 2: Judged Outer Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the LLM-judged outer loop on top of Plan 1 — an Anthropic-Messages judge client, LLM golden synthesis (to scale the golden set with correct offsets), citation + generation metrics, an outer-loop runner over the real agent (measurement points ② agentic-retrieval and ③ final-answer), a judge cache, and Cohen's-kappa calibration — so generation quality, faithfulness, and citation correctness can be measured and tuned.

**Architecture:** Same manual-run package `backend/scripts/rag_eval/` (lazy heavy deps). The judge is a thin httpx client to the **Anthropic Messages API** (`/v1/messages`) on a Claude-Opus model — a **different family** from the DeepSeek generator-under-test, so it cannot self-prefer. The outer-loop runner reuses the real agent primitives (`build_tools`, `run_react_loop`, `ChunkAccumulator`, `stream_final_answer`, `build_citations`) and is fully injectable so smoke tests use `FakeChatModel` + fake judge + fake retriever — no real model, no network.

**Tech Stack:** Python 3.11, stdlib + `httpx` (already a dependency — no new deps), pytest. Reuses `epictrace.agent.{tools,react,answer,state,citations}` and Plan 1's `scripts.rag_eval.{golden,metrics,config,runner}`.

## Global Constraints

- Python **3.11**; backend venv `backend/.venv`; run tests with `./.venv/bin/pytest` from `backend/`.
- **Tests must never spawn real models / network**; heavy deps lazy-imported; smoke tests inject `FakeChatModel` (`backend/tests/fakes.py`), a fake judge (canned dicts), and a fake httpx transport. No test calls the real judge endpoint.
- **No new third-party deps** — judge client uses `httpx` (already used by the project).
- Package `backend/scripts/rag_eval/` (manual-run, not CI). Docstrings/comments in **简体中文**; identifiers/paths/commands English.
- **Judge = `claude-opus-4-8`** via **Anthropic Messages** `POST {BASE_URL}/v1/messages` (live-verified: OpenAI `chat_completions` 404s for claude). Headers: `x-api-key: <key>` **and** `anthropic-version: 2023-06-01` + `content-type: application/json`. Response text at `content[0].text`.
- **Judge determinism does NOT use temperature** (Opus 4.8 dropped it as a lever) — stability comes from (1) the judge cache, (2) structured JSON output. Judge failures → metric value **NaN, never 0**.
- **JSON gotcha**: Opus wraps JSON in ```json fences even when told not to → the judge client **strips markdown fences before `json.loads`**.
- Judge `BASE_URL` + key come from the local `temp_key` file (`BASE_URL=https://api-slb.krill-ai.com`, `KEY=...`) or env; the **key is a secret — never commit it, never print it, never write it into any artifact**.
- **Judge ≠ generator**: the被测 generator is DeepSeek V4 Pro (the project's BYOK chat profile); the judge is Claude Opus. Keep them on separate clients.
- Gold spans are document char ranges; hit = same `ingest_record_id` + char overlap (reuse `scripts.rag_eval.metrics.chunk_hits`).
- git author `ep1sode-33`; commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Builds on Plan 1 (branch `feat/rag-eval`, already merged into the branch). Plan 1 modules are stable interfaces; do not modify them except the additive `report.py` extension in Task 9.

---

### Task 1: Anthropic judge client + JSON extraction

**Files:**
- Create: `backend/scripts/rag_eval/judge_client.py`
- Test: `backend/tests/test_rag_eval_judge_client.py`

**Interfaces:**
- Produces: `extract_json(text:str)->dict|None` (strips ```json/``` fences, `json.loads`; None on failure); `JudgeConfig(base_url:str, api_key:str, model:str)`; `load_judge_config(keyfile:str|None=None)->JudgeConfig` (reads env `RAG_EVAL_JUDGE_BASE_URL`/`RAG_EVAL_JUDGE_KEY`/`RAG_EVAL_JUDGE_MODEL`, else parses `keyfile` or `~/Desktop/temp_key` lines `BASE_URL=`/`KEY=`; model default `claude-opus-4-8`); `class AnthropicJudge` with `__init__(self, config:JudgeConfig, *, transport=None, retries:int=2)` (`transport` is an injectable callable `(url, headers, json_body)->(status:int, json:dict)` for tests; default uses `httpx`) and `judge_json(self, system:str, user:str, *, max_tokens:int=1024)->dict|None` (POST `/v1/messages`, parse `content[0].text` via `extract_json`, retry on exception/non-200/parse-failure, return None after retries).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_judge_client.py
from scripts.rag_eval.judge_client import AnthropicJudge, JudgeConfig, extract_json, load_judge_config


def test_extract_json_strips_fences():
    assert extract_json('```json\n{"supported": true}\n```') == {"supported": True}
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("not json") is None


def test_load_judge_config_from_keyfile(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_EVAL_JUDGE_KEY", raising=False)
    monkeypatch.delenv("RAG_EVAL_JUDGE_BASE_URL", raising=False)
    kf = tmp_path / "temp_key"
    kf.write_text("KEY=sk-abc123\nBASE_URL=https://api-slb.krill-ai.com\n", encoding="utf-8")
    cfg = load_judge_config(str(kf))
    assert cfg.api_key == "sk-abc123"
    assert cfg.base_url == "https://api-slb.krill-ai.com"
    assert cfg.model == "claude-opus-4-8"


def test_judge_json_parses_messages_response():
    calls = {}

    def fake_transport(url, headers, json_body):
        calls["url"] = url
        calls["headers"] = headers
        return 200, {"content": [{"type": "text", "text": '```json\n{"verdict": "ok"}\n```'}]}

    j = AnthropicJudge(JudgeConfig("https://x", "sk-1", "claude-opus-4-8"), transport=fake_transport)
    out = j.judge_json("你是裁判", "判这个")
    assert out == {"verdict": "ok"}
    assert calls["url"].endswith("/v1/messages")
    assert calls["headers"]["x-api-key"] == "sk-1"
    assert calls["headers"]["anthropic-version"] == "2023-06-01"


def test_judge_json_returns_none_after_retries():
    def boom_transport(url, headers, json_body):
        return 500, {"error": "boom"}

    j = AnthropicJudge(JudgeConfig("https://x", "sk-1", "m"), transport=boom_transport, retries=1)
    assert j.judge_json("s", "u") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_judge_client.py -q`
Expected: FAIL (`ModuleNotFoundError: scripts.rag_eval.judge_client`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/judge_client.py
"""Anthropic Messages 判官客户端(claude-opus-4-8,经 krill-ai 代理)。与 DeepSeek 生成器分家。
key 是机密:不打印、不落任何产物。失败回 None(指标记 NaN,不记 0)。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(text: str) -> dict | None:
    """剥 markdown 围栏后 json.loads(Opus 即便被要求只出 JSON 也爱加 ```json 围栏)。"""
    if not text:
        return None
    stripped = _FENCE.sub("", text.strip())
    # 仅去首尾围栏行后仍可能含前后噪声:退而求其次截取第一个 { 到最后一个 }。
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        a, b = stripped.find("{"), stripped.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(stripped[a:b + 1])
            except json.JSONDecodeError:
                return None
        return None


@dataclass(frozen=True)
class JudgeConfig:
    base_url: str
    api_key: str
    model: str = "claude-opus-4-8"


def load_judge_config(keyfile: str | None = None) -> JudgeConfig:
    base = os.environ.get("RAG_EVAL_JUDGE_BASE_URL", "")
    key = os.environ.get("RAG_EVAL_JUDGE_KEY", "")
    model = os.environ.get("RAG_EVAL_JUDGE_MODEL", "claude-opus-4-8")
    if not (base and key):
        path = Path(keyfile or os.path.expanduser("~/Desktop/temp_key"))
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().upper(), v.strip()
            if k in ("KEY", "API_KEY") and not key:
                key = v
            elif k == "BASE_URL" and not base:
                base = v
    if not (base and key):
        raise RuntimeError("judge config 缺 BASE_URL / KEY(看 temp_key 或环境变量)")
    return JudgeConfig(base_url=base, api_key=key, model=model)


def _httpx_transport(url, headers, json_body):
    import httpx
    resp = httpx.post(url, headers=headers, json=json_body, timeout=120)
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, {}


class AnthropicJudge:
    def __init__(self, config: JudgeConfig, *, transport=None, retries: int = 2) -> None:
        self._cfg = config
        self._transport = transport or _httpx_transport
        self._retries = retries

    def judge_json(self, system: str, user: str, *, max_tokens: int = 1024) -> dict | None:
        url = self._cfg.base_url.rstrip("/") + "/v1/messages"
        headers = {"x-api-key": self._cfg.api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        body = {"model": self._cfg.model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        for _ in range(self._retries + 1):
            try:
                status, payload = self._transport(url, headers, body)
                if status == 200:
                    blocks = payload.get("content") or []
                    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                    parsed = extract_json(text)
                    if parsed is not None:
                        return parsed
            except Exception:  # noqa: BLE001 — 重试
                pass
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_judge_client.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/judge_client.py backend/tests/test_rag_eval_judge_client.py
git commit -m "feat(rag-eval): Anthropic Messages judge client + JSON extraction

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Judge cache (disk, keyed, NaN-safe)

**Files:**
- Create: `backend/scripts/rag_eval/judge_cache.py`
- Test: `backend/tests/test_rag_eval_judge_cache.py`

**Interfaces:**
- Produces: `cache_key(metric:str, question_id:str, answer:str, context:str, judge_model:str)->str` (sha256 of the parts, hex); `class JudgeCache(path:Path)` with `get(key:str)->dict|None` and `put(key:str, value:dict)->None`, persisted as JSONL appended on `put`, loaded on init. A cached `value` is the judge's parsed dict (never None — only successful judgments are cached, so failures re-attempt next run).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_judge_cache.py
from scripts.rag_eval.judge_cache import JudgeCache, cache_key


def test_cache_key_stable_and_sensitive():
    a = cache_key("faithfulness", "g1", "ans", "ctx", "claude-opus-4-8")
    b = cache_key("faithfulness", "g1", "ans", "ctx", "claude-opus-4-8")
    assert a == b
    assert a != cache_key("faithfulness", "g1", "ans2", "ctx", "claude-opus-4-8")


def test_put_get_persists(tmp_path):
    p = tmp_path / "judge_cache.jsonl"
    c = JudgeCache(p)
    assert c.get("k1") is None
    c.put("k1", {"score": 0.8})
    assert c.get("k1") == {"score": 0.8}
    # 新实例从磁盘恢复。
    assert JudgeCache(p).get("k1") == {"score": 0.8}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_judge_cache.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/judge_cache.py
"""judge 结果磁盘缓存:相同 (metric, qid, answer, context, model) 不重复付费。只缓存成功裁决。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cache_key(metric: str, question_id: str, answer: str, context: str, judge_model: str) -> str:
    h = hashlib.sha256()
    for part in (metric, question_id, answer, context, judge_model):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class JudgeCache:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._mem: dict[str, dict] = {}
        if self._path.is_file():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._mem[rec["k"]] = rec["v"]

    def get(self, key: str) -> dict | None:
        return self._mem.get(key)

    def put(self, key: str, value: dict) -> None:
        self._mem[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"k": key, "v": value}, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_judge_cache.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/judge_cache.py backend/tests/test_rag_eval_judge_cache.py
git commit -m "feat(rag-eval): disk judge cache (keyed, success-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Citation metrics (deterministic validity + accuracy)

**Files:**
- Create: `backend/scripts/rag_eval/metrics_citation.py`
- Test: `backend/tests/test_rag_eval_metrics_citation.py`

**Interfaces:**
- Consumes: `chunk_hits` from `scripts.rag_eval.metrics` (Plan 1); `pool` = list of `RetrievedChunk`-shaped objects (the order maps `[n]` → `pool[n-1]`, matching `agent.citations.build_citations`).
- Produces: `parse_citation_ids(answer:str)->list[int]` (all `[n]` in order, dedup-preserving-first); `citation_validity(answer:str, n_pool:int)->float` (fraction of cited ids with `1<=n<=n_pool`; `nan` if no citations); `citation_accuracy(answer:str, pool, gold_spans)->float` (of the *valid* cited chunks, fraction whose span hits a gold span; `nan` if no valid citations).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_metrics_citation.py
import math
from collections import namedtuple

from scripts.rag_eval.golden import GoldSpan
from scripts.rag_eval.metrics_citation import citation_accuracy, citation_validity, parse_citation_ids

C = namedtuple("C", "ingest_record_id char_start char_end")


def test_parse_ids_in_order_unique():
    assert parse_citation_ids("据 [2] 与 [1],又见 [2]。") == [2, 1]
    assert parse_citation_ids("无引用") == []


def test_validity():
    assert citation_validity("看 [1] 和 [3]", n_pool=3) == 1.0
    assert citation_validity("看 [1] 和 [9]", n_pool=3) == 0.5    # [9] 越界
    assert math.isnan(citation_validity("无引用", n_pool=3))


def test_accuracy_uses_gold():
    gold = (GoldSpan(1, 0, 50),)
    pool = [C(1, 10, 40), C(2, 0, 10)]      # [1] 命中 gold,[2] 不命中
    assert citation_accuracy("依据 [1]", pool, gold) == 1.0
    assert citation_accuracy("依据 [2]", pool, gold) == 0.0
    assert citation_accuracy("依据 [1] 和 [2]", pool, gold) == 0.5
    assert math.isnan(citation_accuracy("无引用", pool, gold))
    assert math.isnan(citation_accuracy("越界 [9]", pool, gold))   # 无合法引用
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics_citation.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/metrics_citation.py
"""引用指标。validity/accuracy 确定性(faithfulness 需 judge,见 metrics_generation)。"""
from __future__ import annotations

import math
import re

from scripts.rag_eval.metrics import chunk_hits

_CITE = re.compile(r"\[(\d+)\]")


def parse_citation_ids(answer: str) -> list[int]:
    seen, out = set(), []
    for m in _CITE.findall(answer or ""):
        n = int(m)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def citation_validity(answer: str, n_pool: int) -> float:
    ids = parse_citation_ids(answer)
    if not ids:
        return math.nan
    valid = sum(1 for n in ids if 1 <= n <= n_pool)
    return valid / len(ids)


def citation_accuracy(answer: str, pool, gold_spans) -> float:
    valid = [n for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]
    if not valid:
        return math.nan
    hits = sum(1 for n in valid if chunk_hits(pool[n - 1], gold_spans))
    return hits / len(valid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics_citation.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/metrics_citation.py backend/tests/test_rag_eval_metrics_citation.py
git commit -m "feat(rag-eval): deterministic citation validity + accuracy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Generation metrics — faithfulness + answer relevancy (judge)

**Files:**
- Create: `backend/scripts/rag_eval/metrics_generation.py`
- Test: `backend/tests/test_rag_eval_metrics_generation.py`

**Interfaces:**
- Consumes: a `judge` object with `judge_json(system, user)->dict|None` (Task 1 `AnthropicJudge`; fake in tests).
- Produces: `score_faithfulness(judge, *, answer:str, context:str)->float` (judge returns `{"claims":[{"text":..,"supported":bool}]}` → supported/total; `nan` if judge None or no claims); `score_answer_relevancy(judge, *, question:str, answer:str)->float` (judge returns `{"relevancy": 0..1}` clamped to [0,1]; `nan` if None). Each builds its own system+user prompt internally (Chinese, JSON-only instruction).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_metrics_generation.py
import math

from scripts.rag_eval.metrics_generation import score_answer_relevancy, score_faithfulness


class _FakeJudge:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def judge_json(self, system, user):
        self.calls.append((system, user))
        return self._reply


def test_faithfulness_fraction():
    j = _FakeJudge({"claims": [{"text": "a", "supported": True},
                               {"text": "b", "supported": False},
                               {"text": "c", "supported": True}]})
    assert math.isclose(score_faithfulness(j, answer="...", context="..."), 2 / 3, rel_tol=1e-9)
    assert "上下文" in j.calls[0][1] or "context" in j.calls[0][1].lower()


def test_faithfulness_nan_paths():
    assert math.isnan(score_faithfulness(_FakeJudge(None), answer="x", context="y"))
    assert math.isnan(score_faithfulness(_FakeJudge({"claims": []}), answer="x", context="y"))


def test_relevancy_clamped():
    assert score_answer_relevancy(_FakeJudge({"relevancy": 0.9}), question="q", answer="a") == 0.9
    assert score_answer_relevancy(_FakeJudge({"relevancy": 1.5}), question="q", answer="a") == 1.0
    assert math.isnan(score_answer_relevancy(_FakeJudge(None), question="q", answer="a"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics_generation.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/metrics_generation.py
"""生成指标(LLM judge)。judge.judge_json(system,user)->dict|None;None/无声明 → NaN(不记 0)。"""
from __future__ import annotations

import json
import math

_FAITH_SYS = "你是严格的 RAG 评测裁判。只输出 JSON,不要多余文字、不要解释。"


def score_faithfulness(judge, *, answer: str, context: str) -> float:
    """声明分解法:把答案拆成原子声明,逐条判是否被检索上下文蕴含。score = 被支撑/总数。"""
    user = (
        "把【答案】拆成原子声明,逐条判断它是否能由【上下文】支撑(蕴含)。\n"
        "只输出 JSON:{\"claims\":[{\"text\":\"...\",\"supported\":true/false}]}。\n\n"
        f"【上下文】\n{context}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out:
        return math.nan
    claims = out.get("claims") or []
    if not claims:
        return math.nan
    supported = sum(1 for c in claims if c.get("supported") is True)
    return supported / len(claims)


def score_answer_relevancy(judge, *, question: str, answer: str) -> float:
    """答非所问度:答案在多大程度上直接回答了问题(0..1)。"""
    user = (
        "判断【答案】在多大程度上直接回答了【问题】(0 到 1 的小数,1=完全切题)。\n"
        "只输出 JSON:{\"relevancy\": 0.0~1.0}。\n\n"
        f"【问题】\n{question}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out or "relevancy" not in out:
        return math.nan
    try:
        return max(0.0, min(1.0, float(out["relevancy"])))
    except (TypeError, ValueError):
        return math.nan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics_generation.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/metrics_generation.py backend/tests/test_rag_eval_metrics_generation.py
git commit -m "feat(rag-eval): generation metrics — faithfulness + answer relevancy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Generation metrics — answer correctness (claim F1) + refusal + citation faithfulness

**Files:**
- Modify: `backend/scripts/rag_eval/metrics_generation.py`
- Modify: `backend/scripts/rag_eval/metrics_citation.py` (add judge-based `score_citation_faithfulness`)
- Test: `backend/tests/test_rag_eval_metrics_generation.py` (append); `backend/tests/test_rag_eval_metrics_citation.py` (append)

**Interfaces:**
- Produces: `score_answer_correctness(judge, *, question:str, answer:str, reference:str)->float` (judge returns `{"answer_claims_supported":[bool,...], "reference_claims_covered":[bool,...]}` → P=mean(supported), R=mean(covered), F1=2PR/(P+R); `nan` if None/empty); `score_refusal_correctness(judge, *, question:str, answer:str)->float` (judge returns `{"is_refusal":bool}` → 1.0 if refusal else 0.0; `nan` if None) — **only called for unanswerable/negation items**; `score_citation_faithfulness(judge, *, answer:str, cited_texts:list[str])->float` (judge returns `{"citations":[{"supported":bool},...]}` → supported/total; `nan` if None/no citations).

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_rag_eval_metrics_generation.py
from scripts.rag_eval.metrics_generation import score_answer_correctness, score_refusal_correctness


def test_correctness_f1():
    j = _FakeJudge({"answer_claims_supported": [True, True, False],     # P = 2/3
                    "reference_claims_covered": [True, False]})          # R = 1/2
    p, r = 2 / 3, 1 / 2
    assert math.isclose(score_answer_correctness(j, question="q", answer="a", reference="ref"),
                        2 * p * r / (p + r), rel_tol=1e-9)
    assert math.isnan(score_answer_correctness(_FakeJudge(None), question="q", answer="a", reference="r"))


def test_refusal():
    assert score_refusal_correctness(_FakeJudge({"is_refusal": True}), question="q", answer="没有提到") == 1.0
    assert score_refusal_correctness(_FakeJudge({"is_refusal": False}), question="q", answer="是 X") == 0.0
    assert math.isnan(score_refusal_correctness(_FakeJudge(None), question="q", answer="a"))
```

```python
# append to backend/tests/test_rag_eval_metrics_citation.py
import math as _math

from scripts.rag_eval.metrics_citation import score_citation_faithfulness


class _FakeJudge2:
    def __init__(self, reply):
        self._reply = reply

    def judge_json(self, system, user):
        return self._reply


def test_citation_faithfulness():
    j = _FakeJudge2({"citations": [{"supported": True}, {"supported": False}]})
    assert score_citation_faithfulness(j, answer="见 [1][2]", cited_texts=["t1", "t2"]) == 0.5
    assert _math.isnan(score_citation_faithfulness(_FakeJudge2(None), answer="x", cited_texts=["t"]))
    assert _math.isnan(score_citation_faithfulness(_FakeJudge2({"citations": []}), answer="x", cited_texts=[]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics_generation.py tests/test_rag_eval_metrics_citation.py -q`
Expected: FAIL (`ImportError: cannot import name 'score_answer_correctness'` / `score_citation_faithfulness`)

- [ ] **Step 3: Write minimal implementation**

```python
# append to backend/scripts/rag_eval/metrics_generation.py
def _mean_bool(xs) -> float:
    return sum(1 for x in xs if x is True) / len(xs) if xs else math.nan


def score_answer_correctness(judge, *, question: str, answer: str, reference: str) -> float:
    """声明级 F1:答案声明被参考支撑(P)× 参考声明被答案覆盖(R)。"""
    user = (
        "对照【参考答案】评估【答案】。给出两个布尔数组:\n"
        "answer_claims_supported(答案每条原子声明是否被参考支撑)、"
        "reference_claims_covered(参考每条原子声明是否被答案覆盖)。\n"
        "只输出 JSON:{\"answer_claims_supported\":[true/false...],"
        "\"reference_claims_covered\":[true/false...]}。\n\n"
        f"【问题】\n{question}\n\n【参考答案】\n{reference}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out:
        return math.nan
    p = _mean_bool(out.get("answer_claims_supported") or [])
    r = _mean_bool(out.get("reference_claims_covered") or [])
    if math.isnan(p) or math.isnan(r) or (p + r) == 0:
        return 0.0 if not (math.isnan(p) or math.isnan(r)) else math.nan
    return 2 * p * r / (p + r)


def score_refusal_correctness(judge, *, question: str, answer: str) -> float:
    """否定/不可答题:答案是否为恰当的「拒答/说没有」。仅对 negation 题调用。"""
    user = (
        "判断【答案】是否在恰当地表示『资料中没有/无法回答』(拒答)。\n"
        "只输出 JSON:{\"is_refusal\": true/false}。\n\n"
        f"【问题】\n{question}\n\n【答案】\n{answer}"
    )
    out = judge.judge_json(_FAITH_SYS, user)
    if not out or "is_refusal" not in out:
        return math.nan
    return 1.0 if out["is_refusal"] is True else 0.0
```

```python
# append to backend/scripts/rag_eval/metrics_citation.py  (judge-based; imports math already present)
_CF_SYS = "你是严格的引用核验裁判。只输出 JSON,不要多余文字。"


def score_citation_faithfulness(judge, *, answer: str, cited_texts: list[str]) -> float:
    """逐条:被引片段是否真支撑答案里引用它的那句。score = 被支撑/总数。"""
    if not cited_texts:
        return math.nan
    blocks = "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(cited_texts))
    user = (
        "下列每个被引片段,是否真的支撑【答案】中引用它的论述?逐条给布尔。\n"
        "只输出 JSON:{\"citations\":[{\"supported\":true/false}, ...]}(顺序对应片段)。\n\n"
        f"【答案】\n{answer}\n\n【被引片段】\n{blocks}"
    )
    out = judge.judge_json(_CF_SYS, user)
    if not out:
        return math.nan
    cits = out.get("citations") or []
    if not cits:
        return math.nan
    return sum(1 for c in cits if c.get("supported") is True) / len(cits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_metrics_generation.py tests/test_rag_eval_metrics_citation.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/metrics_generation.py backend/scripts/rag_eval/metrics_citation.py backend/tests/test_rag_eval_metrics_generation.py backend/tests/test_rag_eval_metrics_citation.py
git commit -m "feat(rag-eval): answer correctness (claim F1), refusal, citation faithfulness

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: LLM golden synthesis (sample → generate → map offsets → filter)

**Files:**
- Create: `backend/scripts/rag_eval/synth.py`
- Test: `backend/tests/test_rag_eval_synth.py`

**Interfaces:**
- Consumes: a `gen` object with `judge_json(system, user)->dict|None` (reuse `AnthropicJudge`; fake in tests); `GoldItem`/`GoldSpan` (Plan 1).
- Produces: `is_leaky(question:str, chunk_text:str)->bool` (question quotes ≥ 12 consecutive chars of the chunk verbatim); `is_self_contained(question:str)->bool` (no dangling refs 这段/上文/下文/如图/above/below); `map_support_to_span(doc_text:str, support:str)->tuple[int,int]|None` (`str.find`; None if not found); `synth_item(gen, *, item_id:str, ingest_record_id:int, doc_text:str, chunk_text:str, slices:dict, corpus_version:str)->GoldItem|None` (asks gen for `{"question","reference_answer","support_sentence"}`; maps support to a span **within the chunk's doc**; rejects if any filter fails or span not found).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_synth.py
from scripts.rag_eval.synth import is_leaky, is_self_contained, map_support_to_span, synth_item


class _FakeGen:
    def __init__(self, reply):
        self._reply = reply

    def judge_json(self, system, user):
        return self._reply


def test_filters():
    assert is_self_contained("缓存命中率怎么算?") is True
    assert is_self_contained("这段讲了什么?") is False
    assert is_leaky("请背诵:命中率等于命中次数除以总访问次数", "命中率等于命中次数除以总访问次数啊") is True
    assert is_leaky("缓存命中率怎么算?", "命中率等于命中次数除以总访问次数") is False


def test_map_support_to_span():
    doc = "前言。命中率 = 命中 / 总访问。结语。"
    s = map_support_to_span(doc, "命中率 = 命中 / 总访问")
    assert s is not None and doc[s[0]:s[1]] == "命中率 = 命中 / 总访问"
    assert map_support_to_span(doc, "不存在的句子") is None


def test_synth_item_ok_and_rejects():
    doc = "略。命中率 = 命中 / 总访问。略。"
    good = _FakeGen({"question": "缓存命中率怎么算?", "reference_answer": "命中除以总访问",
                     "support_sentence": "命中率 = 命中 / 总访问"})
    it = synth_item(good, item_id="g100", ingest_record_id=7, doc_text=doc,
                    chunk_text=doc, slices={"lang": "zh"}, corpus_version="v1")
    assert it is not None and it.gold_spans[0].ingest_record_id == 7
    assert doc[it.gold_spans[0].doc_char_start:it.gold_spans[0].doc_char_end] == "命中率 = 命中 / 总访问"

    leaky = _FakeGen({"question": "背:命中率 = 命中 / 总访问", "reference_answer": "x",
                      "support_sentence": "命中率 = 命中 / 总访问"})
    assert synth_item(leaky, item_id="g101", ingest_record_id=7, doc_text=doc,
                      chunk_text=doc, slices={}, corpus_version="v1") is None
    assert synth_item(_FakeGen(None), item_id="g102", ingest_record_id=7, doc_text=doc,
                      chunk_text=doc, slices={}, corpus_version="v1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_synth.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/synth.py
"""LLM golden 合成:采样 chunk → 让模型出题+参考答案+支撑句 → 支撑句映射回文档偏移 = gold 跨度。
自动过滤泄漏/指代/不可定位。生成模型 injectable(默认 AnthropicJudge.judge_json)。"""
from __future__ import annotations

from scripts.rag_eval.golden import GoldItem, GoldSpan

_DANGLING = ("这段", "上文", "下文", "上述", "如图", "如下", "前面", "above", "below", "the passage", "this section")
_SYS = "你是 RAG 评测出题助手。只输出 JSON,不要多余文字。"


def is_self_contained(question: str) -> bool:
    q = (question or "").lower()
    return not any(tok.lower() in q for tok in _DANGLING)


def is_leaky(question: str, chunk_text: str, *, n: int = 12) -> bool:
    """题面逐字抄了原文 ≥ n 个连续字 → 背诵题,泄漏。"""
    q = question or ""
    for i in range(0, max(0, len(q) - n + 1)):
        if q[i:i + n] in chunk_text:
            return True
    return False


def map_support_to_span(doc_text: str, support: str) -> tuple[int, int] | None:
    if not support:
        return None
    idx = doc_text.find(support)
    if idx == -1:
        return None
    return (idx, idx + len(support))


def synth_item(gen, *, item_id: str, ingest_record_id: int, doc_text: str, chunk_text: str,
               slices: dict, corpus_version: str) -> GoldItem | None:
    user = (
        "基于下面这段资料,出一道**只能由它回答**的自然问题,并给参考答案,"
        "再原样抄出资料中**支撑答案的那一句**(必须是资料里的原文子串)。\n"
        "只输出 JSON:{\"question\":\"...\",\"reference_answer\":\"...\",\"support_sentence\":\"...\"}。\n\n"
        f"【资料】\n{chunk_text}"
    )
    out = gen.judge_json(_SYS, user)
    if not out:
        return None
    q = out.get("question", "")
    ref = out.get("reference_answer", "")
    support = out.get("support_sentence", "")
    if not (q and ref and support):
        return None
    if not is_self_contained(q) or is_leaky(q, chunk_text):
        return None
    span = map_support_to_span(doc_text, support)
    if span is None:
        return None
    return GoldItem(
        id=item_id, question=q, gold_spans=(GoldSpan(ingest_record_id, span[0], span[1]),),
        reference_answer=ref, slices=dict(slices), provenance="synthetic",
        source="own", corpus_version=corpus_version,
    )
```

> **Implementer note:** the answerability filter (§5.3 step 4 — re-answer from the chunk and compare to the reference) is an additional LLM round-trip; this task ships the cheap deterministic filters (leakage / self-containment / locatability). Add answerability as a follow-up if the synthesized set proves noisy — record it as a Minor finding rather than blocking this task.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_synth.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/synth.py backend/tests/test_rag_eval_synth.py
git commit -m "feat(rag-eval): LLM golden synthesis + deterministic filters

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: review-golden CLI (accept/edit/reject candidates)

**Files:**
- Create: `backend/scripts/rag_eval/review.py`
- Test: `backend/tests/test_rag_eval_review.py`

**Interfaces:**
- Consumes: `GoldItem` (Plan 1), `save_golden` (Plan 1).
- Produces: `review_candidates(candidates:list[GoldItem], *, prompt_fn, out_path)->list[GoldItem]` — for each candidate, calls `prompt_fn(item)->str` returning `"a"` (accept) / `"r"` (reject) / `"q"` (stop, keep accepted so far); accepted items are saved via `save_golden` to `out_path`; returns the accepted list. (Edit is handled by the human editing the saved file afterward — keep the loop simple; `prompt_fn` injectable so tests script the decisions without stdin.)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_review.py
from scripts.rag_eval.golden import GoldItem, GoldSpan, load_golden
from scripts.rag_eval.review import review_candidates


def _cand(i):
    return GoldItem(f"g{i}", f"q{i}", (GoldSpan(1, 0, 10),), "ref", {"lang": "zh"}, "synthetic", "own", "v1")


def test_accept_reject_then_quit(tmp_path):
    cands = [_cand(1), _cand(2), _cand(3), _cand(4)]
    decisions = iter(["a", "r", "q"])   # accept g1, reject g2, quit before g3
    out = tmp_path / "golden.jsonl"
    kept = review_candidates(cands, prompt_fn=lambda it: next(decisions), out_path=out)
    assert [k.id for k in kept] == ["g1"]
    assert [k.id for k in load_golden(out)] == ["g1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_review.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/review.py
"""人工精修:逐题 accept(a)/reject(r)/quit(q)。prompt_fn injectable(CLI 用 input,测试脚本化)。"""
from __future__ import annotations

from pathlib import Path

from scripts.rag_eval.golden import GoldItem, save_golden


def review_candidates(candidates: list[GoldItem], *, prompt_fn, out_path: str | Path) -> list[GoldItem]:
    kept: list[GoldItem] = []
    for it in candidates:
        choice = (prompt_fn(it) or "").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            kept.append(it)
        # 其它(含 "r")= 跳过
    save_golden(kept, out_path)
    return kept


def stdin_prompt(it: GoldItem) -> str:
    print(f"\n[{it.id}] {it.question}\n  参考: {it.reference_answer}\n  slices: {it.slices}")
    return input("  accept(a)/reject(r)/quit(q)? ")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_review.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/review.py backend/tests/test_rag_eval_review.py
git commit -m "feat(rag-eval): review-golden accept/reject loop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Outer-loop runner (points ② + ③) over the real agent

**Files:**
- Create: `backend/scripts/rag_eval/aggregate.py` (nan-aware aggregation)
- Create: `backend/scripts/rag_eval/runner_generation.py`
- Test: `backend/tests/test_rag_eval_aggregate.py`; `backend/tests/test_rag_eval_runner_generation.py`

**Interfaces:**
- Consumes: real agent primitives `epictrace.agent.tools.build_tools`, `epictrace.agent.react.run_react_loop` + `FALLBACK`, `epictrace.agent.state.ChunkAccumulator`, `epictrace.agent.answer.stream_final_answer`, `epictrace.agent.citations.build_citations`; Plan 1 retrieval metrics; Tasks 3–5 citation/generation scorers; Task 2 cache.
- Produces: `mean_skipnan(vals)->float` and `aggregate(per_q, dims)->dict` in `aggregate.py`; `run_generation(golden, *, build_chat_model, llm, retriever, judge, cache, project_id, config, k_values)->dict` in `runner_generation.py`. `build_chat_model` is a 0-arg factory returning a tool-calling chat model (real DeepSeek `ChatOpenAI` in prod; `FakeChatModel` in tests). Per item it: builds tools, runs `run_react_loop` capturing `pool = accumulator.chunks` (②), runs `stream_final_answer` collecting the `_answer` event (③), derives citations; computes `agent_*` retrieval metrics on the pool, citation metrics, and (judge-cached) generation metrics; `refusal_correctness` is scored **only** when `q_type == "negation"` (else omitted from that item). Returns `{config_hash, n, per_question, by_slice, overall}` with nan-aware means.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_aggregate.py
import math
from scripts.rag_eval.aggregate import aggregate, mean_skipnan


def test_mean_skipnan():
    assert mean_skipnan([1.0, math.nan, 0.0]) == 0.5
    assert math.isnan(mean_skipnan([math.nan, math.nan]))
    assert math.isnan(mean_skipnan([]))


def test_aggregate_by_slice():
    per_q = [{"slices": {"lang": "zh"}, "metrics": {"m": 1.0}},
             {"slices": {"lang": "zh"}, "metrics": {"m": 0.0}},
             {"slices": {"lang": "en"}, "metrics": {"m": math.nan}}]
    agg = aggregate(per_q, dims=("lang",))
    assert agg["overall"]["m"] == 0.5            # nan skipped from en; (1+0)/2
    assert agg["by_slice"]["lang=zh"]["m"] == 0.5
    assert math.isnan(agg["by_slice"]["lang=en"]["m"])
```

```python
# backend/tests/test_rag_eval_runner_generation.py
"""端到端注入:假 chat_model + 假 llm + 假 judge + 假 retriever,证明 ②③ 全链路通,不碰真模型。"""
from collections import namedtuple

from scripts.rag_eval.config import EvalConfig
from scripts.rag_eval.golden import GoldItem, GoldSpan
from scripts.rag_eval.runner_generation import run_generation

C = namedtuple("C", "text ingest_record_id project_id char_start char_end source_type score source_kind reference_id")


def _chunk(rid, a, b, text="t"):
    return C(text, rid, 1, a, b, "folder_scan", 1.0, "project", None)


class _FakeJudge:
    def judge_json(self, system, user):
        return {"claims": [{"text": "c", "supported": True}], "relevancy": 1.0,
                "answer_claims_supported": [True], "reference_claims_covered": [True],
                "is_refusal": True, "citations": [{"supported": True}]}


def test_run_generation_smoke(tmp_path, monkeypatch):
    import scripts.rag_eval.runner_generation as rg

    # 桩掉真 agent 原语:run_react_loop 往 accumulator 塞一个命中 gold 的 chunk;
    # stream_final_answer 吐一个带 [1] 引用的答案。
    def fake_loop(chat_model, tools, accumulator, question, **kw):
        accumulator.chunks.append(_chunk(1, 0, 50))
        return "ok"

    def fake_stream(llm, question, pool, **kw):
        yield {"event": "token", "data": "答案 [1]"}
        yield {"event": "_answer", "data": "答案 [1]"}

    monkeypatch.setattr(rg, "run_react_loop", fake_loop)
    monkeypatch.setattr(rg, "stream_final_answer", fake_stream)
    monkeypatch.setattr(rg, "build_tools", lambda **k: [])

    class _Acc:
        def __init__(self): self.chunks = []
    monkeypatch.setattr(rg, "ChunkAccumulator", _Acc)

    golden = [GoldItem("g1", "q1", (GoldSpan(1, 0, 50),), "ref",
                       {"lang": "zh", "q_type": "single_hop"}, "synthetic", "own", "v1")]
    res = run_generation(golden, build_chat_model=lambda: object(), llm=object(),
                         retriever=object(), judge=_FakeJudge(), cache=None,
                         project_id=1, config=EvalConfig(k=6, k_values=(5,)))
    m = res["per_question"][0]["metrics"]
    assert m["agent_recall_any@5"] == 1.0       # pool 命中 gold(②)
    assert m["citation_accuracy"] == 1.0        # [1] 指向命中 chunk
    assert m["faithfulness"] == 1.0             # judge
    assert "refusal_correctness" not in m       # single_hop 不算 refusal
    assert res["overall"]["faithfulness"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_aggregate.py tests/test_rag_eval_runner_generation.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/aggregate.py
"""nan-aware 聚合(judge 失败 = nan,均值跳过 nan;全 nan → nan)。"""
from __future__ import annotations

import math


def mean_skipnan(vals) -> float:
    good = [v for v in vals if not (isinstance(v, float) and math.isnan(v))]
    return sum(good) / len(good) if good else math.nan


def _keys(per_q) -> list[str]:
    ks: list[str] = []
    for r in per_q:
        for k in r["metrics"]:
            if k not in ks:
                ks.append(k)
    return ks


def aggregate(per_q, dims=("domain", "doc_type", "lang", "q_type")) -> dict:
    keys = _keys(per_q)
    overall = {k: mean_skipnan([r["metrics"].get(k, math.nan) for r in per_q]) for k in keys}
    by_slice: dict = {}
    for dim in dims:
        groups: dict = {}
        for r in per_q:
            v = r["slices"].get(dim)
            if v is not None:
                groups.setdefault(f"{dim}={v}", []).append(r)
        for name, rows in groups.items():
            by_slice[name] = {k: mean_skipnan([r["metrics"].get(k, math.nan) for r in rows]) for k in keys}
    return {"overall": overall, "by_slice": by_slice}
```

```python
# backend/scripts/rag_eval/runner_generation.py
"""外循环 runner(测量点 ②③):复用真 agent 原语跑被测生成器,judge 算生成/引用质量。
组件全注入 → 烟测用 FakeChatModel + 假 judge,不碰真模型/网络。"""
from __future__ import annotations

import math

from epictrace.agent.answer import stream_final_answer
from epictrace.agent.citations import build_citations
from epictrace.agent.react import FALLBACK, run_react_loop
from epictrace.agent.state import ChunkAccumulator
from epictrace.agent.tools import build_tools

from scripts.rag_eval.aggregate import aggregate
from scripts.rag_eval.judge_cache import cache_key
from scripts.rag_eval.metrics import (
    context_precision_ordered_at_k, mrr, ndcg_at_k, recall_any_at_k, recall_coverage_at_k,
)
from scripts.rag_eval.metrics_citation import (
    citation_accuracy, citation_validity, parse_citation_ids, score_citation_faithfulness,
)
from scripts.rag_eval.metrics_generation import (
    score_answer_correctness, score_answer_relevancy, score_faithfulness, score_refusal_correctness,
)


def _cached(cache, judge_model, metric, qid, answer, context, fn):
    """judge 评分缓存包装:命中读盘,未命中算了再写(只缓存非 nan)。cache=None → 不缓存。"""
    if cache is None:
        return fn()
    k = cache_key(metric, qid, answer, context, judge_model)
    hit = cache.get(k)
    if hit is not None:
        return hit.get("v", math.nan)
    val = fn()
    if not (isinstance(val, float) and math.isnan(val)):
        cache.put(k, {"v": val})
    return val


def _run_one(it, *, build_chat_model, llm, retriever, judge, cache, judge_model, project_id, config):
    acc = ChunkAccumulator()
    tools = build_tools(retriever=retriever, project_id=project_id, focus_ids=[],
                        attachment_retriever=None, conversation_id=0, indexed_ext_ids=[],
                        reference_texts={}, fulltext_ids=[])
    status = run_react_loop(build_chat_model(), tools, acc, it.question, history=[], attachment_manifest="")
    pool = list(acc.chunks)
    answer = ""
    for ev in stream_final_answer(llm, it.question, pool, history=[], attached_names=[]):
        if ev.get("event") == "_answer":
            answer = ev["data"]
    context = "\n\n".join(getattr(c, "text", "") for c in pool)
    cited_texts = [pool[n - 1].text for n in parse_citation_ids(answer) if 1 <= n <= len(pool)]

    m: dict = {"agent_fallback": 1.0 if status == FALLBACK else 0.0}
    for k in config.k_values:
        m[f"agent_recall_any@{k}"] = recall_any_at_k(pool, it.gold_spans, k)
        m[f"agent_recall_cov@{k}"] = recall_coverage_at_k(pool, it.gold_spans, k)
        m[f"agent_ndcg@{k}"] = ndcg_at_k(pool, it.gold_spans, k)
        m[f"agent_ctxp_ord@{k}"] = context_precision_ordered_at_k(pool, it.gold_spans, k)
    m["agent_mrr"] = mrr(pool, it.gold_spans)
    m["citation_validity"] = citation_validity(answer, len(pool))
    m["citation_accuracy"] = citation_accuracy(answer, pool, it.gold_spans)
    m["citation_faithfulness"] = _cached(
        cache, judge_model, "citation_faithfulness", it.id, answer, context,
        lambda: score_citation_faithfulness(judge, answer=answer, cited_texts=cited_texts))
    m["faithfulness"] = _cached(cache, judge_model, "faithfulness", it.id, answer, context,
                                lambda: score_faithfulness(judge, answer=answer, context=context))
    m["answer_relevancy"] = _cached(cache, judge_model, "answer_relevancy", it.id, answer, "",
                                    lambda: score_answer_relevancy(judge, question=it.question, answer=answer))
    m["answer_correctness"] = _cached(
        cache, judge_model, "answer_correctness", it.id, answer, it.reference_answer,
        lambda: score_answer_correctness(judge, question=it.question, answer=answer, reference=it.reference_answer))
    if it.slices.get("q_type") == "negation":
        m["refusal_correctness"] = _cached(cache, judge_model, "refusal_correctness", it.id, answer, "",
                                           lambda: score_refusal_correctness(judge, question=it.question, answer=answer))
    return {"id": it.id, "slices": it.slices, "metrics": m, "answer": answer}


def run_generation(golden, *, build_chat_model, llm, retriever, judge, cache,
                   project_id: int, config) -> dict:
    judge_model = getattr(getattr(judge, "_cfg", None), "model", "judge")
    per_q = [_run_one(it, build_chat_model=build_chat_model, llm=llm, retriever=retriever,
                      judge=judge, cache=cache, judge_model=judge_model,
                      project_id=project_id, config=config) for it in golden]
    agg = aggregate(per_q)
    return {"config_hash": config.config_hash(), "n": len(per_q), "per_question": per_q,
            "by_slice": agg["by_slice"], "overall": agg["overall"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_aggregate.py tests/test_rag_eval_runner_generation.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/aggregate.py backend/scripts/rag_eval/runner_generation.py backend/tests/test_rag_eval_aggregate.py backend/tests/test_rag_eval_runner_generation.py
git commit -m "feat(rag-eval): outer-loop runner (points 2+3) + nan-aware aggregation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Cohen's-kappa calibration + generation report core

**Files:**
- Create: `backend/scripts/rag_eval/calibration.py`
- Modify: `backend/scripts/rag_eval/report.py` (add `_GEN_CORE` + keep `format_report`/`diff_runs` generic)
- Test: `backend/tests/test_rag_eval_calibration.py`; `backend/tests/test_rag_eval_report_gen.py`

**Interfaces:**
- Produces: `cohen_kappa(a:list, b:list)->float` (categorical agreement; `nan` if undefined); `calibrate(judge_labels:list, human_labels:list)->dict` (`{"kappa","n","agreement"}`); `GEN_CORE` list exported from `report.py` (`["faithfulness","answer_relevancy","answer_correctness","citation_accuracy","citation_faithfulness","agent_recall_any@5"]`) for convenient generation-run reporting.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_calibration.py
import math
from scripts.rag_eval.calibration import calibrate, cohen_kappa


def test_kappa_perfect_and_chance():
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    # 完全相反 → kappa 为负。
    assert cohen_kappa([1, 1, 0, 0], [0, 0, 1, 1]) < 0
    assert math.isnan(cohen_kappa([], []))


def test_calibrate_reports():
    out = calibrate([1, 0, 1, 1], [1, 0, 0, 1])
    assert out["n"] == 4 and out["agreement"] == 0.75
    assert -1.0 <= out["kappa"] <= 1.0
```

```python
# backend/tests/test_rag_eval_report_gen.py
from scripts.rag_eval.report import GEN_CORE, format_report


def test_gen_core_report():
    summary = {"config_hash": "g", "n": 1,
               "overall": {"faithfulness": 0.9, "citation_accuracy": 0.8},
               "by_slice": {"lang=zh": {"faithfulness": 0.7, "citation_accuracy": 0.6}}}
    out = format_report(summary, metrics=["faithfulness", "citation_accuracy"])
    assert "faithfulness" in out and "lang=zh" in out
    assert "faithfulness" in GEN_CORE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_calibration.py tests/test_rag_eval_report_gen.py -q`
Expected: FAIL (`ModuleNotFoundError` / `ImportError: GEN_CORE`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/rag_eval/calibration.py
"""judge 可信前提:judge 裁决 vs 人工标注的一致性。Cohen's kappa(达标才采信 judge)。"""
from __future__ import annotations

import math
from collections import Counter


def cohen_kappa(a: list, b: list) -> float:
    n = len(a)
    if n == 0 or n != len(b):
        return math.nan
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe == 1.0:
        return 1.0 if po == 1.0 else math.nan
    return (po - pe) / (1 - pe)


def calibrate(judge_labels: list, human_labels: list) -> dict:
    n = len(judge_labels)
    agreement = (sum(1 for x, y in zip(judge_labels, human_labels) if x == y) / n) if n else math.nan
    return {"kappa": cohen_kappa(judge_labels, human_labels), "n": n, "agreement": agreement}
```

```python
# append to backend/scripts/rag_eval/report.py
GEN_CORE = ["faithfulness", "answer_relevancy", "answer_correctness",
            "citation_accuracy", "citation_faithfulness", "agent_recall_any@5"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_calibration.py tests/test_rag_eval_report_gen.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/calibration.py backend/scripts/rag_eval/report.py backend/tests/test_rag_eval_calibration.py backend/tests/test_rag_eval_report_gen.py
git commit -m "feat(rag-eval): Cohen's-kappa calibration + generation report core

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: CLI wiring (`gen-golden` / `review-golden` / `run`) + judge/gen wiring + full-suite check

**Files:**
- Modify: `backend/scripts/rag_eval/cli.py` (add `gen-golden`, `review-golden`, `run` subcommands)
- Modify: `backend/scripts/rag_eval/wiring.py` (add `build_judge()`, `build_chat_model_factory()`, `build_llm()` — lazy)
- Test: `backend/tests/test_rag_eval_cli_gen.py`

**Interfaces:**
- Consumes: Tasks 1–9. `wiring.build_judge()->AnthropicJudge` (lazy `load_judge_config`); `wiring.build_chat_model_factory()` + `wiring.build_llm()` (lazy — real DeepSeek `ChatOpenAI`/`OpenAICompatLLM` from settings; mirror `api/deps.get_chat_model_factory`). `run` subcommand: load golden, build retriever+judge+chat_model+llm+cache, call `run_generation`, `write_run`, print `format_report(..., metrics=GEN_CORE)`.
- Produces: `run`/`gen-golden`/`review-golden` subcommands in `main`. CLI tests cover only argument routing for the new subcommands via monkeypatched helpers (no heavy wiring touched), mirroring Plan 1 Task 9's approach.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rag_eval_cli_gen.py
import json

from scripts.rag_eval import cli


def test_run_subcommand_routes(tmp_path, monkeypatch):
    golden = tmp_path / "g.jsonl"
    golden.write_text(json.dumps({"id": "g1", "question": "q", "gold_spans": [],
                                  "reference_answer": "", "slices": {}, "provenance": "hand",
                                  "source": "own", "corpus_version": "v1"}) + "\n", encoding="utf-8")
    called = {}
    # 桩掉重组件装配 + 真跑,只验证路由 + 产物落盘。
    monkeypatch.setattr(cli, "_RUNS", tmp_path / "runs")
    import scripts.rag_eval.wiring as wiring
    monkeypatch.setattr(wiring, "build_retriever", lambda pid: object())
    monkeypatch.setattr(wiring, "build_judge", lambda: object())
    monkeypatch.setattr(wiring, "build_chat_model_factory", lambda: (lambda: object()))
    monkeypatch.setattr(wiring, "build_llm", lambda: object())

    def fake_run_generation(golden_items, **kw):
        called["n"] = len(golden_items)
        return {"config_hash": "abc", "n": len(golden_items), "per_question": [],
                "by_slice": {}, "overall": {"faithfulness": 1.0}}
    monkeypatch.setattr(cli, "run_generation", fake_run_generation)

    rc = cli.main(["run", "--golden", str(golden), "--project-id", "1"])
    assert rc == 0 and called["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_cli_gen.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'run_generation'` / no `run` subcommand)

- [ ] **Step 3: Write minimal implementation**

Add to `backend/scripts/rag_eval/wiring.py`:

```python
def build_judge():
    from scripts.rag_eval.judge_client import AnthropicJudge, load_judge_config
    return AnthropicJudge(load_judge_config())


def build_chat_model_factory():
    # 复用产品的 chat_model 工厂(真 DeepSeek，工具调用路）。镜像 api/deps.get_chat_model_factory。
    from epictrace.api.deps import _build_chat_model_factory  # 若私有名不同,grep deps 对齐
    return _build_chat_model_factory()


def build_llm():
    from epictrace.api.deps import _build_llm  # 同上,对齐真实构造名
    return _build_llm()
```

> **Implementer note:** grep `api/deps.py` for the real chat-model-factory / llm constructors (Plan 1 referenced `get_chat_model_factory`); they take a `request`/app-state. If they require a FastAPI `request`, build the minimal app state the same way `deps` does (or construct `OpenAICompatLLM` + `ChatOpenAI` directly from `SettingsService` like `deps` does). Match the real construction; do not invent. This wiring is **manual-run only**, never unit-tested.

Add to `backend/scripts/rag_eval/cli.py` (imports + subcommands):

```python
from scripts.rag_eval.report import GEN_CORE
from scripts.rag_eval.runner_generation import run_generation


def _cmd_run(ns) -> int:
    from scripts.rag_eval import wiring
    from scripts.rag_eval.judge_cache import JudgeCache
    golden = load_golden(ns.golden)
    cfg = EvalConfig(k=ns.k, dense_n=ns.dense_n, fuse_m=ns.fuse_m, label=ns.label or "")
    cache = JudgeCache(_RUNS / "judge_cache.jsonl")
    res = run_generation(golden, build_chat_model=wiring.build_chat_model_factory(),
                         llm=wiring.build_llm(), retriever=wiring.build_retriever(ns.project_id),
                         judge=wiring.build_judge(), cache=cache, project_id=ns.project_id, config=cfg)
    out = write_run(res, _RUNS)
    print(format_report({k: res[k] for k in ("config_hash", "n", "by_slice", "overall")}, metrics=GEN_CORE))
    print(f"\n[rag-eval] run written to {out}", file=sys.stderr)
    return 0


def _cmd_gen_golden(ns) -> int:
    from scripts.rag_eval import wiring
    from scripts.rag_eval.synth import synth_item   # 采样→合成由 manual 脚本/此命令组织
    raise SystemExit("gen-golden: 见 plan 手动 bring-up——本命令组织 采样+synth_item;按真实抽取文本接线")


def _cmd_review_golden(ns) -> int:
    from scripts.rag_eval.golden import load_golden as _lg
    from scripts.rag_eval.review import review_candidates, stdin_prompt
    kept = review_candidates(_lg(ns.candidates), prompt_fn=stdin_prompt, out_path=ns.out)
    print(f"[rag-eval] kept {len(kept)} items → {ns.out}", file=sys.stderr)
    return 0
```

Register the three subparsers inside `main` (alongside Plan 1's): `run` (args `--golden`, `--project-id`, `--k`, `--dense-n`, `--fuse-m`, `--label`), `review-golden` (`--candidates`, `--out`), `gen-golden` (`--out`, plus corpus/index args as the implementer wires synthesis). Use the same `set_defaults(fn=...)` pattern as Plan 1.

> **Implementer note:** `gen-golden` orchestrates Task 6's `synth_item` over sampled chunks from the indexed eval corpus — it needs the extracted `doc_text` per `ingest_record_id` (read from the store / re-extract). Wire it against the real store the same way `indexing.py` does; it is manual-run only. The stub above must be replaced with the real orchestration, OR (acceptable for this task) `gen-golden` may be deferred to the manual bring-up with a clear `SystemExit` message — but `run` and `review-golden` must be fully wired and the routing test must pass.

- [ ] **Step 4: Run the new test + full suite**

Run: `cd backend && ./.venv/bin/pytest tests/test_rag_eval_cli_gen.py -q` → PASS.
Then `./.venv/bin/pytest -q` → confirm no regressions and no rag_eval test imported a real model (heavy imports are confined to `wiring.py`, which tests monkeypatch).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/rag_eval/cli.py backend/scripts/rag_eval/wiring.py backend/tests/test_rag_eval_cli_gen.py
git commit -m "feat(rag-eval): CLI run/gen-golden/review-golden + judge/gen wiring

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Manual bring-up (run once after Task 10, on William's Mac)

Builds on Plan 1's bring-up (corpus already built + indexed). Needs the real DeepSeek BYOK profile (generator) + the `temp_key` Claude judge + network.

1. `export RAG_EVAL_JUDGE_KEY=...` and `RAG_EVAL_JUDGE_BASE_URL=https://api-slb.krill-ai.com` (or rely on `~/Desktop/temp_key`). Confirm `python -c "from scripts.rag_eval.judge_client import load_judge_config; print(load_judge_config().model)"` prints `claude-opus-4-8`.
2. **Generate golden at scale**: `gen-golden` (or a short script) samples stratified chunks from the indexed eval corpus and runs `synth_item` → candidate `golden.jsonl`. Then `review-golden --candidates candidates.jsonl --out tests/fixtures/rag_eval/golden.jsonl` to cull to a trusted set (~60–70% keep). Add the ~10 hand multi-hop/negation items from Plan 1 Task 6.
3. **Calibrate the judge**: hand-label 30–50 faithfulness/correctness verdicts, run `calibrate(judge_labels, human_labels)`; adopt the judge only if Cohen's kappa clears the bar (≈≥0.6). Record the number in the run report.
4. **Baseline `run`**: `./.venv/bin/python -m scripts.rag_eval.cli run --golden tests/fixtures/rag_eval/golden.jsonl --project-id <pid> --label baseline` → generation + citation + ② retrieval report, judge-cached.
5. Sweep retrieval/prompt params and `diff` runs (Plan 1's `diff`) — now the deltas cover answer quality + citations, not just retrieval. This quantifies §11's weaknesses against the slice report.

## Self-Review

**Spec coverage (`2026-06-21-rag-eval-design.md`):**
- §5.3 synthesis (steps 3–4) → Task 6 (`synth_item` + filters; answerability noted as follow-up). §5.5 review CLI → Task 7. ✓
- §6.B citation (validity/accuracy deterministic, faithfulness judge) → Tasks 3, 5. ✓
- §6.C generation (faithfulness/relevancy/correctness/refusal) → Tasks 4, 5. ✓
- §6.D judge infra (Anthropic Messages, fence-strip, NaN-not-0, no-temperature, cache, key-secret, judge≠generator) → Tasks 1, 2, 8 + Global Constraints. ✓ Kappa calibration → Task 9. Optional DeepSeek second-judge → noted out of scope (a follow-up flag on the runner).
- §3 measurement points ②③ + two-tier outer loop → Task 8 (`run_generation` reuses real agent primitives; ② = `accumulator.chunks`, ③ = answer+citations). ✓
- §7 `run` outer loop + judge cache → Tasks 8, 10. §8 report/diff for generation → Task 9 (`GEN_CORE`; `format_report`/`diff_runs` already generic from Plan 1). ✓
- §10 self-tests (fake judge, FakeChatModel, fake transport, no real model) → every task's tests. ✓

**Placeholder scan:** No vague placeholders. Three implementer notes (synthesis answerability follow-up; `wiring` constructor-name verification against `api/deps`; `gen-golden` orchestration may defer to bring-up) are explicit, bounded decisions — not "TODO later". `gen-golden`'s stub is allowed to `SystemExit` only if `run`+`review-golden` are fully wired and tested.

**Type consistency:** judge interface `judge_json(system,user)->dict|None` used uniformly by all scorers (Tasks 1,4,5,6) and the runner (Task 8); `mean_skipnan`/`aggregate` (Task 8) consumed by `run_generation`; metric keys produced in Task 8 (`agent_*`, `faithfulness`, `answer_relevancy`, `answer_correctness`, `refusal_correctness`, `citation_*`) are exactly what `GEN_CORE` (Task 9) and the report select; `GoldItem`/`GoldSpan`/`save_golden`/`load_golden`/`chunk_hits`/metric fns reused from Plan 1 unchanged.

**Cross-plan note:** Plan 1's `report.format_report`/`diff_runs` are metric-generic, so they serve generation metrics without change (Task 9 only adds `GEN_CORE`). The only Plan 1 file modified here is `report.py` (additive).

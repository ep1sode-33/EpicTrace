# Plan 7: MinerU High-Quality Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pypdf PDF processor with a MinerU (hybrid-engine, effort=high) subprocess backend behind the existing `MediaProcessor` seam — including in-app `uv` provisioning of an isolated `.MinerU-venv`, no-fallback error semantics, and a content_list provenance sidecar — so downstream chunking/index/citation gets better text with zero changes to Plan 5/6.

**Architecture:** `get_processor(path, config)` returns a `MinerUMediaProcessor` for `.pdf` (built from `config` with a `MinerUProvisioner` over `<data_dir>/.MinerU-venv`). When ready, `process()` shells out to `<.MinerU-venv>/bin/mineru` in a separate subprocess (structurally avoiding the macOS gRPC-fork segfault), parses `<stem>.md` + `<stem>_content_list.json`, and returns a `MediaResult`; when not ready it raises `ExtractionEngineNotReady`, and any subprocess failure/timeout/missing-output raises `ExtractionFailed` (no pypdf fallback). The four extraction call sites (ingest/references/index/source) thread `config` via `self._db.config` and the ingest + reference call sites persist a `content_list` provenance sidecar. New `extraction/status` + `extraction/provision` API endpoints drive a minimal "高质量提取" settings section.

**Tech Stack:** Python 3.11 (FastAPI backend, venv at `backend/.venv`), MinerU CLI (`mineru[all]`) provisioned via `uv` into `<data_dir>/.MinerU-venv`, pytest with injected fake subprocess/uv runners (no real mineru/uv/network), React + shadcn/ui + Tailwind frontend.

---

## File Structure

### Backend — Created

| File | Responsibility |
| --- | --- |
| `backend/epictrace/media/errors.py` | `ExtractionEngineNotReady` + `ExtractionFailed` exceptions (media layer). |
| `backend/epictrace/media/mineru_runner.py` | `run_mineru(...)` — build the `mineru` command, run it (injectable runner callable) with timeout, read `<stem>.md` + `<stem>_content_list.json`, return `(markdown, content_list)`; failure/timeout/missing/empty → `ExtractionFailed`. |
| `backend/epictrace/media/mineru_provisioner.py` | `MinerUProvisioner` — manage `<data_dir>/.MinerU-venv`: `is_ready()`, `provision(progress_cb)`, `mineru_bin()`, `uv_bin()`, `state` machine (not_installed/installing/ready/failed); uv invocation injectable. |
| `backend/epictrace/media/mineru.py` | `MinerUMediaProcessor(MediaProcessor)` — `.pdf` always; `process()` → not ready raises `ExtractionEngineNotReady`, ready calls injected runner → `MediaResult`, runner failure propagates `ExtractionFailed`. |
| `backend/epictrace/media/provenance.py` | `write_provenance(data_dir, kind, item_id, content_list)` — write `<data_dir>/provenance/<kind>-<id>.json`. |

### Backend — Modified

| File | Responsibility |
| --- | --- |
| `backend/epictrace/media/__init__.py` | `get_processor(path, config)` signature; build PDF slot as `MinerUMediaProcessor` from `config`; remove `PdfMediaProcessor` from `_PROCESSORS` (keep `pdf.py`). |
| `backend/epictrace/config.py` | Add `model_source` (default `"modelscope"`) and `extraction_timeout` (default `600`) to `AppConfig`; `mineru_venv_dir` + `provenance_dir` properties. |
| `backend/epictrace/services/ingest.py` | `get_processor(dest, self._db.config)`; on `content_list` in metadata, write `ingest` provenance after flush. |
| `backend/epictrace/services/references.py` | `get_processor(p, self._db.config)` (x2); write `reference` provenance for external attachments carrying `content_list`. |
| `backend/epictrace/services/index.py` | `get_processor(Path(...), self._db.config)` (x2). |
| `backend/epictrace/services/source.py` | `get_processor(path, self._db.config)` (x2). |
| `backend/epictrace/services/settings.py` | `extraction_status()` accessor returning `{state, ready}` from a `MinerUProvisioner`. |
| `backend/epictrace/api/routers/settings.py` | `GET /extraction/status` + `POST /extraction/provision` endpoints. |
| `backend/epictrace/api/deps.py` | `get_provisioner(request)` helper (lazily build/cache `MinerUProvisioner` on `app.state`). |
| `backend/epictrace/schemas.py` | `ExtractionStatusOut` schema. |
| `backend/pyproject.toml` | (No new runtime dep — MinerU lives in `.MinerU-venv`, not the core env. Add a comment documenting that.) |

### Backend — Tests (Created)

| File | Responsibility |
| --- | --- |
| `backend/tests/test_media_errors.py` | The two exceptions exist, subclass `Exception`, carry a message. |
| `backend/tests/test_mineru_runner.py` | Command assembly + output parsing via fake subprocess runner; non-zero/timeout/missing/empty → `ExtractionFailed`. |
| `backend/tests/test_mineru_provisioner.py` | Fake-uv command assembly (`uv venv` / `uv pip install`), state machine, `is_ready()` probe. |
| `backend/tests/test_mineru_processor.py` | `supports(.pdf)`; not-ready → `ExtractionEngineNotReady`; ready → `MediaResult` text/metadata; runner failure → `ExtractionFailed` (no pypdf text). |
| `backend/tests/test_media_provenance.py` | `write_provenance` path + content. |
| `backend/tests/test_extraction_api.py` | `GET /extraction/status` + `POST /extraction/provision` (fake provisioner). |
| `backend/tests/test_mineru_slow.py` | Opt-in (`EPICTRACE_RUN_SLOW=1`) real-mineru test; skips unless `.MinerU-venv` provisioned. |

### Backend — Tests (Modified)

| File | Responsibility |
| --- | --- |
| `backend/tests/test_media_docs.py` | `get_processor(p, config)`; assert PDF → `MinerUMediaProcessor`, not pypdf. |
| `backend/tests/test_media_text.py` | `get_processor(f, config)` two-arg calls. |
| `backend/tests/test_ingest_service.py` | Monkeypatch `get_processor` with 2-arg lambda; add provenance-persisted-on-pdf test. |
| `backend/tests/test_index_service.py` | `boom(p, config)` 2-arg monkeypatch. |
| `backend/tests/test_references_service.py` | (No signature change needed — uses real text files; verify still green.) |
| `backend/tests/test_source_service.py` | (No signature change needed — verify still green.) |

### Frontend — Modified

| File | Responsibility |
| --- | --- |
| `frontend/src/lib/api.ts` | `ExtractionStatus` interface + `getExtractionStatus()` / `provisionExtraction()` calls. |
| `frontend/src/views/SettingsView.tsx` | A minimal "高质量提取" `<section>`: status badge (未安装/安装中/就绪/失败), install button → provision, poll status. |

---

## Task 1: Media-layer exceptions

**Files:**
- Create `backend/epictrace/media/errors.py`
- Create `backend/tests/test_media_errors.py`

Steps:

- [ ] Write failing test `backend/tests/test_media_errors.py`:
  ```python
  import pytest

  from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed


  def test_exceptions_are_exceptions_with_message():
      assert issubclass(ExtractionEngineNotReady, Exception)
      assert issubclass(ExtractionFailed, Exception)
      assert str(ExtractionEngineNotReady("not ready")) == "not ready"
      assert str(ExtractionFailed("boom")) == "boom"


  def test_exceptions_are_distinct():
      with pytest.raises(ExtractionEngineNotReady):
          raise ExtractionEngineNotReady("x")
      with pytest.raises(ExtractionFailed):
          raise ExtractionFailed("y")
      assert ExtractionEngineNotReady is not ExtractionFailed
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_media_errors.py` from `/Users/william/Desktop/EpicTrace/backend`. Expect FAIL (ModuleNotFoundError: `epictrace.media.errors`).
- [ ] Create `backend/epictrace/media/errors.py`:
  ```python
  from __future__ import annotations


  class ExtractionEngineNotReady(Exception):
      """PDF 提取引擎(MinerU)尚未 provision/就绪。调用方应提示用户先安装高质量提取引擎。"""


  class ExtractionFailed(Exception):
      """MinerU 子进程失败/超时/缺输出/空文本。无回退——调用方按既有失败路径呈现。"""
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_media_errors.py`. Expect PASS (2 passed).
- [ ] Commit:
  ```
  git add backend/epictrace/media/errors.py backend/tests/test_media_errors.py && git commit -m "$(cat <<'EOF'
  Add media-layer extraction exceptions

  ExtractionEngineNotReady / ExtractionFailed for the MinerU PDF backend
  (no-fallback semantics; callers map to existing failure paths).

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2: `get_processor(path, config)` signature change + thread config through all call sites

> **Integration-risk task.** `get_processor` is called in 5 places across 4 services: `ingest.py` (1), `references.py` (2), `index.py` (2), `source.py` (2). All services already hold `self._db`, and `Database` exposes a `.config` property (`backend/epictrace/db.py`), so the threading is `get_processor(path, self._db.config)` — **no service constructor or router changes**. The PDF slot still returns the current pypdf processor for now (it is swapped to MinerU in Task 6); this task is purely the signature/threading change so the suite stays green between tasks.

**Files:**
- Modify `backend/epictrace/media/__init__.py`
- Modify `backend/epictrace/config.py` (add `provenance_dir` + `mineru_venv_dir` properties now so later tasks reuse; `model_source`/`extraction_timeout` come in Task 8)
- Modify `backend/epictrace/services/ingest.py`
- Modify `backend/epictrace/services/references.py`
- Modify `backend/epictrace/services/index.py`
- Modify `backend/epictrace/services/source.py`
- Modify `backend/tests/test_media_docs.py`
- Modify `backend/tests/test_media_text.py`
- Modify `backend/tests/test_ingest_service.py`
- Modify `backend/tests/test_index_service.py`

Steps:

- [ ] Update test `backend/tests/test_media_text.py` to the new two-arg signature. Replace the three `get_processor(...)` call lines:
  - Add `from epictrace.config import AppConfig` to imports.
  - `proc = get_processor(f)` → `proc = get_processor(f, AppConfig(data_dir=tmp_path))`
  - `proc = get_processor(f)` (in the loop) → `proc = get_processor(f, AppConfig(data_dir=tmp_path))`
  - `assert get_processor(tmp_path / "a.png") is None` → `assert get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path)) is None`
- [ ] Update test `backend/tests/test_media_docs.py`: add `from epictrace.config import AppConfig`, and change every `get_processor(p)` / `get_processor(tmp_path / "...")` to pass `AppConfig(data_dir=tmp_path)` as the second arg. For the PDF test, keep `assert proc is not None` and `assert "Hello PDF" in proc.process(p).text` for now (Task 6 changes this to assert no pypdf text). Concretely:
  - `proc = get_processor(p)` (docx) → `proc = get_processor(p, AppConfig(data_dir=tmp_path))`
  - `proc = get_processor(p)` (pptx) → `proc = get_processor(p, AppConfig(data_dir=tmp_path))`
  - `proc = get_processor(p)` (pdf) → `proc = get_processor(p, AppConfig(data_dir=tmp_path))`
  - both unknown-type asserts → `get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path))` / `get_processor(tmp_path / "a.mp3", AppConfig(data_dir=tmp_path))`
- [ ] Update test `backend/tests/test_ingest_service.py` monkeypatch (line 126) to a 2-arg lambda:
  - `monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda _: _BadProc())` → `monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda _path, _config: _BadProc())`
- [ ] Update test `backend/tests/test_index_service.py` monkeypatch (lines 59-66) to a 2-arg signature:
  - `def boom(p):` → `def boom(p, config):`
  - inside, `return real(p)` → `return real(p, config)`
- [ ] Run `./.venv/bin/pytest -q tests/test_media_text.py tests/test_media_docs.py tests/test_ingest_service.py tests/test_index_service.py` from `/Users/william/Desktop/EpicTrace/backend`. Expect FAIL (current `get_processor` takes one positional arg → TypeError).
- [ ] Add config properties to `backend/epictrace/config.py` (inside `AppConfig`, after `attachment_milvus_path`):
  ```python
      @property
      def mineru_venv_dir(self) -> Path:
          return self.data_dir / ".MinerU-venv"

      @property
      def provenance_dir(self) -> Path:
          return self.data_dir / "provenance"
  ```
- [ ] Rewrite `backend/epictrace/media/__init__.py` to the new signature (PDF slot still pypdf for this task; MinerU swap is Task 6):
  ```python
  from __future__ import annotations

  from pathlib import Path

  from epictrace.config import AppConfig
  from epictrace.interfaces.media import MediaProcessor
  from epictrace.media.text import TextMediaProcessor
  from epictrace.media.pdf import PdfMediaProcessor
  from epictrace.media.docx import DocxMediaProcessor
  from epictrace.media.pptx import PptxMediaProcessor

  # 非 PDF 的静态处理器(无需 config)。PDF 槽由 config 构造(见 _pdf_processor)。
  _STATIC_PROCESSORS: list[MediaProcessor] = [
      TextMediaProcessor(),
      DocxMediaProcessor(),
      PptxMediaProcessor(),
  ]


  def _pdf_processor(config: AppConfig) -> MediaProcessor:
      # Task 6 起改为从 config 构造 MinerUMediaProcessor;暂时仍用 pypdf 以保持套件绿。
      return PdfMediaProcessor()


  def get_processor(path: Path, config: AppConfig) -> MediaProcessor | None:
      for proc in _STATIC_PROCESSORS:
          if proc.supports(path):
              return proc
      pdf = _pdf_processor(config)
      if pdf.supports(path):
          return pdf
      return None
  ```
- [ ] Edit `backend/epictrace/services/ingest.py` line 69: `proc = get_processor(dest)` → `proc = get_processor(dest, self._db.config)`.
- [ ] Edit `backend/epictrace/services/references.py`:
  - line 42 (`add_external`): `proc = get_processor(p)` → `proc = get_processor(p, self._db.config)`
  - line 82 (`add_internal`): `proc = get_processor(path)` → `proc = get_processor(path, self._db.config)`
- [ ] Edit `backend/epictrace/services/index.py`:
  - line 57: `if get_processor(Path(r.stored_path)) is not None` → `if get_processor(Path(r.stored_path), self._db.config) is not None`
  - line 78 (in `_run`): `proc = get_processor(path)` → `proc = get_processor(path, self._db.config)`
- [ ] Edit `backend/epictrace/services/source.py`:
  - line 23 (`get_text`): `proc = get_processor(path)` → `proc = get_processor(path, self._db.config)`
  - line 38 (`get_attachment_text`): `proc = get_processor(Path(path))` → `proc = get_processor(Path(path), self._db.config)`
- [ ] Run `./.venv/bin/pytest -q tests/test_media_text.py tests/test_media_docs.py tests/test_ingest_service.py tests/test_index_service.py tests/test_references_service.py tests/test_source_service.py`. Expect PASS (all green).
- [ ] Run the full backend suite `./.venv/bin/pytest -q -k "not slow and not real_smoke"` to confirm no other call site broke. Expect PASS.
- [ ] Commit:
  ```
  git add backend/epictrace/media/__init__.py backend/epictrace/config.py backend/epictrace/services/ingest.py backend/epictrace/services/references.py backend/epictrace/services/index.py backend/epictrace/services/source.py backend/tests/test_media_text.py backend/tests/test_media_docs.py backend/tests/test_ingest_service.py backend/tests/test_index_service.py && git commit -m "$(cat <<'EOF'
  Thread config through get_processor(path, config)

  get_processor now takes AppConfig; all four extraction call sites
  (ingest/references/index/source) pass self._db.config. PDF slot still
  pypdf for now; swapped to MinerU in a later task. Add provenance_dir /
  mineru_venv_dir config properties.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3: Subprocess runner (`mineru_runner.py`)

> The actual `subprocess.run` is injected as a `runner` callable so tests fake it without a real mineru. Default runner uses `subprocess.run`.

**Files:**
- Create `backend/epictrace/media/mineru_runner.py`
- Create `backend/tests/test_mineru_runner.py`

Steps:

- [ ] Write failing test `backend/tests/test_mineru_runner.py`:
  ```python
  import json
  import subprocess
  from pathlib import Path

  import pytest

  from epictrace.media.errors import ExtractionFailed
  from epictrace.media.mineru_runner import run_mineru


  def _fake_ok(out_dir: Path, stem: str, markdown: str, content_list: list):
      """Return a runner that, when invoked, writes mineru's expected output tree
      (<out>/<stem>/<stem>.md + <stem>_content_list.json) then returns rc=0."""
      def runner(cmd, timeout):
          d = out_dir / stem
          d.mkdir(parents=True, exist_ok=True)
          (d / f"{stem}.md").write_text(markdown, encoding="utf-8")
          (d / f"{stem}_content_list.json").write_text(
              json.dumps(content_list), encoding="utf-8")
          return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
      return runner


  def test_builds_command_and_parses_output(tmp_path: Path):
      pdf = tmp_path / "paper.pdf"
      pdf.write_bytes(b"%PDF-1.4")
      out = tmp_path / "out"
      seen = {}
      content = [{"type": "text", "text": "hello", "page_idx": 0}]

      def runner(cmd, timeout):
          seen["cmd"] = cmd
          seen["timeout"] = timeout
          return _fake_ok(out, "paper", "# Hello\n\nworld", content)(cmd, timeout)

      md, cl = run_mineru(
          pdf, out, mineru_bin="/venv/bin/mineru",
          model_source="modelscope", timeout=600, runner=runner,
      )
      assert md == "# Hello\n\nworld"
      assert cl == content
      cmd = seen["cmd"]
      assert cmd[0] == "/venv/bin/mineru"
      assert "-p" in cmd and str(pdf) in cmd
      assert "-o" in cmd and str(out) in cmd
      assert "-b" in cmd and "hybrid-engine" in cmd
      assert "--effort" in cmd and "high" in cmd
      assert "--source" in cmd and "modelscope" in cmd
      assert seen["timeout"] == 600


  def test_nonzero_exit_raises(tmp_path: Path):
      pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
      def runner(cmd, timeout):
          return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom")
      with pytest.raises(ExtractionFailed):
          run_mineru(pdf, tmp_path / "o", mineru_bin="mineru",
                     model_source="modelscope", timeout=10, runner=runner)


  def test_timeout_raises(tmp_path: Path):
      pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
      def runner(cmd, timeout):
          raise subprocess.TimeoutExpired(cmd, timeout)
      with pytest.raises(ExtractionFailed):
          run_mineru(pdf, tmp_path / "o", mineru_bin="mineru",
                     model_source="modelscope", timeout=1, runner=runner)


  def test_missing_output_raises(tmp_path: Path):
      pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
      def runner(cmd, timeout):
          # rc=0 但没有写任何输出文件
          return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
      with pytest.raises(ExtractionFailed):
          run_mineru(pdf, tmp_path / "o", mineru_bin="mineru",
                     model_source="modelscope", timeout=10, runner=runner)


  def test_empty_markdown_raises(tmp_path: Path):
      pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
      out = tmp_path / "o"
      def runner(cmd, timeout):
          return _fake_ok(out, "p", "   \n  ", [])(cmd, timeout)
      with pytest.raises(ExtractionFailed):
          run_mineru(pdf, out, mineru_bin="mineru",
                     model_source="modelscope", timeout=10, runner=runner)
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_runner.py`. Expect FAIL (ModuleNotFoundError: `epictrace.media.mineru_runner`).
- [ ] Create `backend/epictrace/media/mineru_runner.py`:
  ```python
  from __future__ import annotations

  import json
  import subprocess
  from pathlib import Path
  from typing import Callable

  from epictrace.media.errors import ExtractionFailed

  # 注入点:默认用 subprocess.run;测试传假 runner 完全不碰真 mineru。
  Runner = Callable[[list[str], int], subprocess.CompletedProcess]


  def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
      return subprocess.run(
          cmd, timeout=timeout, capture_output=True, text=True, check=False
      )


  def run_mineru(
      pdf_path: Path,
      out_dir: Path,
      *,
      mineru_bin: str,
      model_source: str,
      timeout: int,
      runner: Runner | None = None,
  ) -> tuple[str, list]:
      """跑 MinerU 子进程(hybrid-engine, effort=high),读 markdown + content_list。

      失败语义(无回退):非零退出 / 超时 / 缺输出 / 空文本 → ExtractionFailed。
      runner 注入便于测试(默认 subprocess.run)。
      """
      run = runner or _default_runner
      out_dir.mkdir(parents=True, exist_ok=True)
      stem = pdf_path.stem
      cmd = [
          mineru_bin,
          "-p", str(pdf_path),
          "-o", str(out_dir),
          "-b", "hybrid-engine",
          "--effort", "high",
          "--source", model_source,
      ]
      try:
          proc = run(cmd, timeout)
      except subprocess.TimeoutExpired as e:
          raise ExtractionFailed(f"mineru timed out after {timeout}s") from e
      except OSError as e:  # 二进制缺失/不可执行
          raise ExtractionFailed(f"mineru could not be launched: {e}") from e
      if proc.returncode != 0:
          raise ExtractionFailed(
              f"mineru exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
          )
      result_dir = out_dir / stem
      md_path = result_dir / f"{stem}.md"
      cl_path = result_dir / f"{stem}_content_list.json"
      if not md_path.exists():
          raise ExtractionFailed(f"mineru produced no markdown at {md_path}")
      markdown = md_path.read_text(encoding="utf-8", errors="replace")
      if not markdown.strip():
          raise ExtractionFailed("mineru produced empty markdown")
      content_list: list = []
      if cl_path.exists():
          try:
              content_list = json.loads(cl_path.read_text(encoding="utf-8"))
          except (json.JSONDecodeError, OSError):
              content_list = []
      return markdown, content_list
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_runner.py`. Expect PASS (5 passed).
- [ ] Commit:
  ```
  git add backend/epictrace/media/mineru_runner.py backend/tests/test_mineru_runner.py && git commit -m "$(cat <<'EOF'
  Add MinerU subprocess runner

  run_mineru builds the hybrid-engine/high command, runs it (injectable
  runner) with a timeout, parses <stem>.md + <stem>_content_list.json;
  non-zero/timeout/missing/empty -> ExtractionFailed (no fallback).

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4: `MinerUProvisioner`

> Manages `<data_dir>/.MinerU-venv`. The uv invocation is injected (`uv_runner`) so tests fake uv without a real install/network. `uv_bin()` resolves `uv` on PATH (dev); a `uv_bin` override allows a bundled binary later (DMG).

**Files:**
- Create `backend/epictrace/media/mineru_provisioner.py`
- Create `backend/tests/test_mineru_provisioner.py`

Steps:

- [ ] Write failing test `backend/tests/test_mineru_provisioner.py`:
  ```python
  import subprocess
  from pathlib import Path

  import pytest

  from epictrace.media.mineru_provisioner import MinerUProvisioner


  def _venv_dir(tmp_path: Path) -> Path:
      return tmp_path / ".MinerU-venv"


  def test_not_ready_before_provision(tmp_path: Path):
      p = MinerUProvisioner(_venv_dir(tmp_path), uv_bin="/usr/local/bin/uv")
      assert p.is_ready() is False
      assert p.state == "not_installed"


  def test_provision_runs_uv_commands_and_becomes_ready(tmp_path: Path):
      venv = _venv_dir(tmp_path)
      calls: list[list[str]] = []

      def uv_runner(cmd, timeout):
          calls.append(cmd)
          # 模拟 `uv venv` 创建 bin/mineru 可执行,使 is_ready() 转真
          if "venv" in cmd:
              (venv / "bin").mkdir(parents=True, exist_ok=True)
              (venv / "bin" / "mineru").write_text("#!/bin/sh\n")
              (venv / "bin" / "mineru").chmod(0o755)
          return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

      progress: list[str] = []
      p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", uv_runner=uv_runner)
      p.provision(progress_cb=progress.append)

      assert p.state == "ready"
      assert p.is_ready() is True
      # 第一条:uv venv --python 3.11 <venv>
      assert calls[0][:1] == ["/usr/local/bin/uv"]
      assert "venv" in calls[0]
      assert "--python" in calls[0] and "3.11" in calls[0]
      assert str(venv) in calls[0]
      # 第二条:uv pip install "mineru[all]" (into the venv)
      assert "pip" in calls[1] and "install" in calls[1]
      assert any("mineru[all]" in c for c in calls[1])
      assert len(progress) >= 1  # 粗粒度进度回调


  def test_provision_failure_sets_failed_state(tmp_path: Path):
      venv = _venv_dir(tmp_path)

      def uv_runner(cmd, timeout):
          return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="network down")

      p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", uv_runner=uv_runner)
      with pytest.raises(RuntimeError):
          p.provision()
      assert p.state == "failed"
      assert p.is_ready() is False


  def test_mineru_bin_path(tmp_path: Path):
      venv = _venv_dir(tmp_path)
      p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv")
      assert p.mineru_bin() == str(venv / "bin" / "mineru")


  def test_uv_bin_defaults_to_path_lookup(tmp_path: Path, monkeypatch):
      monkeypatch.setattr(
          "epictrace.media.mineru_provisioner.shutil.which",
          lambda name: "/found/uv" if name == "uv" else None,
      )
      p = MinerUProvisioner(_venv_dir(tmp_path))
      assert p.uv_bin() == "/found/uv"
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_provisioner.py`. Expect FAIL (ModuleNotFoundError: `epictrace.media.mineru_provisioner`).
- [ ] Create `backend/epictrace/media/mineru_provisioner.py`:
  ```python
  from __future__ import annotations

  import shutil
  import subprocess
  from pathlib import Path
  from typing import Callable

  # 注入点:默认用 subprocess.run;测试传假 uv_runner,完全不碰真 uv/网络。
  UvRunner = Callable[[list[str], int], subprocess.CompletedProcess]

  # provision 阶段较长(建环境 + 装几 GB 依赖);给宽松超时。
  _PROVISION_TIMEOUT = 3600


  def _default_uv_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
      return subprocess.run(
          cmd, timeout=timeout, capture_output=True, text=True, check=False
      )


  class MinerUProvisioner:
      """管 <data_dir>/.MinerU-venv:用 uv 建隔离环境并装 mineru[all]。

      状态机:not_installed -> installing -> ready / failed。
      uv 调用注入(uv_runner)便于测试;uv_bin 可覆盖(dev: PATH;DMG: 打包内置)。
      """

      def __init__(
          self,
          venv_dir: Path,
          *,
          uv_bin: str | None = None,
          uv_runner: UvRunner | None = None,
      ) -> None:
          self._venv_dir = Path(venv_dir)
          self._uv_bin = uv_bin
          self._uv_runner = uv_runner or _default_uv_runner
          self._failed = False

      # ---- 路径 ----
      def mineru_bin(self) -> str:
          return str(self._venv_dir / "bin" / "mineru")

      def uv_bin(self) -> str:
          if self._uv_bin:
              return self._uv_bin
          found = shutil.which("uv")
          if not found:
              raise RuntimeError("uv not found on PATH; cannot provision MinerU")
          return found

      # ---- 状态 ----
      def is_ready(self) -> bool:
          return Path(self.mineru_bin()).exists()

      @property
      def state(self) -> str:
          if self.is_ready():
              return "ready"
          if self._failed:
              return "failed"
          return "not_installed"

      # ---- provision ----
      def provision(self, progress_cb: Callable[[str], None] | None = None) -> None:
          def emit(msg: str) -> None:
              if progress_cb is not None:
                  progress_cb(msg)

          self._failed = False
          uv = self.uv_bin()
          self._venv_dir.parent.mkdir(parents=True, exist_ok=True)

          emit("创建隔离环境…")
          venv_cmd = [uv, "venv", "--python", "3.11", str(self._venv_dir)]
          self._run_or_fail(venv_cmd)

          emit("安装 MinerU(首次较久)…")
          install_cmd = [
              uv, "pip", "install", "--python", str(self._venv_dir),
              "mineru[all]",
          ]
          self._run_or_fail(install_cmd)
          emit("安装完成")

      def _run_or_fail(self, cmd: list[str]) -> None:
          try:
              proc = self._uv_runner(cmd, _PROVISION_TIMEOUT)
          except (subprocess.TimeoutExpired, OSError) as e:
              self._failed = True
              raise RuntimeError(f"uv command failed: {e}") from e
          if proc.returncode != 0:
              self._failed = True
              raise RuntimeError(
                  f"uv exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
              )
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_provisioner.py`. Expect PASS (5 passed).
- [ ] Commit:
  ```
  git add backend/epictrace/media/mineru_provisioner.py backend/tests/test_mineru_provisioner.py && git commit -m "$(cat <<'EOF'
  Add MinerUProvisioner

  Manages <data_dir>/.MinerU-venv via uv (uv venv --python 3.11 +
  uv pip install mineru[all]); is_ready/mineru_bin/uv_bin + state machine
  (not_installed/installing/ready/failed). uv invocation injectable.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5: `MinerUMediaProcessor`

> Implements `MediaProcessor`. Injects a `provisioner` and a `runner` callable (defaulting to `run_mineru`). Uses a temp output dir per call.

**Files:**
- Create `backend/epictrace/media/mineru.py`
- Create `backend/tests/test_mineru_processor.py`

Steps:

- [ ] Write failing test `backend/tests/test_mineru_processor.py`:
  ```python
  from pathlib import Path

  import pytest

  from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed
  from epictrace.media.mineru import MinerUMediaProcessor


  class _FakeProvisioner:
      def __init__(self, ready: bool):
          self._ready = ready

      def is_ready(self) -> bool:
          return self._ready

      def mineru_bin(self) -> str:
          return "/venv/bin/mineru"


  def test_supports_pdf_always(tmp_path: Path):
      proc = MinerUMediaProcessor(_FakeProvisioner(ready=False),
                                  model_source="modelscope", timeout=600)
      assert proc.supports(Path("x.pdf")) is True
      assert proc.supports(Path("X.PDF")) is True
      assert proc.supports(Path("x.docx")) is False


  def test_not_ready_raises_engine_not_ready(tmp_path: Path):
      proc = MinerUMediaProcessor(_FakeProvisioner(ready=False),
                                  model_source="modelscope", timeout=600)
      with pytest.raises(ExtractionEngineNotReady):
          proc.process(tmp_path / "x.pdf")


  def test_ready_returns_media_result(tmp_path: Path):
      pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
      content = [{"type": "text", "text": "hi", "page_idx": 0},
                 {"type": "text", "text": "bye", "page_idx": 1}]
      captured = {}

      def fake_runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout):
          captured["mineru_bin"] = mineru_bin
          captured["model_source"] = model_source
          captured["timeout"] = timeout
          return "# Title\n\nbody", content

      proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                  model_source="modelscope", timeout=600,
                                  runner=fake_runner)
      result = proc.process(pdf)
      assert result.text == "# Title\n\nbody"
      assert result.metadata["backend"] == "mineru-hybrid"
      assert result.metadata["content_list"] == content
      assert result.metadata["pages"] == 2  # max page_idx + 1
      assert captured["mineru_bin"] == "/venv/bin/mineru"
      assert captured["model_source"] == "modelscope"
      assert captured["timeout"] == 600


  def test_runner_failure_propagates_as_extraction_failed(tmp_path: Path):
      pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")

      def boom(pdf_path, out_dir, *, mineru_bin, model_source, timeout):
          raise ExtractionFailed("subprocess died")

      proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                  model_source="modelscope", timeout=600,
                                  runner=boom)
      with pytest.raises(ExtractionFailed):
          proc.process(pdf)
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_processor.py`. Expect FAIL (ModuleNotFoundError: `epictrace.media.mineru`).
- [ ] Create `backend/epictrace/media/mineru.py`:
  ```python
  from __future__ import annotations

  import tempfile
  from pathlib import Path
  from typing import Callable, Protocol

  from epictrace.interfaces.media import MediaProcessor, MediaResult
  from epictrace.media.errors import ExtractionEngineNotReady
  from epictrace.media.mineru_runner import run_mineru


  class _Provisioner(Protocol):
      def is_ready(self) -> bool: ...
      def mineru_bin(self) -> str: ...


  # runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout) -> (markdown, content_list)
  RunnerFn = Callable[..., tuple[str, list]]


  def _page_count(content_list: list) -> int:
      pages = [b.get("page_idx") for b in content_list
               if isinstance(b, dict) and isinstance(b.get("page_idx"), int)]
      return (max(pages) + 1) if pages else 0


  class MinerUMediaProcessor(MediaProcessor):
      """PDF 唯一引擎(无回退)。未就绪 → ExtractionEngineNotReady;子进程失败 →
      ExtractionFailed(由 runner 抛出,直接透传,不退回 pypdf)。"""

      def __init__(
          self,
          provisioner: _Provisioner,
          *,
          model_source: str,
          timeout: int,
          runner: RunnerFn | None = None,
      ) -> None:
          self._provisioner = provisioner
          self._model_source = model_source
          self._timeout = timeout
          self._runner = runner or run_mineru

      def supports(self, path: Path) -> bool:
          return path.suffix.lower() == ".pdf"

      def process(self, path: Path) -> MediaResult:
          if not self._provisioner.is_ready():
              raise ExtractionEngineNotReady(
                  "高质量提取引擎尚未安装,请先在设置中安装 MinerU。"
              )
          with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
              markdown, content_list = self._runner(
                  path, Path(tmp),
                  mineru_bin=self._provisioner.mineru_bin(),
                  model_source=self._model_source,
                  timeout=self._timeout,
              )
          return MediaResult(
              text=markdown,
              metadata={
                  "backend": "mineru-hybrid",
                  "content_list": content_list,
                  "pages": _page_count(content_list),
              },
          )
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_processor.py`. Expect PASS (4 passed).
- [ ] Commit:
  ```
  git add backend/epictrace/media/mineru.py backend/tests/test_mineru_processor.py && git commit -m "$(cat <<'EOF'
  Add MinerUMediaProcessor

  Implements MediaProcessor for PDF: not-ready -> ExtractionEngineNotReady;
  ready -> run injected mineru runner -> MediaResult(text=md, metadata with
  backend/content_list/pages); runner ExtractionFailed propagates (no pypdf
  fallback).

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 6: Registry wiring (swap PDF slot to MinerU)

> Now `media/__init__.py` builds the PDF slot as `MinerUMediaProcessor` from `config` (provisioner over `config.mineru_venv_dir`); `PdfMediaProcessor` is removed from the active registry (the `pdf.py` file stays). This needs `model_source`/`extraction_timeout` on config — but those land in Task 8. To keep this task self-contained, read them via `getattr(config, ..., default)` so it works whether or not Task 8 has run, then Task 8 makes them first-class.

**Files:**
- Modify `backend/epictrace/media/__init__.py`
- Modify `backend/tests/test_media_docs.py` (PDF case: assert MinerU, not pypdf)

Steps:

- [ ] Update `backend/tests/test_media_docs.py` PDF test to assert the MinerU processor is selected (and that without provisioning, processing raises rather than returning pypdf text). Replace `test_pdf_extraction` with:
  ```python
  def test_pdf_slot_is_mineru_not_pypdf(tmp_path: Path):
      from reportlab.pdfgen import canvas

      from epictrace.media.mineru import MinerUMediaProcessor
      from epictrace.media.errors import ExtractionEngineNotReady

      p = tmp_path / "a.pdf"
      c = canvas.Canvas(str(p)); c.drawString(72, 720, "Hello PDF world"); c.save()
      proc = get_processor(p, AppConfig(data_dir=tmp_path))
      assert isinstance(proc, MinerUMediaProcessor)  # pypdf 不再被选中
      # 未 provision → 处理 PDF 报错(无回退,不返回 pypdf 文本)
      with pytest.raises(ExtractionEngineNotReady):
          proc.process(p)
  ```
  Add `import pytest` to the top of the file if not present.
- [ ] Run `./.venv/bin/pytest -q tests/test_media_docs.py`. Expect FAIL (current `_pdf_processor` still returns `PdfMediaProcessor`, so `isinstance(... MinerUMediaProcessor)` fails).
- [ ] Edit `backend/epictrace/media/__init__.py` to build the MinerU PDF slot. Replace the imports and `_pdf_processor`:
  - Remove `from epictrace.media.pdf import PdfMediaProcessor`.
  - Add:
    ```python
    from epictrace.media.mineru import MinerUMediaProcessor
    from epictrace.media.mineru_provisioner import MinerUProvisioner
    ```
  - Replace `_pdf_processor`:
    ```python
    def _pdf_processor(config: AppConfig) -> MediaProcessor:
        provisioner = MinerUProvisioner(config.mineru_venv_dir)
        return MinerUMediaProcessor(
            provisioner,
            model_source=getattr(config, "model_source", "modelscope"),
            timeout=getattr(config, "extraction_timeout", 600),
        )
    ```
  - Update the module docstring/comment to note `pdf.py` is kept but unregistered.
- [ ] Run `./.venv/bin/pytest -q tests/test_media_docs.py tests/test_media_text.py`. Expect PASS (PDF → MinerU; text/docx/pptx unchanged).
- [ ] Run the full suite `./.venv/bin/pytest -q -k "not slow and not real_smoke"`. Expect PASS. (Ingest/source/index tests of PDFs do not exist in the default suite; the text-based tests are unaffected. If any default-suite test ingests a real `.pdf` and expects pypdf text, fix it to use a non-PDF fixture or assert the no-fallback raise — none currently do per the existing tests read in this plan.)
- [ ] Commit:
  ```
  git add backend/epictrace/media/__init__.py backend/tests/test_media_docs.py && git commit -m "$(cat <<'EOF'
  Wire MinerU into the PDF registry slot

  get_processor returns MinerUMediaProcessor for .pdf, built from config
  with a MinerUProvisioner over config.mineru_venv_dir. PdfMediaProcessor
  removed from the active registry; media/pdf.py kept unreferenced.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 7: Provenance sidecar persistence

> A small helper writes `<data_dir>/provenance/<kind>-<id>.json`. The ingest call site writes `kind="ingest"` after the record is flushed (has an id); the reference call site writes `kind="reference"` for external attachments after the ref is created. Only fires when `MediaResult.metadata` carries a non-empty `content_list`.

**Files:**
- Create `backend/epictrace/media/provenance.py`
- Create `backend/tests/test_media_provenance.py`
- Modify `backend/epictrace/services/ingest.py`
- Modify `backend/epictrace/services/references.py`
- Modify `backend/tests/test_ingest_service.py` (add a provenance-persisted test using a fake PDF processor)

Steps:

- [ ] Write failing test `backend/tests/test_media_provenance.py`:
  ```python
  import json
  from pathlib import Path

  from epictrace.media.provenance import write_provenance


  def test_writes_sidecar_to_expected_path(tmp_path: Path):
      content = [{"type": "text", "text": "hi", "page_idx": 0}]
      out = write_provenance(tmp_path, "ingest", 42, content)
      assert out == tmp_path / "provenance" / "ingest-42.json"
      assert out.exists()
      assert json.loads(out.read_text(encoding="utf-8")) == content


  def test_reference_kind_path(tmp_path: Path):
      out = write_provenance(tmp_path, "reference", 7, [])
      assert out == tmp_path / "provenance" / "reference-7.json"
      assert out.exists()
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_media_provenance.py`. Expect FAIL (ModuleNotFoundError: `epictrace.media.provenance`).
- [ ] Create `backend/epictrace/media/provenance.py`:
  ```python
  from __future__ import annotations

  import json
  from pathlib import Path


  def write_provenance(data_dir: Path, kind: str, item_id: int, content_list: list) -> Path:
      """落 content_list sidecar 到 <data_dir>/provenance/<kind>-<id>.json。

      派生缓存(可由重跑 MinerU 重建),不进核心 SQL 事实表。
      kind 为 'ingest' 或 'reference'。
      """
      out_dir = Path(data_dir) / "provenance"
      out_dir.mkdir(parents=True, exist_ok=True)
      out_path = out_dir / f"{kind}-{item_id}.json"
      out_path.write_text(
          json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
      )
      return out_path
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_media_provenance.py`. Expect PASS (2 passed).
- [ ] Wire the ingest call site. In `backend/epictrace/services/ingest.py`, add the import at top: `from epictrace.media.provenance import write_provenance`. Then change the extraction block (lines ~68-87) so it captures the `MediaResult` (not just `.text`) and writes provenance after `s.refresh(rec)`:
  ```python
              try:
                  proc = get_processor(dest, self._db.config)
                  result = proc.process(dest) if proc is not None else None
                  extracted = result.text if result is not None else ""

                  rec = IngestRecord(
                      project_id=project_id,
                      original_filename=src.name,
                      stored_path=str(dest),
                      content_hash=_sha256(dest),
                      size_bytes=dest.stat().st_size,
                      mtime=dest.stat().st_mtime,
                      ingest_method=ingest_method,
                      description=description,
                      extracted_text=extracted,
                  )
                  s.add(rec)
                  s.flush()
                  s.refresh(rec)
                  if result is not None and result.metadata.get("content_list"):
                      write_provenance(
                          self._db.config.data_dir, "ingest", rec.id,
                          result.metadata["content_list"],
                      )
                  s.expunge(rec)
                  return rec
              except Exception:
                  dest.unlink(missing_ok=True)
                  raise
  ```
- [ ] Wire the reference (external attachment) call site. In `backend/epictrace/services/references.py`, add `from epictrace.media.provenance import write_provenance` at top. In `add_external`, capture the full result and write provenance after the ref id is known. Replace the extraction + persistence portion:
  ```python
          proc = get_processor(p, self._db.config)
          if proc is None:
              raise ValueError("unsupported file type")
          try:
              result = proc.process(p)
          except Exception as e:  # noqa: BLE001 — 提取失败转成可读的 400(由路由映射)
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
          if result.metadata.get("content_list"):
              write_provenance(
                  self._db.config.data_dir, "reference", ref_id,
                  result.metadata["content_list"],
              )
  ```
  (Leave the rest of `add_external` — the deferred-indexing block and `return out` — unchanged.)
- [ ] Add a provenance-persisted ingest test to `backend/tests/test_ingest_service.py`:
  ```python
  def test_ingest_pdf_persists_provenance_sidecar(tmp_path: Path, monkeypatch):
      db, proj = _setup(tmp_path)
      src = tmp_path / "src" / "paper.pdf"
      src.parent.mkdir()
      src.write_bytes(b"%PDF-1.4 fake")

      from epictrace.interfaces.media import MediaResult

      content = [{"type": "text", "text": "hi", "page_idx": 0}]

      class _PdfProc:
          def supports(self, _path):
              return True

          def process(self, _path):
              return MediaResult(text="# extracted", metadata={
                  "backend": "mineru-hybrid", "content_list": content, "pages": 1})

      monkeypatch.setattr(
          "epictrace.services.ingest.get_processor",
          lambda path, config: _PdfProc(),
      )
      rec = IngestService(db).ingest_file(
          project_id=proj.id, source_path=str(src),
          ingest_method="file_direct", description="",
      )
      sidecar = Path(tmp_path) / "provenance" / f"ingest-{rec.id}.json"
      assert sidecar.exists()
      import json
      assert json.loads(sidecar.read_text(encoding="utf-8")) == content
      assert rec.extracted_text == "# extracted"
  ```
  (Note: `_setup` builds `Database(AppConfig(data_dir=tmp_path))`, so `self._db.config.data_dir == tmp_path`.)
- [ ] Run `./.venv/bin/pytest -q tests/test_media_provenance.py tests/test_ingest_service.py tests/test_references_service.py`. Expect PASS.
- [ ] Commit:
  ```
  git add backend/epictrace/media/provenance.py backend/tests/test_media_provenance.py backend/epictrace/services/ingest.py backend/epictrace/services/references.py backend/tests/test_ingest_service.py && git commit -m "$(cat <<'EOF'
  Persist content_list provenance sidecar at ingest/attachment

  write_provenance writes <data_dir>/provenance/<kind>-<id>.json; ingest
  and external-reference call sites emit it when MediaResult carries a
  content_list. Derived cache, not a SQL fact table.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 8: Config + settings provisioning status

> Make `model_source` / `extraction_timeout` first-class on `AppConfig`, and add a `SettingsService.extraction_status()` accessor that reports the provisioner state.

**Files:**
- Modify `backend/epictrace/config.py`
- Modify `backend/epictrace/services/settings.py`
- Modify `backend/tests/test_config.py` (add fields)
- Create test cases in `backend/tests/test_settings.py` (extraction_status) — or add to the existing file.

Steps:

- [ ] Add a config test. Append to `backend/tests/test_config.py`:
  ```python
  def test_extraction_defaults():
      from epictrace.config import AppConfig

      c = AppConfig()
      assert c.model_source == "modelscope"
      assert c.extraction_timeout == 600
      assert c.mineru_venv_dir == c.data_dir / ".MinerU-venv"
      assert c.provenance_dir == c.data_dir / "provenance"
  ```
- [ ] Add a settings test. Append to `backend/tests/test_settings.py`:
  ```python
  def test_extraction_status_reports_state(tmp_path):
      from epictrace.config import AppConfig
      from epictrace.services.settings import SettingsService

      svc = SettingsService(AppConfig(data_dir=tmp_path))
      status = svc.extraction_status()
      assert status["state"] == "not_installed"
      assert status["ready"] is False
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_config.py tests/test_settings.py`. Expect FAIL (`AppConfig` has no `model_source`; `SettingsService` has no `extraction_status`).
- [ ] Edit `backend/epictrace/config.py`: add fields to `AppConfig` (after `chat_llm`):
  ```python
      # 高质量提取(MinerU):模型源 + 子进程超时(秒)。
      model_source: str = "modelscope"
      extraction_timeout: int = 600
  ```
  (`mineru_venv_dir` / `provenance_dir` properties were added in Task 2.)
- [ ] Edit `backend/epictrace/services/settings.py`: add the accessor (and an import). At top add `from epictrace.media.mineru_provisioner import MinerUProvisioner`. Store config and add the method:
  - In `__init__`, after `self._path = config.data_dir / "settings.json"`, add `self._config = config`.
  - Add a method (e.g. after `is_configured`):
    ```python
        def extraction_status(self) -> dict:
            """高质量提取引擎(MinerU)的 provisioning 状态。"""
            prov = MinerUProvisioner(self._config.mineru_venv_dir)
            return {"state": prov.state, "ready": prov.is_ready()}
    ```
- [ ] Run `./.venv/bin/pytest -q tests/test_config.py tests/test_settings.py`. Expect PASS.
- [ ] Confirm Task 6's `getattr` now resolves to first-class attributes: run `./.venv/bin/pytest -q tests/test_media_docs.py`. Expect PASS.
- [ ] Commit:
  ```
  git add backend/epictrace/config.py backend/epictrace/services/settings.py backend/tests/test_config.py backend/tests/test_settings.py && git commit -m "$(cat <<'EOF'
  Add model_source/extraction_timeout config + extraction_status accessor

  AppConfig gains model_source (modelscope) and extraction_timeout (600);
  SettingsService.extraction_status() reports the MinerUProvisioner state.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 9: Provision + status API endpoints

> `GET /api/extraction/status` returns `{state, ready}`. `POST /api/extraction/provision` kicks off provisioning in a background thread (matching the index-job background pattern) and returns the current status. MVP: no fine-grained stdout progress scraping — coarse state only. The provisioner is built lazily and cached on `app.state` via a deps helper so it is injectable in tests.

**Files:**
- Modify `backend/epictrace/api/deps.py`
- Modify `backend/epictrace/api/routers/settings.py`
- Modify `backend/epictrace/schemas.py`
- Create `backend/tests/test_extraction_api.py`

Steps:

- [ ] Write failing test `backend/tests/test_extraction_api.py`:
  ```python
  import threading

  import pytest
  from fastapi.testclient import TestClient

  from epictrace.api.app import create_app
  from epictrace.config import AppConfig
  from epictrace.db import Database


  class _FakeProvisioner:
      def __init__(self):
          self._ready = False
          self.provisioned = threading.Event()

      def is_ready(self):
          return self._ready

      @property
      def state(self):
          return "ready" if self._ready else "not_installed"

      def provision(self, progress_cb=None):
          self._ready = True
          self.provisioned.set()


  @pytest.fixture()
  def app_and_prov(tmp_path):
      db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
      app = create_app(db=db)
      prov = _FakeProvisioner()
      app.state.provisioner = prov   # 注入假 provisioner(deps.get_provisioner 优先用它)
      return TestClient(app), prov


  def test_status_reports_not_installed(app_and_prov):
      client, _ = app_and_prov
      r = client.get("/api/extraction/status")
      assert r.status_code == 200
      body = r.json()
      assert body["state"] == "not_installed"
      assert body["ready"] is False


  def test_provision_kicks_off_and_becomes_ready(app_and_prov):
      client, prov = app_and_prov
      r = client.post("/api/extraction/provision")
      assert r.status_code == 200
      assert prov.provisioned.wait(timeout=5)  # 后台线程跑完
      # 轮询 status 直到 ready
      assert client.get("/api/extraction/status").json()["ready"] is True
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_extraction_api.py`. Expect FAIL (404 — endpoints don't exist).
- [ ] Add the schema to `backend/epictrace/schemas.py`:
  ```python
  class ExtractionStatusOut(BaseModel):
      state: str            # not_installed | installing | ready | failed
      ready: bool
      error: str | None = None
  ```
- [ ] Add a deps helper to `backend/epictrace/api/deps.py`:
  ```python
  def get_provisioner(request: Request):
      """高质量提取 provisioner(MinerU)。优先用注入的 app.state.provisioner(测试假件);
      否则按 app.state.config.mineru_venv_dir 懒构造并缓存。"""
      prov = getattr(request.app.state, "provisioner", None)
      if prov is not None:
          return prov
      from epictrace.config import AppConfig
      from epictrace.media.mineru_provisioner import MinerUProvisioner

      config = getattr(request.app.state, "config", None) or AppConfig()
      prov = MinerUProvisioner(config.mineru_venv_dir)
      request.app.state.provisioner = prov
      return prov
  ```
- [ ] Add the endpoints to `backend/epictrace/api/routers/settings.py`. Add imports at top:
  ```python
  import threading

  from epictrace.api.deps import get_provisioner
  from epictrace.schemas import ExtractionStatusOut
  ```
  And append the routes:
  ```python
  def _provision_status(prov) -> ExtractionStatusOut:
      error = getattr(prov, "last_error", None)
      return ExtractionStatusOut(state=prov.state, ready=prov.is_ready(), error=error)


  @router.get("/extraction/status", response_model=ExtractionStatusOut)
  def extraction_status(request: Request):
      return _provision_status(get_provisioner(request))


  @router.post("/extraction/provision", response_model=ExtractionStatusOut)
  def extraction_provision(request: Request):
      """触发 provisioning(后台线程,粗粒度状态)。立即返回当前状态;前端轮询 status。"""
      prov = get_provisioner(request)

      def _run():
          try:
              prov.provision()
          except Exception as exc:  # noqa: BLE001 — 失败状态由 prov.state 反映;记录原因
              try:
                  prov.last_error = str(exc)[:500]
              except Exception:  # noqa: BLE001
                  pass

      threading.Thread(target=_run, daemon=True).start()
      return _provision_status(prov)
  ```
  (Note: `MinerUProvisioner` does not define `last_error`; `getattr(prov, "last_error", None)` returns `None`, and the thread sets it on failure. This keeps the schema's `error` field meaningful without changing the provisioner contract. The fake in the test also lacks it → `None`.)
- [ ] Run `./.venv/bin/pytest -q tests/test_extraction_api.py`. Expect PASS (2 passed).
- [ ] Run `./.venv/bin/pytest -q tests/test_api_settings.py` to confirm the settings router still works. Expect PASS.
- [ ] Commit:
  ```
  git add backend/epictrace/api/deps.py backend/epictrace/api/routers/settings.py backend/epictrace/schemas.py backend/tests/test_extraction_api.py && git commit -m "$(cat <<'EOF'
  Add extraction status/provision API endpoints

  GET /api/extraction/status returns {state, ready, error};
  POST /api/extraction/provision kicks off provisioning in a background
  thread (coarse state only). Provisioner built lazily / injectable via
  deps.get_provisioner.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 10: Minimal frontend "高质量提取" section

> A minimal `<section>` in `SettingsView.tsx` showing the engine status (未安装/安装中/就绪/失败), an install button calling `provisionExtraction()`, and polling `getExtractionStatus()` while installing. Functional, not polished.

**Files:**
- Modify `frontend/src/lib/api.ts`
- Modify `frontend/src/views/SettingsView.tsx`

Steps:

- [ ] Add the API surface to `frontend/src/lib/api.ts`. Add the interface near the other interfaces (after `Settings`):
  ```ts
  export interface ExtractionStatus {
    state: "not_installed" | "installing" | "ready" | "failed";
    ready: boolean;
    error?: string | null;
  }
  ```
  And add to the `api` object (e.g. after `testProfile`):
  ```ts
    getExtractionStatus: () =>
      fetch(`${BASE}/api/extraction/status`).then(j<ExtractionStatus>),
    provisionExtraction: () =>
      fetch(`${BASE}/api/extraction/provision`, { method: "POST" }).then(j<ExtractionStatus>),
  ```
- [ ] Add the section to `frontend/src/views/SettingsView.tsx`. Add the import update (extend the existing `@/lib/api` import to include `ExtractionStatus`):
  ```ts
  import { api, type ExtractionStatus, type LLMProfile, type Settings } from "@/lib/api";
  ```
  Add a self-contained component at the bottom of the file (next to the other helper components):
  ```tsx
  const STATE_LABEL: Record<ExtractionStatus["state"], string> = {
    not_installed: "未安装",
    installing: "安装中",
    ready: "就绪",
    failed: "失败",
  };

  function ExtractionSection() {
    const [status, setStatus] = useState<ExtractionStatus | null>(null);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
      let cancelled = false;
      api
        .getExtractionStatus()
        .then((s) => !cancelled && setStatus(s))
        .catch((e) => !cancelled && setErr(String(e)));
      return () => {
        cancelled = true;
      };
    }, []);

    // 安装中:轮询 status 直到 ready/failed。
    useEffect(() => {
      if (status?.state !== "installing" && !busy) return;
      const t = setInterval(() => {
        api
          .getExtractionStatus()
          .then((s) => {
            setStatus(s);
            if (s.state === "ready" || s.state === "failed") {
              setBusy(false);
              clearInterval(t);
            }
          })
          .catch(() => {});
      }, 2000);
      return () => clearInterval(t);
    }, [status?.state, busy]);

    const install = async () => {
      setBusy(true);
      setErr(null);
      try {
        setStatus(await api.provisionExtraction());
      } catch (e) {
        setErr(String(e));
        setBusy(false);
      }
    };

    const ready = status?.ready === true;
    const installing = busy || status?.state === "installing";

    return (
      <section className="mt-10 flex flex-col gap-3 border-t border-border/60 pt-8">
        <div className="flex flex-col gap-1">
          <h2 className="text-sm font-semibold text-foreground">高质量提取</h2>
          <p className="text-xs leading-relaxed text-muted-foreground">
            用 MinerU(版面/表格/公式/OCR)替代基础 PDF 提取。首次安装会下载模型(约数 GB),仅一次,装完全本地运行。
          </p>
        </div>

        <div className="flex items-center gap-3 rounded-xl border border-border/70 bg-muted/30 px-3 py-2.5">
          <span className="flex items-center gap-2 text-sm text-foreground">
            {installing && <Loader2 className="size-3.5 animate-spin" />}
            {ready && <CheckCircle2 className="size-3.5 text-primary" strokeWidth={2.25} />}
            {status?.state === "failed" && <TriangleAlert className="size-3.5 text-destructive" />}
            状态:{status ? STATE_LABEL[status.state] : "…"}
          </span>
          <div className="ml-auto">
            {!ready && (
              <Button
                type="button"
                size="sm"
                disabled={installing}
                onClick={install}
                title="下载并安装 MinerU 提取引擎"
              >
                {installing ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" />
                    安装中…
                  </>
                ) : status?.state === "failed" ? (
                  "重试安装"
                ) : (
                  "安装"
                )}
              </Button>
            )}
          </div>
        </div>

        {(err || status?.error) && (
          <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
            {err || status?.error}
          </p>
        )}
      </section>
    );
  }
  ```
  Then render `<ExtractionSection />` inside the page, immediately after the closing `</section>` of the Profile-management section (before the closing `</div></div>` of the page container):
  ```tsx
          </section>

          <ExtractionSection />
        </div>
      </div>
  ```
- [ ] Run `cd frontend && npm run build` (from `/Users/william/Desktop/EpicTrace`, e.g. `npm --prefix frontend run build`). Expect a successful build (no TypeScript errors). If `Loader2`/`CheckCircle2`/`TriangleAlert` aren't already imported in this file, they are (verified at top of `SettingsView.tsx`).
- [ ] Commit:
  ```
  git add frontend/src/lib/api.ts frontend/src/views/SettingsView.tsx && git commit -m "$(cat <<'EOF'
  Add minimal high-quality-extraction settings section

  SettingsView gains a "高质量提取" section: status badge
  (未安装/安装中/就绪/失败), install button -> provisionExtraction, polls
  getExtractionStatus while installing. api.ts gains ExtractionStatus +
  the two calls.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 11: Verification (full suite + opt-in real-mineru slow test + frontend build)

**Files:**
- Create `backend/tests/test_mineru_slow.py`
- Modify `backend/pyproject.toml` (documentation comment only)

Steps:

- [ ] Create the opt-in real-model slow test `backend/tests/test_mineru_slow.py`:
  ```python
  import os
  from pathlib import Path

  import pytest

  pytestmark = pytest.mark.skipif(
      os.environ.get("EPICTRACE_RUN_SLOW") != "1",
      reason="real-mineru extraction test; set EPICTRACE_RUN_SLOW=1 to run",
  )


  def test_real_mineru_extracts_a_pdf(tmp_path):
      """Sketch: against a provisioned .MinerU-venv, run the real mineru on a
      small generated PDF and assert non-empty markdown + a content_list.
      Skips if MinerU is not provisioned (no model download forced here)."""
      from reportlab.pdfgen import canvas

      from epictrace.config import AppConfig
      from epictrace.media.mineru import MinerUMediaProcessor
      from epictrace.media.mineru_provisioner import MinerUProvisioner

      config = AppConfig()
      prov = MinerUProvisioner(config.mineru_venv_dir)
      if not prov.is_ready():
          pytest.skip("MinerU not provisioned; install via settings first")

      pdf = tmp_path / "sample.pdf"
      c = canvas.Canvas(str(pdf))
      c.drawString(72, 720, "Hello high quality extraction world")
      c.save()

      proc = MinerUMediaProcessor(
          prov, model_source=config.model_source, timeout=config.extraction_timeout
      )
      result = proc.process(pdf)
      assert result.text.strip()
      assert result.metadata["backend"] == "mineru-hybrid"
      assert isinstance(result.metadata["content_list"], list)
  ```
- [ ] Run `./.venv/bin/pytest -q tests/test_mineru_slow.py`. Expect it to be SKIPPED (no `EPICTRACE_RUN_SLOW`). Output should show `1 skipped`.
- [ ] Add a documentation comment to `backend/pyproject.toml` recording that MinerU is NOT a core dependency (it lives in `.MinerU-venv`, provisioned at runtime via uv). After the `"sse-starlette",` line in `dependencies`, add the comment line just above the closing `]`:
  ```toml
    "sse-starlette",
    # 注:MinerU 不进核心环境(几 GB)。它由 MinerUProvisioner 用 uv 装进
    # <data_dir>/.MinerU-venv,运行期子进程调用(见 epictrace/media/mineru*.py)。
  ]
  ```
- [ ] Run the full backend suite excluding slow/real-smoke: `./.venv/bin/pytest -q -k "not slow and not real_smoke"` from `/Users/william/Desktop/EpicTrace/backend`. Expect all green (0 failed).
- [ ] Run the full suite WITHOUT the `-k` filter to confirm slow/real tests are skipped (not failing): `./.venv/bin/pytest -q`. Expect green with skips for the `EPICTRACE_RUN_SLOW`-gated and real-smoke tests.
- [ ] Run the frontend build: `npm --prefix frontend run build` from `/Users/william/Desktop/EpicTrace`. Expect a clean build.
- [ ] Commit:
  ```
  git add backend/tests/test_mineru_slow.py backend/pyproject.toml && git commit -m "$(cat <<'EOF'
  Add opt-in real-mineru slow test + document MinerU is not a core dep

  test_mineru_slow runs the real mineru on a generated PDF when
  EPICTRACE_RUN_SLOW=1 and .MinerU-venv is provisioned (else skips).
  pyproject documents that MinerU lives in .MinerU-venv, not the core env.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Spec coverage check (§4–§10)

- **§4 Architecture** — Tasks 5 (processor: ready/not-ready branches), 6 (registry slot, `get_processor(path, config)`), 3 (subprocess command + output read), 2 (config threading), `pdf.py` kept unregistered (Task 6).
- **§5.1 `MinerUProvisioner`** — Task 4 (`is_ready`/`provision`/`mineru_bin`/`uv_bin`, state machine, injectable uv).
- **§5.2 `MinerUMediaProcessor`** — Task 5 (`.pdf` supports, not-ready raise, ready→runner→MediaResult, failure→ExtractionFailed, no fallback).
- **§5.3 Subprocess runner** — Task 3 (command assembly, timeout, output read, failure semantics, `--source` model source).
- **§5.4 Provenance archival** — Task 7 (helper + ingest/attachment call-site wiring, metadata `content_list`).
- **§5.5 Settings/registry** — Tasks 6 (registry from config), 8 (`model_source`/`extraction_timeout`/status), 9 (provision/status endpoints), 10 (frontend section).
- **§6 Data flow** — Tasks 9/10 (provision → status), 5/6 (PDF → MinerU subprocess → markdown into existing chain), 7 (content_list archival); not-ready/failed raises (Tasks 5, mapped at call sites in Task 2/7).
- **§7 Provisioning & model download UX** — Tasks 9 (coarse background provision, no stdout scraping) + 10 (status badge, "下载模型(约数 GB),仅首次" copy, spinner).
- **§8 Data-model/contract changes** — Tasks 2 (`get_processor(path, config)`), 6 (`_PROCESSORS` swap, pypdf kept), 5 (`MediaResult.metadata` backend/content_list), 1 (new exceptions), 7 (`provenance/` sidecar), 8 (`model_source`/`extraction_timeout`/status), 9 (new endpoints), 10 (frontend section); no SQL table added.
- **§9 Error handling & boundaries** — Tasks 5 (not-ready / failure raises, no fallback), 3 (non-zero/timeout/missing/empty → ExtractionFailed), 4/9 (provision failure → `failed` state + retry), Task 2 (existing failure paths preserved). The hybrid-engine flag is pinned per §9's note; Task 3 hard-codes `hybrid-engine --effort high` per the spec — confirm against the pinned MinerU version during execution and adjust the flag string in `mineru_runner.py` only if the installed version uses `hybrid-auto-engine`.
- **§10 Test strategy** — Tasks 5 (processor fakes), 4 (provisioner fakes), 6 (`get_processor` selects MinerU, pypdf not chosen), 7 (provenance), 2/7 (call-site behavior), 11 (`EPICTRACE_RUN_SLOW` real test + `npm run build`).

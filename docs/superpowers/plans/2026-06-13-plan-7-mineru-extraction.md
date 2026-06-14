# Plan 7:MinerU 高质量提取实现计划

> **致 agentic workers:** 必备子技能:使用 superpowers:subagent-driven-development(推荐)或 superpowers:executing-plans 逐任务实现本计划。各步骤用 checkbox(`- [ ]`)语法跟踪进度。

**Goal:** 在现有 `MediaProcessor` 接口缝后,用 MinerU(hybrid-engine, effort=high)子进程后端替换 pypdf 的 PDF 处理器 —— 包括应用内用 `uv` provision 一个隔离的 `.MinerU-venv`、无回退的错误语义,以及一个 content_list provenance sidecar —— 让下游切割/索引/引用拿到更好的文本,且对 Plan 5/6 零改动。

**Architecture:** `get_processor(path, config)` 为 `.pdf` 返回 `MinerUMediaProcessor`(由 `config` 构造,带一个作用于 `<data_dir>/.MinerU-venv` 的 `MinerUProvisioner`)。就绪时,`process()` 在独立子进程中调用 `<.MinerU-venv>/bin/mineru`(从结构上规避 macOS gRPC-fork segfault),解析 `<stem>.md` + `<stem>_content_list.json`,并返回 `MediaResult`;未就绪时抛 `ExtractionEngineNotReady`,任何子进程失败/超时/缺输出则抛 `ExtractionFailed`(无 pypdf 回退)。四个提取调用点(ingest/references/index/source)通过 `self._db.config` 把 `config` 传下去,ingest + reference 两个调用点会持久化一个 `content_list` provenance sidecar。新增 `extraction/status` + `extraction/provision` API 端点驱动一个极简的"高质量提取"设置区块。

**Tech Stack:** Python 3.11(FastAPI 后端,venv 在 `backend/.venv`),MinerU CLI(`mineru[all]`)经 `uv` provision 进 `<data_dir>/.MinerU-venv`,pytest 注入假 subprocess/uv runner(不碰真实 mineru/uv/网络),React + shadcn/ui + Tailwind 前端。

---

## 文件结构

### 后端 —— 新建

| File | 职责 |
| --- | --- |
| `backend/epictrace/media/errors.py` | `ExtractionEngineNotReady` + `ExtractionFailed` 异常(media 层)。 |
| `backend/epictrace/media/mineru_runner.py` | `run_mineru(...)` —— 拼出 `mineru` 命令,带 timeout 运行它(runner 可注入),读 `<stem>.md` + `<stem>_content_list.json`,返回 `(markdown, content_list)`;失败/超时/缺失/空 → `ExtractionFailed`。 |
| `backend/epictrace/media/mineru_provisioner.py` | `MinerUProvisioner` —— 管理 `<data_dir>/.MinerU-venv`:`is_ready()`、`provision(progress_cb)`、`mineru_bin()`、`uv_bin()`、`state` 状态机(not_installed/installing/ready/failed);uv 调用可注入。 |
| `backend/epictrace/media/mineru.py` | `MinerUMediaProcessor(MediaProcessor)` —— 永远支持 `.pdf`;`process()` → 未就绪抛 `ExtractionEngineNotReady`,就绪则调用注入的 runner → `MediaResult`,runner 失败透传 `ExtractionFailed`。 |
| `backend/epictrace/media/provenance.py` | `write_provenance(data_dir, kind, item_id, content_list)` —— 写 `<data_dir>/provenance/<kind>-<id>.json`。 |

### 后端 —— 修改

| File | 职责 |
| --- | --- |
| `backend/epictrace/media/__init__.py` | `get_processor(path, config)` 签名;由 `config` 把 PDF 槽构造成 `MinerUMediaProcessor`;从 `_PROCESSORS` 移除 `PdfMediaProcessor`(保留 `pdf.py`)。 |
| `backend/epictrace/config.py` | 给 `AppConfig` 加 `model_source`(默认 `"modelscope"`)和 `extraction_timeout`(默认 `600`);`mineru_venv_dir` + `provenance_dir` 属性。 |
| `backend/epictrace/services/ingest.py` | `get_processor(dest, self._db.config)`;metadata 里有 `content_list` 时,在 flush 之后写 `ingest` provenance。 |
| `backend/epictrace/services/references.py` | `get_processor(p, self._db.config)`(x2);为携带 `content_list` 的外部附件写 `reference` provenance。 |
| `backend/epictrace/services/index.py` | `get_processor(Path(...), self._db.config)`(x2)。 |
| `backend/epictrace/services/source.py` | `get_processor(path, self._db.config)`(x2)。 |
| `backend/epictrace/services/settings.py` | `extraction_status()` 访问器,从 `MinerUProvisioner` 返回 `{state, ready}`。 |
| `backend/epictrace/api/routers/settings.py` | `GET /extraction/status` + `POST /extraction/provision` 端点。 |
| `backend/epictrace/api/deps.py` | `get_provisioner(request)` 辅助函数(在 `app.state` 上懒构造/缓存 `MinerUProvisioner`)。 |
| `backend/epictrace/schemas.py` | `ExtractionStatusOut` schema。 |
| `backend/pyproject.toml` | (无新增运行时依赖 —— MinerU 住在 `.MinerU-venv`,不在核心环境。加一条注释说明这一点。) |

### 后端 —— 测试(新建)

| File | 职责 |
| --- | --- |
| `backend/tests/test_media_errors.py` | 两个异常存在、继承 `Exception`、携带消息。 |
| `backend/tests/test_mineru_runner.py` | 借假 subprocess runner 验命令拼装 + 输出解析;非零/超时/缺失/空 → `ExtractionFailed`。 |
| `backend/tests/test_mineru_provisioner.py` | 假 uv 的命令拼装(`uv venv` / `uv pip install`)、状态机、`is_ready()` 探测。 |
| `backend/tests/test_mineru_processor.py` | `supports(.pdf)`;未就绪 → `ExtractionEngineNotReady`;就绪 → `MediaResult` 文本/metadata;runner 失败 → `ExtractionFailed`(无 pypdf 文本)。 |
| `backend/tests/test_media_provenance.py` | `write_provenance` 路径 + 内容。 |
| `backend/tests/test_extraction_api.py` | `GET /extraction/status` + `POST /extraction/provision`(假 provisioner)。 |
| `backend/tests/test_mineru_slow.py` | 可选开启(`EPICTRACE_RUN_SLOW=1`)的真 mineru 测试;除非已 provision `.MinerU-venv`,否则 skip。 |

### 后端 —— 测试(修改)

| File | 职责 |
| --- | --- |
| `backend/tests/test_media_docs.py` | `get_processor(p, config)`;断言 PDF → `MinerUMediaProcessor`,而非 pypdf。 |
| `backend/tests/test_media_text.py` | `get_processor(f, config)` 两参调用。 |
| `backend/tests/test_ingest_service.py` | 用 2 参 lambda monkeypatch `get_processor`;加一个 pdf 上 provenance 已持久化的测试。 |
| `backend/tests/test_index_service.py` | `boom(p, config)` 2 参 monkeypatch。 |
| `backend/tests/test_references_service.py` | (无需改签名 —— 用真实文本文件;确认仍为绿。) |
| `backend/tests/test_source_service.py` | (无需改签名 —— 确认仍为绿。) |

### 前端 —— 修改

| File | 职责 |
| --- | --- |
| `frontend/src/lib/api.ts` | `ExtractionStatus` interface + `getExtractionStatus()` / `provisionExtraction()` 调用。 |
| `frontend/src/views/SettingsView.tsx` | 一个极简的"高质量提取" `<section>`:状态徽标(未安装/安装中/就绪/失败)、安装按钮 → provision、轮询 status。 |

---

## Task 1:Media 层异常

**文件:**
- 创建 `backend/epictrace/media/errors.py`
- 创建 `backend/tests/test_media_errors.py`

步骤:

- [ ] 写失败测试 `backend/tests/test_media_errors.py`:
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
- [ ] 在 `/Users/william/Desktop/EpicTrace/backend` 下运行 `./.venv/bin/pytest -q tests/test_media_errors.py`,预期 FAIL(ModuleNotFoundError:`epictrace.media.errors`)。
- [ ] 创建 `backend/epictrace/media/errors.py`:
  ```python
  from __future__ import annotations


  class ExtractionEngineNotReady(Exception):
      """PDF 提取引擎(MinerU)尚未 provision/就绪。调用方应提示用户先安装高质量提取引擎。"""


  class ExtractionFailed(Exception):
      """MinerU 子进程失败/超时/缺输出/空文本。无回退——调用方按既有失败路径呈现。"""
  ```
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_errors.py`,预期 PASS(2 passed)。
- [ ] 提交:
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

## Task 2:`get_processor(path, config)` 签名变更 + 把 config 穿过所有调用点

> **集成风险任务。** `get_processor` 在 4 个 service 中共 5 处调用:`ingest.py`(1)、`references.py`(2)、`index.py`(2)、`source.py`(2)。所有 service 都已持有 `self._db`,而 `Database` 暴露了 `.config` 属性(`backend/epictrace/db.py`),所以穿参就是 `get_processor(path, self._db.config)` —— **无需改 service 构造器或 router**。PDF 槽暂时仍返回当前的 pypdf 处理器(在 Task 6 才换成 MinerU);本任务纯粹是签名/穿参改动,让套件在任务之间保持绿。

**文件:**
- 修改 `backend/epictrace/media/__init__.py`
- 修改 `backend/epictrace/config.py`(现在先加 `provenance_dir` + `mineru_venv_dir` 属性,便于后续任务复用;`model_source`/`extraction_timeout` 在 Task 8 才加)
- 修改 `backend/epictrace/services/ingest.py`
- 修改 `backend/epictrace/services/references.py`
- 修改 `backend/epictrace/services/index.py`
- 修改 `backend/epictrace/services/source.py`
- 修改 `backend/tests/test_media_docs.py`
- 修改 `backend/tests/test_media_text.py`
- 修改 `backend/tests/test_ingest_service.py`
- 修改 `backend/tests/test_index_service.py`

步骤:

- [ ] 把测试 `backend/tests/test_media_text.py` 改成新的两参签名。替换那三处 `get_processor(...)` 调用行:
  - 在 imports 里加 `from epictrace.config import AppConfig`。
  - `proc = get_processor(f)` → `proc = get_processor(f, AppConfig(data_dir=tmp_path))`
  - `proc = get_processor(f)`(循环里那处) → `proc = get_processor(f, AppConfig(data_dir=tmp_path))`
  - `assert get_processor(tmp_path / "a.png") is None` → `assert get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path)) is None`
- [ ] 更新测试 `backend/tests/test_media_docs.py`:加 `from epictrace.config import AppConfig`,并把每处 `get_processor(p)` / `get_processor(tmp_path / "...")` 都改成把 `AppConfig(data_dir=tmp_path)` 作为第二个参数传入。对 PDF 测试,暂时保留 `assert proc is not None` 和 `assert "Hello PDF" in proc.process(p).text`(Task 6 会把它改成断言没有 pypdf 文本)。具体:
  - `proc = get_processor(p)`(docx) → `proc = get_processor(p, AppConfig(data_dir=tmp_path))`
  - `proc = get_processor(p)`(pptx) → `proc = get_processor(p, AppConfig(data_dir=tmp_path))`
  - `proc = get_processor(p)`(pdf) → `proc = get_processor(p, AppConfig(data_dir=tmp_path))`
  - 两处未知类型的 assert → `get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path))` / `get_processor(tmp_path / "a.mp3", AppConfig(data_dir=tmp_path))`
- [ ] 把测试 `backend/tests/test_ingest_service.py` 的 monkeypatch(line 126)改成 2 参 lambda:
  - `monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda _: _BadProc())` → `monkeypatch.setattr("epictrace.services.ingest.get_processor", lambda _path, _config: _BadProc())`
- [ ] 把测试 `backend/tests/test_index_service.py` 的 monkeypatch(lines 59-66)改成 2 参签名:
  - `def boom(p):` → `def boom(p, config):`
  - 内部,`return real(p)` → `return real(p, config)`
- [ ] 在 `/Users/william/Desktop/EpicTrace/backend` 下运行 `./.venv/bin/pytest -q tests/test_media_text.py tests/test_media_docs.py tests/test_ingest_service.py tests/test_index_service.py`,预期 FAIL(当前 `get_processor` 只收一个位置参数 → TypeError)。
- [ ] 给 `backend/epictrace/config.py` 加 config 属性(在 `AppConfig` 内,`attachment_milvus_path` 之后):
  ```python
      @property
      def mineru_venv_dir(self) -> Path:
          return self.data_dir / ".MinerU-venv"

      @property
      def provenance_dir(self) -> Path:
          return self.data_dir / "provenance"
  ```
- [ ] 把 `backend/epictrace/media/__init__.py` 重写成新签名(本任务 PDF 槽仍是 pypdf;MinerU 替换在 Task 6):
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
- [ ] 编辑 `backend/epictrace/services/ingest.py` line 69:`proc = get_processor(dest)` → `proc = get_processor(dest, self._db.config)`。
- [ ] 编辑 `backend/epictrace/services/references.py`:
  - line 42(`add_external`):`proc = get_processor(p)` → `proc = get_processor(p, self._db.config)`
  - line 82(`add_internal`):`proc = get_processor(path)` → `proc = get_processor(path, self._db.config)`
- [ ] 编辑 `backend/epictrace/services/index.py`:
  - line 57:`if get_processor(Path(r.stored_path)) is not None` → `if get_processor(Path(r.stored_path), self._db.config) is not None`
  - line 78(在 `_run` 内):`proc = get_processor(path)` → `proc = get_processor(path, self._db.config)`
- [ ] 编辑 `backend/epictrace/services/source.py`:
  - line 23(`get_text`):`proc = get_processor(path)` → `proc = get_processor(path, self._db.config)`
  - line 38(`get_attachment_text`):`proc = get_processor(Path(path))` → `proc = get_processor(Path(path), self._db.config)`
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_text.py tests/test_media_docs.py tests/test_ingest_service.py tests/test_index_service.py tests/test_references_service.py tests/test_source_service.py`,预期 PASS(全绿)。
- [ ] 运行完整后端套件 `./.venv/bin/pytest -q -k "not slow and not real_smoke"`,确认没有其他调用点被打破,预期 PASS。
- [ ] 提交:
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

## Task 3:子进程 runner(`mineru_runner.py`)

> 真正的 `subprocess.run` 作为 `runner` 可注入,这样测试可以在没有真 mineru 的情况下伪造它。默认 runner 用 `subprocess.run`。

**文件:**
- 创建 `backend/epictrace/media/mineru_runner.py`
- 创建 `backend/tests/test_mineru_runner.py`

步骤:

- [ ] 写失败测试 `backend/tests/test_mineru_runner.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_runner.py`,预期 FAIL(ModuleNotFoundError:`epictrace.media.mineru_runner`)。
- [ ] 创建 `backend/epictrace/media/mineru_runner.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_runner.py`,预期 PASS(5 passed)。
- [ ] 提交:
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

## Task 4:`MinerUProvisioner`

> 管理 `<data_dir>/.MinerU-venv`。uv 调用被注入(`uv_runner`),这样测试可以在不真正安装/联网的情况下伪造 uv。`uv_bin()` 在 PATH 上解析 `uv`(dev);`uv_bin` 覆盖项允许以后改用打包内置的二进制(DMG)。

**文件:**
- 创建 `backend/epictrace/media/mineru_provisioner.py`
- 创建 `backend/tests/test_mineru_provisioner.py`

步骤:

- [ ] 写失败测试 `backend/tests/test_mineru_provisioner.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_provisioner.py`,预期 FAIL(ModuleNotFoundError:`epictrace.media.mineru_provisioner`)。
- [ ] 创建 `backend/epictrace/media/mineru_provisioner.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_provisioner.py`,预期 PASS(5 passed)。
- [ ] 提交:
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

## Task 5:`MinerUMediaProcessor`

> 实现 `MediaProcessor`。注入一个 `provisioner` 和一个 `runner`(默认为 `run_mineru`)。每次调用用一个临时输出目录。

**文件:**
- 创建 `backend/epictrace/media/mineru.py`
- 创建 `backend/tests/test_mineru_processor.py`

步骤:

- [ ] 写失败测试 `backend/tests/test_mineru_processor.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_processor.py`,预期 FAIL(ModuleNotFoundError:`epictrace.media.mineru`)。
- [ ] 创建 `backend/epictrace/media/mineru.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_processor.py`,预期 PASS(4 passed)。
- [ ] 提交:
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

## Task 6:注册表接线(把 PDF 槽换成 MinerU)

> 现在 `media/__init__.py` 由 `config` 把 PDF 槽构造成 `MinerUMediaProcessor`(provisioner 作用于 `config.mineru_venv_dir`);`PdfMediaProcessor` 从生效的注册表中移除(`pdf.py` 文件保留)。这需要 config 上的 `model_source`/`extraction_timeout` —— 但那俩要到 Task 8 才落地。为让本任务自洽,用 `getattr(config, ..., default)` 读取,这样无论 Task 8 是否跑过都能工作,然后由 Task 8 把它们升为一等字段。

**文件:**
- 修改 `backend/epictrace/media/__init__.py`
- 修改 `backend/tests/test_media_docs.py`(PDF 用例:断言 MinerU,而非 pypdf)

步骤:

- [ ] 更新 `backend/tests/test_media_docs.py` 的 PDF 测试,断言选中的是 MinerU 处理器(且未 provision 时,处理会抛错而非返回 pypdf 文本)。把 `test_pdf_extraction` 替换为:
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
  如果文件顶部还没有 `import pytest`,加上。
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_docs.py`,预期 FAIL(当前 `_pdf_processor` 仍返回 `PdfMediaProcessor`,所以 `isinstance(... MinerUMediaProcessor)` 失败)。
- [ ] 编辑 `backend/epictrace/media/__init__.py` 以构造 MinerU 的 PDF 槽。替换 imports 和 `_pdf_processor`:
  - 移除 `from epictrace.media.pdf import PdfMediaProcessor`。
  - 加上:
    ```python
    from epictrace.media.mineru import MinerUMediaProcessor
    from epictrace.media.mineru_provisioner import MinerUProvisioner
    ```
  - 替换 `_pdf_processor`:
    ```python
    def _pdf_processor(config: AppConfig) -> MediaProcessor:
        provisioner = MinerUProvisioner(config.mineru_venv_dir)
        return MinerUMediaProcessor(
            provisioner,
            model_source=getattr(config, "model_source", "modelscope"),
            timeout=getattr(config, "extraction_timeout", 600),
        )
    ```
  - 更新模块 docstring/注释,说明 `pdf.py` 保留但未注册。
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_docs.py tests/test_media_text.py`,预期 PASS(PDF → MinerU;text/docx/pptx 不变)。
- [ ] 运行完整套件 `./.venv/bin/pytest -q -k "not slow and not real_smoke"`,预期 PASS。(默认套件里不存在对 PDF 的 ingest/source/index 测试;基于文本的测试不受影响。若有任何默认套件测试 ingest 了真实 `.pdf` 并期待 pypdf 文本,把它改成用非 PDF fixture 或断言无回退抛错 —— 按本计划已读到的现有测试,目前没有这样的测试。)
- [ ] 提交:
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

## Task 7:Provenance sidecar 持久化

> 一个小辅助函数写 `<data_dir>/provenance/<kind>-<id>.json`。ingest 调用点在记录被 flush(拿到 id)后写 `kind="ingest"`;reference 调用点在外部附件的 ref 创建后写 `kind="reference"`。仅当 `MediaResult.metadata` 携带非空 `content_list` 时触发。

**文件:**
- 创建 `backend/epictrace/media/provenance.py`
- 创建 `backend/tests/test_media_provenance.py`
- 修改 `backend/epictrace/services/ingest.py`
- 修改 `backend/epictrace/services/references.py`
- 修改 `backend/tests/test_ingest_service.py`(用一个假 PDF 处理器加一个 provenance 已持久化的测试)

步骤:

- [ ] 写失败测试 `backend/tests/test_media_provenance.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_provenance.py`,预期 FAIL(ModuleNotFoundError:`epictrace.media.provenance`)。
- [ ] 创建 `backend/epictrace/media/provenance.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_provenance.py`,预期 PASS(2 passed)。
- [ ] 接线 ingest 调用点。在 `backend/epictrace/services/ingest.py` 顶部加 import:`from epictrace.media.provenance import write_provenance`。然后改提取块(约 lines 68-87),让它捕获 `MediaResult`(不只是 `.text`),并在 `s.refresh(rec)` 之后写 provenance:
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
- [ ] 接线 reference(外部附件)调用点。在 `backend/epictrace/services/references.py` 顶部加 `from epictrace.media.provenance import write_provenance`。在 `add_external` 中,捕获完整 result,并在拿到 ref id 后写 provenance。替换提取 + 持久化部分:
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
  (`add_external` 的其余部分 —— deferred-indexing 块和 `return out` —— 保持不变。)
- [ ] 给 `backend/tests/test_ingest_service.py` 加一个 provenance 已持久化的 ingest 测试:
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
  (注:`_setup` 构造 `Database(AppConfig(data_dir=tmp_path))`,所以 `self._db.config.data_dir == tmp_path`。)
- [ ] 运行 `./.venv/bin/pytest -q tests/test_media_provenance.py tests/test_ingest_service.py tests/test_references_service.py`,预期 PASS。
- [ ] 提交:
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

## Task 8:Config + 设置 provisioning 状态

> 把 `model_source` / `extraction_timeout` 升为 `AppConfig` 的一等字段,并加一个 `SettingsService.extraction_status()` 访问器,报告 provisioner 状态。

**文件:**
- 修改 `backend/epictrace/config.py`
- 修改 `backend/epictrace/services/settings.py`
- 修改 `backend/tests/test_config.py`(加字段)
- 在 `backend/tests/test_settings.py` 中创建测试用例(extraction_status) —— 或加到现有文件里。

步骤:

- [ ] 加一个 config 测试。追加到 `backend/tests/test_config.py`:
  ```python
  def test_extraction_defaults():
      from epictrace.config import AppConfig

      c = AppConfig()
      assert c.model_source == "modelscope"
      assert c.extraction_timeout == 600
      assert c.mineru_venv_dir == c.data_dir / ".MinerU-venv"
      assert c.provenance_dir == c.data_dir / "provenance"
  ```
- [ ] 加一个 settings 测试。追加到 `backend/tests/test_settings.py`:
  ```python
  def test_extraction_status_reports_state(tmp_path):
      from epictrace.config import AppConfig
      from epictrace.services.settings import SettingsService

      svc = SettingsService(AppConfig(data_dir=tmp_path))
      status = svc.extraction_status()
      assert status["state"] == "not_installed"
      assert status["ready"] is False
  ```
- [ ] 运行 `./.venv/bin/pytest -q tests/test_config.py tests/test_settings.py`,预期 FAIL(`AppConfig` 没有 `model_source`;`SettingsService` 没有 `extraction_status`)。
- [ ] 编辑 `backend/epictrace/config.py`:给 `AppConfig` 加字段(在 `chat_llm` 之后):
  ```python
      # 高质量提取(MinerU):模型源 + 子进程超时(秒)。
      model_source: str = "modelscope"
      extraction_timeout: int = 600
  ```
  (`mineru_venv_dir` / `provenance_dir` 属性已在 Task 2 加过。)
- [ ] 编辑 `backend/epictrace/services/settings.py`:加访问器(和一个 import)。顶部加 `from epictrace.media.mineru_provisioner import MinerUProvisioner`。存下 config 并加方法:
  - 在 `__init__` 中,`self._path = config.data_dir / "settings.json"` 之后,加 `self._config = config`。
  - 加一个方法(例如在 `is_configured` 之后):
    ```python
        def extraction_status(self) -> dict:
            """高质量提取引擎(MinerU)的 provisioning 状态。"""
            prov = MinerUProvisioner(self._config.mineru_venv_dir)
            return {"state": prov.state, "ready": prov.is_ready()}
    ```
- [ ] 运行 `./.venv/bin/pytest -q tests/test_config.py tests/test_settings.py`,预期 PASS。
- [ ] 确认 Task 6 的 `getattr` 现在解析到一等属性:运行 `./.venv/bin/pytest -q tests/test_media_docs.py`,预期 PASS。
- [ ] 提交:
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

## Task 9:Provision + 状态 API 端点

> `GET /api/extraction/status` 返回 `{state, ready}`。`POST /api/extraction/provision` 在后台线程里启动 provisioning(与 index-job 的后台模式一致),并返回当前状态。MVP:不做细粒度 stdout 进度刮取 —— 只给粗粒度状态。provisioner 通过一个 deps 辅助函数懒构造并缓存在 `app.state` 上,以便在测试中注入。

**文件:**
- 修改 `backend/epictrace/api/deps.py`
- 修改 `backend/epictrace/api/routers/settings.py`
- 修改 `backend/epictrace/schemas.py`
- 创建 `backend/tests/test_extraction_api.py`

步骤:

- [ ] 写失败测试 `backend/tests/test_extraction_api.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_extraction_api.py`,预期 FAIL(404 —— 端点不存在)。
- [ ] 给 `backend/epictrace/schemas.py` 加 schema:
  ```python
  class ExtractionStatusOut(BaseModel):
      state: str            # not_installed | installing | ready | failed
      ready: bool
      error: str | None = None
  ```
- [ ] 给 `backend/epictrace/api/deps.py` 加一个 deps 辅助函数:
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
- [ ] 给 `backend/epictrace/api/routers/settings.py` 加端点。顶部加 imports:
  ```python
  import threading

  from epictrace.api.deps import get_provisioner
  from epictrace.schemas import ExtractionStatusOut
  ```
  并追加路由:
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
  (注:`MinerUProvisioner` 没有定义 `last_error`;`getattr(prov, "last_error", None)` 返回 `None`,失败时由线程设置它。这让 schema 的 `error` 字段有意义,又不改 provisioner 契约。测试里的假件也没有它 → `None`。)
- [ ] 运行 `./.venv/bin/pytest -q tests/test_extraction_api.py`,预期 PASS(2 passed)。
- [ ] 运行 `./.venv/bin/pytest -q tests/test_api_settings.py`,确认 settings router 仍工作,预期 PASS。
- [ ] 提交:
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

## Task 10:极简前端"高质量提取"区块

> 在 `SettingsView.tsx` 中加一个极简 `<section>`,显示引擎状态(未安装/安装中/就绪/失败)、一个调用 `provisionExtraction()` 的安装按钮,以及安装期间轮询 `getExtractionStatus()`。能用即可,不追求精致。

**文件:**
- 修改 `frontend/src/lib/api.ts`
- 修改 `frontend/src/views/SettingsView.tsx`

步骤:

- [ ] 给 `frontend/src/lib/api.ts` 加 API 接口。在其他 interface 旁边(`Settings` 之后)加这个 interface:
  ```ts
  export interface ExtractionStatus {
    state: "not_installed" | "installing" | "ready" | "failed";
    ready: boolean;
    error?: string | null;
  }
  ```
  并加到 `api` 对象里(例如在 `testProfile` 之后):
  ```ts
    getExtractionStatus: () =>
      fetch(`${BASE}/api/extraction/status`).then(j<ExtractionStatus>),
    provisionExtraction: () =>
      fetch(`${BASE}/api/extraction/provision`, { method: "POST" }).then(j<ExtractionStatus>),
  ```
- [ ] 给 `frontend/src/views/SettingsView.tsx` 加区块。更新 import(扩展现有的 `@/lib/api` import,纳入 `ExtractionStatus`):
  ```ts
  import { api, type ExtractionStatus, type LLMProfile, type Settings } from "@/lib/api";
  ```
  在文件底部(其他辅助组件旁边)加一个自洽的组件:
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
  然后在页面内渲染 `<ExtractionSection />`,紧接在 Profile 管理区块的闭合 `</section>` 之后(在页面容器闭合 `</div></div>` 之前):
  ```tsx
          </section>

          <ExtractionSection />
        </div>
      </div>
  ```
- [ ] 运行 `cd frontend && npm run build`(从 `/Users/william/Desktop/EpicTrace`,例如 `npm --prefix frontend run build`),预期构建成功(无 TypeScript 错误)。若本文件还没 import `Loader2`/`CheckCircle2`/`TriangleAlert`,其实已经 import 了(已在 `SettingsView.tsx` 顶部确认)。
- [ ] 提交:
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

## Task 11:验证(完整套件 + 可选开启的真 mineru 慢测 + 前端构建)

**文件:**
- 创建 `backend/tests/test_mineru_slow.py`
- 修改 `backend/pyproject.toml`(仅文档注释)

步骤:

- [ ] 创建可选开启的真模型慢测 `backend/tests/test_mineru_slow.py`:
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
- [ ] 运行 `./.venv/bin/pytest -q tests/test_mineru_slow.py`,预期被 SKIPPED(没有 `EPICTRACE_RUN_SLOW`)。输出应显示 `1 skipped`。
- [ ] 给 `backend/pyproject.toml` 加一条文档注释,记录 MinerU 不是核心依赖(它住在 `.MinerU-venv`,运行期由 uv provision)。在 `dependencies` 里 `"sse-starlette",` 一行之后、闭合 `]` 之前,加注释行:
  ```toml
    "sse-starlette",
    # 注:MinerU 不进核心环境(几 GB)。它由 MinerUProvisioner 用 uv 装进
    # <data_dir>/.MinerU-venv,运行期子进程调用(见 epictrace/media/mineru*.py)。
  ]
  ```
- [ ] 在 `/Users/william/Desktop/EpicTrace/backend` 下运行排除 slow/real-smoke 的完整后端套件:`./.venv/bin/pytest -q -k "not slow and not real_smoke"`,预期全绿(0 failed)。
- [ ] 不带 `-k` 过滤运行完整套件,确认 slow/real 测试被 skip(而非失败):`./.venv/bin/pytest -q`,预期绿色,且受 `EPICTRACE_RUN_SLOW` 把关的测试与 real-smoke 测试被 skip。
- [ ] 运行前端构建:从 `/Users/william/Desktop/EpicTrace` 运行 `npm --prefix frontend run build`,预期构建干净。
- [ ] 提交:
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

## Spec 覆盖核对(§4–§10)

- **§4 Architecture** —— Tasks 5(处理器:就绪/未就绪分支)、6(注册表槽、`get_processor(path, config)`)、3(子进程命令 + 读输出)、2(config 穿参),`pdf.py` 保留但未注册(Task 6)。
- **§5.1 `MinerUProvisioner`** —— Task 4(`is_ready`/`provision`/`mineru_bin`/`uv_bin`、状态机、可注入 uv)。
- **§5.2 `MinerUMediaProcessor`** —— Task 5(支持 `.pdf`、未就绪抛错、就绪→runner→MediaResult、失败→ExtractionFailed、无回退)。
- **§5.3 子进程 runner** —— Task 3(命令拼装、timeout、读输出、失败语义、`--source` 模型源)。
- **§5.4 Provenance 归档** —— Task 7(辅助函数 + ingest/attachment 调用点接线、metadata `content_list`)。
- **§5.5 设置/注册表** —— Tasks 6(从 config 构造注册表)、8(`model_source`/`extraction_timeout`/状态)、9(provision/status 端点)、10(前端区块)。
- **§6 数据流** —— Tasks 9/10(provision → status)、5/6(PDF → MinerU 子进程 → markdown 进入现有链路)、7(content_list 归档);未就绪/失败抛错(Task 5,在调用点由 Task 2/7 映射)。
- **§7 Provisioning 与模型下载 UX** —— Tasks 9(粗粒度后台 provision,不刮 stdout)+ 10(状态徽标、"下载模型(约数 GB),仅首次"文案、spinner)。
- **§8 数据模型/契约变更** —— Tasks 2(`get_processor(path, config)`)、6(`_PROCESSORS` 替换、pypdf 保留)、5(`MediaResult.metadata` backend/content_list)、1(新异常)、7(`provenance/` sidecar)、8(`model_source`/`extraction_timeout`/状态)、9(新端点)、10(前端区块);未新增 SQL 表。
- **§9 错误处理与边界** —— Tasks 5(未就绪 / 失败抛错、无回退)、3(非零/超时/缺失/空 → ExtractionFailed)、4/9(provision 失败 → `failed` 状态 + 重试)、Task 2(保留现有失败路径)。hybrid-engine 标志按 §9 注释钉死;Task 3 按 spec 硬编码 `hybrid-engine --effort high` —— 执行时对照钉死的 MinerU 版本确认,仅当所装版本用的是 `hybrid-auto-engine` 时才调整 `mineru_runner.py` 中的标志字符串。
- **§10 测试策略** —— Tasks 5(处理器假件)、4(provisioner 假件)、6(`get_processor` 选中 MinerU、不选 pypdf)、7(provenance)、2/7(调用点行为)、11(`EPICTRACE_RUN_SLOW` 真测 + `npm run build`)。

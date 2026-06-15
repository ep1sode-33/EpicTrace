# 批次 B:提取设置 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 MinerU 高质量提取加上用户可配置设置(effort / model_source)+ 独立的模型预下载步骤,并在「装了包但没下模型」时发文件能自动下载且进度可见。

**Architecture:** provisioner 从「装包即就绪」拆成「装包(`installed_no_models`)→ 下模型(`ready`)」两步独立子进程注入;`SettingsService` 把 `extraction` 设置持久化进 `settings.json`,registry 构造 `MinerUMediaProcessor` 时读它;提取入口(references SSE / 项目索引)在处理前检查状态,缺模型则先经现有进度通道把模型下载完再提取。

**Tech Stack:** Python 3.11 + FastAPI(后端,venv 在 `backend/.venv`)· pytest(全 fake,不碰真 uv/mineru/网络)· React + TypeScript + shadcn/ui(前端)。

---

## 文件结构

**修改:**
- `backend/epictrace/media/mineru_provisioner.py` — 拆 `provision()`(只装包→`installed_no_models`)与新 `download_models()`(跑 `mineru-models-download`→`ready`);新增模型 marker 检查 `_models_ready()`;`is_ready()` = bin + 模型;`state` 扩展五态 + downloading;新增 `_models_runner` 注入点。
- `backend/epictrace/config.py` — 新增 `model_source` 取值约束注释(不改默认值);保持 `extraction_effort`/`model_source`/`extraction_timeout` 作为「无持久化设置时」的回退。
- `backend/epictrace/services/settings.py` — 新增 `get_extraction_settings()` / `set_extraction_settings(...)`(写入 `settings.json` 的 `extraction` 对象 + 校验);`extraction_status()` 透传扩展后的 `state`/`ready`/`error`。
- `backend/epictrace/media/__init__.py` — `_rich_processors(config)` 改为读 `SettingsService.get_extraction_settings()` 的 effort/model_source(无 → 回退 `AppConfig` 默认)。
- `backend/epictrace/schemas.py` — 新增 `ExtractionSettingsIn`/`ExtractionSettingsOut`;`ExtractionStatusOut` 注释补新状态(字段不变)。
- `backend/epictrace/api/routers/settings.py` — 新增 `GET/PUT /extraction/settings`、`POST /extraction/download-models`;`extraction/status` 已透传新 state(无需改逻辑)。
- `backend/epictrace/services/references.py` — `add_external` 在提取前接入「无模型→先下模型(进度走 progress_cb)」;`ReferenceService.__init__` 新增 `provisioner` 参数。
- `backend/epictrace/api/routers/references.py` — 给流式/非流式 `ReferenceService` 注入 `get_provisioner(request)`。
- `backend/epictrace/services/index.py` — `IndexService.__init__` 新增 `provisioner` 参数;`_run` 在首个目标文件前确保模型就绪(下载进度写进 `IndexJob`)。
- `backend/epictrace/api/routers/projects.py` — `index_project`/`reindex_project` 构造 `IndexService` 时注入 `get_provisioner(request)`。
- `frontend/src/lib/api.ts` — 新增 `getExtractionSettings`/`putExtractionSettings`/`downloadModels`;`ExtractionStatus.state` 联合类型加两态;新增 `ExtractionSettings` 接口。
- `frontend/src/views/SettingsView.tsx` — `ExtractionSection` 改为引擎选择器壳(MinerU 默认选中)+ 条件渲染状态徽标(新态)、安装、下载模型(进度)、effort 下拉、model_source 下拉。

**测试:**
- `backend/tests/test_mineru_provisioner.py` — 加 `provision`→`installed_no_models`(不下模型)、`download_models`→`ready`、`is_ready` 的模型检查、并发不可重入。
- `backend/tests/test_settings.py` — 加 `get/set_extraction_settings` + 校验。
- `backend/tests/test_media_docs.py` — 加 registry 读持久化 effort/model_source。
- `backend/tests/test_extraction_api.py` — 加 get/put settings、download-models、status 扩展状态。
- `backend/tests/test_api_references.py` — 加「无模型发文件 → 自动下载带进度 → 再提取」。
- `backend/tests/test_index_service.py` — 加「索引无模型 → 自动下载 → 提取」。

---

## Task 1: provisioner 拆分 + 状态机(装包不再下模型)

把 `provision()` 收敛为「只装包」,完成态从 `ready` 变 `installed_no_models`;模型检查移到独立 marker。

**Files:**
- Modify: `backend/epictrace/media/mineru_provisioner.py`
- Test: `backend/tests/test_mineru_provisioner.py`

- [ ] **Step 1: 改既有「装包即就绪」断言为「装包→installed_no_models」**

把 `test_provision_runs_uv_commands_and_becomes_ready` 与 `test_state_is_installing_while_provisioning` 的 fake `uv_runner` 中「创建 bin/mineru 使 is_ready 转真」这一隐含语义改成「装包后 state 为 `installed_no_models`、`is_ready()` 仍为 False」。替换这两个测试为下列内容(其余保留)。

把 `backend/tests/test_mineru_provisioner.py` 里的 `test_provision_runs_uv_commands_and_becomes_ready` 整段替换为:

```python
def test_provision_installs_packages_only_not_models(tmp_path: Path):
    """provision 只装包:完成态是 installed_no_models(尚未下模型),is_ready 仍 False。"""
    venv = _venv_dir(tmp_path)
    calls: list[list[str]] = []

    def uv_runner(cmd, timeout):
        calls.append(cmd)
        # 模拟 `uv venv` 创建 bin/mineru 可执行(包就绪);模型仍未下。
        if "venv" in cmd:
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "mineru").write_text("#!/bin/sh\n")
            (venv / "bin" / "mineru").chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    progress: list[str] = []
    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", uv_runner=uv_runner)
    p.provision(progress_cb=progress.append)

    assert p.state == "installed_no_models"
    assert p.is_ready() is False  # 包装好但模型未下 → 未就绪
    # 第一条:uv venv --python 3.11 <venv>
    assert calls[0][:1] == ["/usr/local/bin/uv"]
    assert "venv" in calls[0]
    assert "--python" in calls[0] and "3.11" in calls[0]
    assert str(venv) in calls[0]
    # 第二条:uv pip install "mineru[all]" (into the venv)
    assert "pip" in calls[1] and "install" in calls[1]
    assert any("mineru[all]" in c for c in calls[1])
    # provision 绝不跑模型下载子进程
    assert not any("mineru-models-download" in " ".join(c) for c in calls)
    assert len(progress) >= 1  # 粗粒度进度回调
```

把 `test_state_is_installing_while_provisioning` 末尾的 `assert p.state == "ready"` 改为 `assert p.state == "installed_no_models"`。

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_mineru_provisioner.py::test_provision_installs_packages_only_not_models -q`
预期 FAIL(当前 `provision` 完成后 `state == "ready"`,因为旧 `is_ready()` 只看 bin)。

- [ ] **Step 3: 改 provisioner —— 拆模型 marker + state 扩展**

把 `backend/epictrace/media/mineru_provisioner.py` 顶部注入点与超时常量改为(在 `_PROVISION_TIMEOUT` 下方新增下载超时):

```python
# 注入点:默认用 subprocess.run;测试传假 uv_runner / models_runner,完全不碰真 uv/mineru/网络。
UvRunner = Callable[[list[str], int], subprocess.CompletedProcess]
ModelsRunner = Callable[[list[str], int], subprocess.CompletedProcess]

# provision 阶段较长(建环境 + 装几 GB 依赖);给宽松超时。
_PROVISION_TIMEOUT = 3600
# 模型下载(几 GB)同样给宽松超时。
_DOWNLOAD_TIMEOUT = 3600
```

把 `__init__` 签名与字段改为(新增 `models_runner` 注入 + downloading 守卫):

```python
    def __init__(
        self,
        venv_dir: Path,
        *,
        uv_bin: str | None = None,
        uv_runner: UvRunner | None = None,
        models_runner: ModelsRunner | None = None,
    ) -> None:
        self._venv_dir = Path(venv_dir)
        self._uv_bin = uv_bin
        self._uv_runner = uv_runner or _default_uv_runner
        self._models_runner = models_runner or _default_uv_runner
        self._failed = False
        # 并发守卫:_installing / _downloading 各自一个标志 + 共用锁。安装/下载中重复触发
        # no-op;state() 据标志暴露 "installing"/"downloading_models"(前端徽标依赖它)。
        self._installing = False
        self._downloading = False
        self._lock = threading.Lock()
        self.last_error: str | None = None
```

在 `mineru_bin()` 下方新增模型路径与 marker(模型下到 venv 内的 huggingface/modelscope 缓存,marker 用 `mineru.json`;实现期对着 pin 的 MinerU 版本确认确切路径,这里用「`models_dir` 下存在 `mineru.json` 且其同级模型目录非空」为准):

```python
    def models_dir(self) -> Path:
        # mineru-models-download 把权重 + mineru.json 落到 venv 内的模型根目录。
        return self._venv_dir / "models"

    def _models_ready(self) -> bool:
        # marker:mineru.json 存在,且模型根目录里有至少一个非 mineru.json 的条目(模型权重)。
        root = self.models_dir()
        marker = root / "mineru.json"
        if not marker.is_file():
            return False
        try:
            return any(c.name != "mineru.json" for c in root.iterdir())
        except OSError:
            return False
```

把 `is_ready()` 与 `state` 改为:

```python
    def is_ready(self) -> bool:
        # 必须是真实文件(目录占位不算就绪)+ 模型已下;保持廉价,不起子进程探测。
        return Path(self.mineru_bin()).is_file() and self._models_ready()

    @property
    def state(self) -> str:
        # installing/downloading 优先于其余:对应线程活跃期间一律暴露该过渡态(前端轮询/徽标据此)。
        if self._installing:
            return "installing"
        if self._downloading:
            return "downloading_models"
        if self.is_ready():
            return "ready"
        if self._failed:
            return "failed"
        if Path(self.mineru_bin()).is_file():
            return "installed_no_models"  # 包就绪、模型未下
        return "not_installed"
```

把 `provision()` 的最后一段 `emit("安装完成")` 保留不变(它本就只装包),但确保其 docstring/注释不再暗示就绪;`provision` 完成后 `state` 自然变 `installed_no_models`(bin 存在、模型缺)。无需改 `provision` 主体逻辑。

- [ ] **Step 4: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_mineru_provisioner.py -q`
预期 PASS(含改写后的两个测试 + 其余原有测试;`test_not_ready_before_provision`、`test_is_ready_requires_a_file_not_a_directory`、`test_provision_failure_sets_failed_state` 仍绿)。

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/media/mineru_provisioner.py backend/tests/test_mineru_provisioner.py
git commit -m "Split MinerU provision into package install (installed_no_models) and model marker

provision() now installs packages only and lands in installed_no_models;
is_ready() additionally requires the model marker (mineru.json + non-empty
models dir). state machine extended with installed_no_models/downloading_models.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: provisioner —— `download_models()` 下模型 → ready

新增独立的模型下载步骤,跑 `<venv>/bin/mineru-models-download`,子进程可注入,完成后 `ready`,并发不可重入。

**Files:**
- Modify: `backend/epictrace/media/mineru_provisioner.py`
- Test: `backend/tests/test_mineru_provisioner.py`

- [ ] **Step 1: 写失败测试 —— download_models 跑下载命令并转 ready**

在 `backend/tests/test_mineru_provisioner.py` 末尾追加:

```python
def _install_only(venv: Path) -> "MinerUProvisioner":
    """造一个「装了包、未下模型」的 provisioner(bin 存在,models 缺)。"""
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "mineru").write_text("#!/bin/sh\n")
    (venv / "bin" / "mineru").chmod(0o755)
    (venv / "bin" / "mineru-models-download").write_text("#!/bin/sh\n")
    (venv / "bin" / "mineru-models-download").chmod(0o755)
    return MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv")


def test_download_models_runs_download_and_becomes_ready(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    p = _install_only(venv)
    assert p.state == "installed_no_models"
    calls: list[list[str]] = []

    def models_runner(cmd, timeout):
        calls.append(cmd)
        # 模拟下载:落 mineru.json + 一个模型目录,使 _models_ready 转真。
        root = venv / "models"
        root.mkdir(parents=True, exist_ok=True)
        (root / "mineru.json").write_text("{}")
        (root / "layout").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    progress: list[str] = []
    p.download_models(progress_cb=progress.append)

    assert p.state == "ready"
    assert p.is_ready() is True
    # 跑的是 venv 内的 mineru-models-download,且带 --source <model_source>
    assert calls[0][0] == str(venv / "bin" / "mineru-models-download")
    assert "--source" in calls[0]
    assert len(progress) >= 1


def test_download_models_takes_model_source(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    calls: list[list[str]] = []

    def models_runner(cmd, timeout):
        calls.append(cmd)
        root = venv / "models"; root.mkdir(parents=True, exist_ok=True)
        (root / "mineru.json").write_text("{}"); (root / "layout").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    p.download_models(model_source="huggingface")
    src_idx = calls[0].index("--source")
    assert calls[0][src_idx + 1] == "huggingface"


def test_download_models_failure_sets_failed(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)

    def models_runner(cmd, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="net down")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    with pytest.raises(RuntimeError):
        p.download_models()
    assert p.state == "failed"
    assert p.is_ready() is False
    assert p.last_error and "net down" in p.last_error


def test_duplicate_download_while_downloading_is_noop(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    release = threading.Event(); started = threading.Event()
    dl_calls = {"n": 0}

    def models_runner(cmd, timeout):
        dl_calls["n"] += 1
        started.set()
        release.wait(timeout=5)  # 卡住第一次下载,使其保持 downloading_models
        root = venv / "models"; root.mkdir(parents=True, exist_ok=True)
        (root / "mineru.json").write_text("{}"); (root / "layout").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    t = threading.Thread(target=p.download_models, daemon=True)
    t.start()
    assert started.wait(timeout=5)
    assert p.state == "downloading_models"
    p.download_models()  # 第二次必须立刻 no-op,不开第二次下载
    assert p.state == "downloading_models"
    release.set()
    t.join(timeout=5)
    assert p.state == "ready"
    assert dl_calls["n"] == 1  # 只跑了一次下载
```

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_mineru_provisioner.py -k download_models -q`
预期 FAIL（`AttributeError: 'MinerUProvisioner' object has no attribute 'download_models'`)。

- [ ] **Step 3: 实现 download_models**

在 `backend/epictrace/media/mineru_provisioner.py` 的 `provision` 与 `_run_or_fail` 之间新增方法。`download_models` 复用 `_run_or_fail` 的「runner→returncode」机制,但用 `_models_runner` + `_DOWNLOAD_TIMEOUT`:

```python
    def models_download_bin(self) -> str:
        return str(self._venv_dir / "bin" / "mineru-models-download")

    def download_models(
        self,
        *,
        model_source: str = "modelscope",
        progress_cb: Callable[[str], None] | None = None,
    ) -> str:
        """下模型(跑 <venv>/bin/mineru-models-download --source <model_source>)。返回结束时 state。

        前提:已 provision(包就绪)。并发安全:下载中再调 no-op(返回当前 state)。
        任何失败置 failed + last_error 再上抛(后台触发线程捕获)。"""
        with self._lock:
            if self._downloading:
                return self.state
            self._downloading = True
            self._failed = False
            self.last_error = None

        def emit(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        try:
            emit("正在下载模型(约数 GB,首次较久)…")
            cmd = [self.models_download_bin(), "--source", model_source]
            self._run_models_or_fail(cmd)
            emit("模型下载完成")
        except Exception as e:  # noqa: BLE001 — 任何失败都落到 failed + last_error
            with self._lock:
                self._failed = True
                self.last_error = str(e)[:500]
            _log.warning("MinerU model download failed: %s", e, exc_info=True)
            raise
        finally:
            with self._lock:
                self._downloading = False
        return self.state

    def _run_models_or_fail(self, cmd: list[str]) -> None:
        try:
            proc = self._models_runner(cmd, _DOWNLOAD_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"model download failed: {e}") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"mineru-models-download exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:500]}"
            )
```

- [ ] **Step 4: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_mineru_provisioner.py -q`
预期 PASS(全部 provisioner 测试绿)。

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/media/mineru_provisioner.py backend/tests/test_mineru_provisioner.py
git commit -m "Add MinerUProvisioner.download_models with injectable runner

download_models runs <venv>/bin/mineru-models-download --source <model_source>,
transitions installed_no_models -> downloading_models -> ready, is non-reentrant,
and records failed + last_error on failure.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: SettingsService —— 持久化 extraction 设置 + 校验

把 `extraction` 对象存进 `settings.json`,给 get/set + 校验。

**Files:**
- Modify: `backend/epictrace/services/settings.py`
- Test: `backend/tests/test_settings.py`

- [ ] **Step 1: 写失败测试 —— get 默认 / set 持久化 / 校验**

在 `backend/tests/test_settings.py` 末尾追加(沿用文件顶部已有的 `_svc(tmp_path)` 辅助):

```python
def test_extraction_settings_default(tmp_path: Path):
    svc = _svc(tmp_path)
    es = svc.get_extraction_settings()
    # 无持久化设置 → 回退 AppConfig 默认(engine=mineru, effort=medium, model_source=modelscope)。
    assert es == {"engine": "mineru", "effort": "medium", "model_source": "modelscope"}


def test_set_extraction_settings_persists_and_roundtrips(tmp_path: Path):
    svc = _svc(tmp_path)
    out = svc.set_extraction_settings(engine="mineru", effort="high", model_source="huggingface")
    assert out == {"engine": "mineru", "effort": "high", "model_source": "huggingface"}
    # 新建一个 service 读盘:持久化生效。
    assert _svc(tmp_path).get_extraction_settings() == {
        "engine": "mineru", "effort": "high", "model_source": "huggingface",
    }
    # 与 profiles 共存:不互相覆盖。
    svc.create_profile(name="A", base_url="http://x", api_key="k", model="m")
    assert _svc(tmp_path).get_extraction_settings()["effort"] == "high"
    assert _svc(tmp_path).public_view()["configured"] is True


def test_set_extraction_settings_rejects_bad_effort(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        _svc(tmp_path).set_extraction_settings(engine="mineru", effort="ultra",
                                               model_source="modelscope")


def test_set_extraction_settings_rejects_bad_model_source(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        _svc(tmp_path).set_extraction_settings(engine="mineru", effort="medium",
                                               model_source="s3")


def test_set_extraction_settings_rejects_bad_engine(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        _svc(tmp_path).set_extraction_settings(engine="docling", effort="medium",
                                               model_source="modelscope")
```

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_settings.py -k extraction_settings -q`
预期 FAIL（`AttributeError: 'SettingsService' object has no attribute 'get_extraction_settings'`)。

- [ ] **Step 3: 实现 get/set_extraction_settings**

在 `backend/epictrace/services/settings.py`,先在文件靠上(类外、`_short_id` 下方)加校验常量:

```python
_VALID_EFFORT = {"high", "medium"}
_VALID_MODEL_SOURCE = {"modelscope", "huggingface", "local"}
_VALID_ENGINE = {"mineru"}
```

在 `SettingsService` 内、`extraction_status()` 上方新增两个方法(`get` 回退 `AppConfig` 默认;`set` 校验后只改 `extraction` 键、保留其余键):

```python
    def get_extraction_settings(self) -> dict:
        """{engine, effort, model_source}。无持久化 → 回退 AppConfig 默认。"""
        data = self._read_raw()
        ext = data.get("extraction")
        if not isinstance(ext, dict):
            ext = {}
        return {
            "engine": ext.get("engine", "mineru"),
            "effort": ext.get("effort", self._config.extraction_effort),
            "model_source": ext.get("model_source", self._config.model_source),
        }

    def set_extraction_settings(
        self, *, engine: str, effort: str, model_source: str
    ) -> dict:
        """校验后持久化 extraction 对象,返回更新后的设置。非法值 → ValueError。"""
        if engine not in _VALID_ENGINE:
            raise ValueError(f"invalid engine: {engine}")
        if effort not in _VALID_EFFORT:
            raise ValueError(f"invalid effort: {effort}")
        if model_source not in _VALID_MODEL_SOURCE:
            raise ValueError(f"invalid model_source: {model_source}")
        # 只改 extraction;profiles/active_profile_id 等其余键原样保留(读 raw,不经 _load 归一)。
        data = self._read_raw()
        data["extraction"] = {
            "engine": engine, "effort": effort, "model_source": model_source,
        }
        self._write(data)
        return self.get_extraction_settings()
```

把 `extraction_status()` 改为透传 `last_error`(API/前端要新状态 + 错误):

```python
    def extraction_status(self) -> dict:
        """高质量提取引擎(MinerU)的 provisioning 状态。"""
        prov = MinerUProvisioner(self._config.mineru_venv_dir)
        return {
            "state": prov.state,
            "ready": prov.is_ready(),
            "error": prov.last_error,
        }
```

注:`_write` 写的是它收到的 `data`。当 `set_extraction_settings` 用 `_read_raw()`(可能含旧形状 `chat_llm` 或已归一的 `profiles`)时直接挂 `extraction` 键再写回,不破坏既有键。`test_extraction_status_reports_state` 现仍只断言 `state`/`ready`,新增的 `error` 键不影响它。

- [ ] **Step 4: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_settings.py -q`
预期 PASS(新增 5 个 + 既有全绿,含 `test_extraction_status_reports_state`)。

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/services/settings.py backend/tests/test_settings.py
git commit -m "Persist extraction settings (engine/effort/model_source) in settings.json

SettingsService gains get/set_extraction_settings with validation (effort in
{high,medium}, model_source in {modelscope,huggingface,local}, engine=mineru);
get falls back to AppConfig defaults. extraction_status now carries last_error.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: registry/runner 读持久化设置

`get_processor` 构造 `MinerUMediaProcessor` 时用 `SettingsService.get_extraction_settings()` 的 effort/model_source(无 → 回退 `AppConfig`)。

**Files:**
- Modify: `backend/epictrace/media/__init__.py`
- Test: `backend/tests/test_media_docs.py`

- [ ] **Step 1: 写失败测试 —— registry 用持久化 effort/model_source**

把 `backend/tests/test_media_docs.py` 的 `test_rich_processor_uses_config_extraction_effort` 替换为下列两个测试(默认 + 持久化覆盖):

```python
def test_rich_processor_defaults_to_config_when_no_persisted(tmp_path: Path):
    # 无持久化 extraction 设置 → 回退 AppConfig 默认(effort=medium, model_source=modelscope)。
    proc = get_processor(tmp_path / "a.pdf", AppConfig(data_dir=tmp_path))
    assert isinstance(proc, MinerUMediaProcessor)
    assert proc._effort == "medium"
    assert proc._model_source == "modelscope"


def test_rich_processor_uses_persisted_extraction_settings(tmp_path: Path):
    # 持久化了 effort=high / model_source=huggingface → registry 据此构造处理器。
    from epictrace.services.settings import SettingsService
    cfg = AppConfig(data_dir=tmp_path)
    SettingsService(cfg).set_extraction_settings(
        engine="mineru", effort="high", model_source="huggingface")
    proc = get_processor(tmp_path / "a.pdf", cfg)
    assert isinstance(proc, MinerUMediaProcessor)
    assert proc._effort == "high"
    assert proc._model_source == "huggingface"
```

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_media_docs.py::test_rich_processor_uses_persisted_extraction_settings -q`
预期 FAIL(当前 `_rich_processors` 只读 `config.extraction_effort`/`model_source`,忽略持久化设置 → `_effort == "medium"`)。

- [ ] **Step 3: 改 `_rich_processors` 读持久化设置**

把 `backend/epictrace/media/__init__.py` 的 `_rich_processors` 改为:

```python
def _rich_processors(config: AppConfig) -> list[MediaProcessor]:
    # 引入放函数内,避免顶层 import 形成 settings ↔ media 循环依赖。
    from epictrace.services.settings import SettingsService

    provisioner = MinerUProvisioner(config.mineru_venv_dir)
    ext = SettingsService(config).get_extraction_settings()
    return [
        MinerUMediaProcessor(
            provisioner,
            model_source=ext["model_source"],
            timeout=getattr(config, "extraction_timeout", 600),
            effort=ext["effort"],
        )
    ]
```

注:`SettingsService(config).get_extraction_settings()` 在无持久化时回退 `config.extraction_effort`/`config.model_source`,故默认行为与原来一致(`test_rich_processor_defaults_to_config_when_no_persisted` 仍绿)。`SettingsService` 顶部已 import `MinerUProvisioner`,但不 import `media`,反向 import 在函数内做以防循环。

- [ ] **Step 4: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_media_docs.py -q`
预期 PASS(含两个新测试 + `test_rich_doc_slots_are_mineru_not_python_processors`、`test_unknown_type_returns_none`)。

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/media/__init__.py backend/tests/test_media_docs.py
git commit -m "Read persisted extraction settings when building MinerU processor

get_processor now constructs MinerUMediaProcessor with effort/model_source from
SettingsService.get_extraction_settings (falling back to AppConfig defaults when
no persisted extraction settings exist).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: API —— GET/PUT settings、download-models、status 扩展

新增 `GET/PUT /api/extraction/settings`、`POST /api/extraction/download-models`;`status` 已透传新 state(`_provision_status` 读 `prov.state`)。

**Files:**
- Modify: `backend/epictrace/schemas.py`
- Modify: `backend/epictrace/api/routers/settings.py`
- Test: `backend/tests/test_extraction_api.py`

- [ ] **Step 1: 写失败测试 —— settings get/put + download-models + status 新态**

先扩展 `backend/tests/test_extraction_api.py` 顶部的 `_FakeProvisioner`,让它支持 `download_models` + 三态(注意:`app_and_prov` fixture 会注入它,故现有测试不能退化)。把文件顶部 `_FakeProvisioner` 整段替换为:

```python
class _FakeProvisioner:
    def __init__(self):
        self._installed = False
        self._models = False
        self.provisioned = threading.Event()
        self.downloaded = threading.Event()
        self.last_error = None

    def is_ready(self):
        return self._installed and self._models

    @property
    def state(self):
        if self._installed and self._models:
            return "ready"
        if self._installed:
            return "installed_no_models"
        return "not_installed"

    def provision(self, progress_cb=None):
        self._installed = True
        self.provisioned.set()

    def download_models(self, *, model_source="modelscope", progress_cb=None):
        self._models = True
        self.downloaded.set()
```

在 `backend/tests/test_extraction_api.py` 末尾追加:

```python
def test_status_reports_installed_no_models(app_and_prov):
    client, prov = app_and_prov
    prov.provision()  # 包装好、模型未下
    body = client.get("/api/extraction/status").json()
    assert body["state"] == "installed_no_models"
    assert body["ready"] is False


def test_get_extraction_settings_defaults(app_and_prov):
    client, _ = app_and_prov
    body = client.get("/api/extraction/settings").json()
    assert body == {"engine": "mineru", "effort": "medium", "model_source": "modelscope"}


def test_put_extraction_settings_persists(app_and_prov):
    client, _ = app_and_prov
    r = client.put("/api/extraction/settings",
                   json={"engine": "mineru", "effort": "high", "model_source": "huggingface"})
    assert r.status_code == 200
    assert r.json() == {"engine": "mineru", "effort": "high", "model_source": "huggingface"}
    # 持久化:再 GET 取到新值。
    assert client.get("/api/extraction/settings").json()["effort"] == "high"


def test_put_extraction_settings_rejects_bad_value(app_and_prov):
    client, _ = app_and_prov
    r = client.put("/api/extraction/settings",
                   json={"engine": "mineru", "effort": "ultra", "model_source": "modelscope"})
    assert r.status_code == 400


def test_download_models_kicks_off_and_becomes_ready(app_and_prov):
    client, prov = app_and_prov
    prov.provision()  # 先装包
    r = client.post("/api/extraction/download-models")
    assert r.status_code == 200
    assert prov.downloaded.wait(timeout=5)  # 后台线程跑完
    assert client.get("/api/extraction/status").json()["ready"] is True
```

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_extraction_api.py -q`
预期 FAIL(`/api/extraction/settings`、`/api/extraction/download-models` 路由不存在 → 404）。

- [ ] **Step 3: 加 schemas**

在 `backend/epictrace/schemas.py` 的 `ExtractionStatusOut` 上方(或下方)新增:

```python
class ExtractionSettingsIn(BaseModel):
    engine: str = "mineru"
    effort: str
    model_source: str


class ExtractionSettingsOut(BaseModel):
    engine: str
    effort: str
    model_source: str
```

把 `ExtractionStatusOut` 的注释更新(字段不变):

```python
class ExtractionStatusOut(BaseModel):
    # not_installed | installing | installed_no_models | downloading_models | ready | failed
    state: str
    ready: bool
    error: str | None = None
```

- [ ] **Step 4: 加路由**

在 `backend/epictrace/api/routers/settings.py` 顶部 import 加 `HTTPException`、新 schema 与 `SettingsService` 已 import。把 import 段补成:

```python
from fastapi import APIRouter, HTTPException, Request
```

并在 schemas import 块里加入:

```python
    ExtractionSettingsIn,
    ExtractionSettingsOut,
    ExtractionStatusOut,
```

在文件末尾(`extraction_provision` 下方)新增三个端点(`status` 不动,`_provision_status` 已读 `prov.state` 透传新态):

```python
@router.get("/extraction/settings", response_model=ExtractionSettingsOut)
def get_extraction_settings(request: Request):
    return _svc(request).get_extraction_settings()


@router.put("/extraction/settings", response_model=ExtractionSettingsOut)
def put_extraction_settings(payload: ExtractionSettingsIn, request: Request):
    try:
        return _svc(request).set_extraction_settings(
            engine=payload.engine,
            effort=payload.effort,
            model_source=payload.model_source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/extraction/download-models", response_model=ExtractionStatusOut)
def extraction_download_models(request: Request):
    """触发模型下载(后台线程,粗粒度状态)。立即返回当前状态;前端轮询 status。

    机制同 extraction_provision:后台线程吞掉上抛异常(失败状态/last_error 由
    provisioner 记录);重复触发由 provisioner 内部并发守卫 no-op。model_source 取
    持久化设置(无 → AppConfig 默认)。"""
    prov = get_provisioner(request)
    model_source = _svc(request).get_extraction_settings()["model_source"]

    def _run():
        try:
            prov.download_models(model_source=model_source)
        except Exception:  # noqa: BLE001 — 失败状态/last_error 已由 provisioner 记录
            pass

    threading.Thread(target=_run, daemon=True).start()
    return _provision_status(prov)
```

- [ ] **Step 5: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_extraction_api.py -q`
预期 PASS(含新增 5 个 + 既有 `test_status_reports_not_installed`、`test_provision_kicks_off_and_becomes_ready`)。

- [ ] **Step 6: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/schemas.py backend/epictrace/api/routers/settings.py backend/tests/test_extraction_api.py
git commit -m "Add extraction settings GET/PUT and download-models endpoints

GET/PUT /api/extraction/settings round-trips engine/effort/model_source (400 on
invalid). POST /api/extraction/download-models kicks off background download using
the persisted model_source. status now surfaces the extended provisioner states.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 无模型发文件(挂附件)→ 自动下载带进度 → 再提取

`ReferenceService.add_external` 提取前检查 provisioner:`installed_no_models` 先 `download_models`(进度走 `progress_cb`),再提取;`ready` 直接提取;`not_installed` 沿用既有失败路径。

**Files:**
- Modify: `backend/epictrace/services/references.py`
- Modify: `backend/epictrace/api/routers/references.py`
- Test: `backend/tests/test_api_references.py`

- [ ] **Step 1: 写失败测试 —— 流式挂附件,无模型时先下载再提取**

在 `backend/tests/test_api_references.py` 末尾追加。用注入 `app.state.provisioner` 的假件(初始 `installed_no_models`,`download_models` 后转 `ready`),并 monkeypatch `get_processor` 让提取仅在「就绪」时返回文本:

```python
def test_stream_attach_downloads_models_then_extracts(client, tmp_path: Path, monkeypatch):
    """无模型(installed_no_models)发文件:先经 status 进度报「下载模型」,下完转 ready,再提取出 done。"""
    _, cid = _project_conv(client, tmp_path)
    f = tmp_path / "paper.pdf"; f.write_bytes(b"%PDF")

    class _Prov:
        def __init__(self):
            self._ready = False
        @property
        def state(self):
            return "ready" if self._ready else "installed_no_models"
        def is_ready(self):
            return self._ready
        def download_models(self, *, model_source="modelscope", progress_cb=None):
            if progress_cb:
                progress_cb("正在下载模型(约数 GB,首次较久)…")
            self._ready = True

    prov = _Prov()
    client.app.state.provisioner = prov

    class _Proc:
        def supports(self, _p):
            return True
        def process(self, _p, *, progress_cb=None, cancel=None):
            progress_cb and progress_cb("解析中 1/1")
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr("epictrace.services.references.get_processor",
                        lambda p, config: _Proc())

    with client.stream("POST", f"/api/conversations/{cid}/references/stream",
                       json={"kind": "external", "source_path": str(f)}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    events = _sse_events(body)
    statuses = [d for e, d in events if e == "status"]
    # 先有下载模型的进度,再有提取进度。
    assert any("下载模型" in s for s in statuses)
    assert "解析中 1/1" in statuses
    assert prov.is_ready() is True  # 下载已发生
    done = [d for e, d in events if e == "done"]
    assert len(done) == 1
```

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_api_references.py::test_stream_attach_downloads_models_then_extracts -q`
预期 FAIL(当前 `add_external` 不接收/不使用 provisioner,无「下载模型」进度;且未就绪时 `MinerUMediaProcessor.process` 会抛 `ExtractionEngineNotReady` → error 事件)。

- [ ] **Step 3: 给 ReferenceService 注入 provisioner 并在提取前确保模型就绪**

把 `backend/epictrace/services/references.py` 的 `__init__` 与 `add_external` 改为(新增 `provisioner` 参数 + 提取前的 `_ensure_models` 调用):

```python
    def __init__(self, db: Database, embedder=None, attachment_store=None,
                 provisioner=None) -> None:
        self._db = db
        self._embedder = embedder
        self._attachment_store = attachment_store
        self._provisioner = provisioner
```

在 `add_external` 中,把现有 `proc = get_processor(...)` 之后、`proc.process(...)` 之前插入「确保模型就绪」一步:

```python
    def add_external(self, conversation_id: int, path: str, context_window: int,
                     progress_cb=None, cancel=None) -> dict:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise ValueError("file not found")
        proc = get_processor(p, self._db.config)
        if proc is None:
            raise ValueError("unsupported file type")
        # 富文档(MinerU)且「装了包但没下模型」→ 先下模型(进度走同一 progress_cb 通道),
        # 下完再提取。not_installed / ready 都不在此处理:not_installed 由 process()
        # 抛 ExtractionEngineNotReady,ready 直接提取。
        self._ensure_models_ready(progress_cb)
        try:
            result = proc.process(p, progress_cb=progress_cb, cancel=cancel)
        except Exception as e:  # noqa: BLE001 — 提取失败转成可读的 400(由路由映射)
            raise ValueError(f"extract failed: {e}")
        # …（其余原样不变:text/used/mode/落库/provenance/索引）…
```

在类内(`add_external` 上方)新增辅助方法:

```python
    def _ensure_models_ready(self, progress_cb=None) -> None:
        """provisioner 为 installed_no_models 时先下模型(进度走 progress_cb)。
        无 provisioner / 非该状态 → no-op(ready 直接提取;not_installed 由 process 抛错)。"""
        prov = self._provisioner
        if prov is None:
            return
        if getattr(prov, "state", None) == "installed_no_models":
            ext = SettingsService(self._db.config).get_extraction_settings()
            prov.download_models(model_source=ext["model_source"], progress_cb=progress_cb)
```

并在文件顶部 import 段加入:

```python
from epictrace.services.settings import SettingsService
```

- [ ] **Step 4: 在 references 路由注入 provisioner**

把 `backend/epictrace/api/routers/references.py` 顶部 import 加 `get_provisioner`:

```python
from epictrace.api.deps import get_db, get_embedder, get_attachment_store, get_provisioner
```

在三处构造 `ReferenceService` 时传入 provisioner。`add_reference`(非流式):

```python
    svc = ReferenceService(db, embedder=_Lazy(lambda: get_embedder(request)),
                           attachment_store=_Lazy(lambda: get_attachment_store(request)),
                           provisioner=get_provisioner(request))
```

`add_reference_stream`(流式):

```python
    svc = ReferenceService(db, embedder=_Lazy(lambda: get_embedder(request)),
                           attachment_store=_Lazy(lambda: get_attachment_store(request)),
                           provisioner=get_provisioner(request))
```

`detach_reference` 不涉及提取,保持不变(不传 provisioner)。

- [ ] **Step 5: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_api_references.py -q`
预期 PASS(新增 1 个 + 既有 SSE/非流式测试全绿。既有测试 monkeypatch 了 `get_processor` 但未注入 provisioner;`_ensure_models_ready` 在 `provisioner is None` 时 no-op,故不受影响。`test_add_external_reference_and_list` 等用真 `get_provisioner` 懒构造一个 not_installed 的真 provisioner,`_ensure_models_ready` 对其也 no-op,处理 `.md` 走 `TextMediaProcessor` 不碰 MinerU)。

- [ ] **Step 6: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/services/references.py backend/epictrace/api/routers/references.py backend/tests/test_api_references.py
git commit -m "Auto-download MinerU models before extraction when packages installed

ReferenceService takes a provisioner; add_external downloads models (progress via
the same progress_cb / SSE status channel) when state is installed_no_models, then
extracts. references routes inject get_provisioner.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 无模型发文件(项目索引)→ 自动下载 → 提取

`IndexService` 同理:`_run` 处理首个目标前确保模型就绪,下载进度写进 `IndexJob`(项目索引的进度通道)。

**Files:**
- Modify: `backend/epictrace/services/index.py`
- Modify: `backend/epictrace/api/routers/projects.py`
- Test: `backend/tests/test_index_service.py`

- [ ] **Step 1: 写失败测试 —— 索引无模型时先下载再提取**

先看 `backend/tests/test_index_service.py` 现有构造方式(`IndexService(db, embedder, store)`),沿用同款 fake。在文件末尾追加:

```python
def test_index_downloads_models_when_installed_no_models(tmp_path, monkeypatch):
    """项目索引时 provisioner 为 installed_no_models → 先 download_models 再提取;进度记进 job.errors? 否:
    用 provisioner.downloaded 事件断言下载发生,且文件最终被索引(job.status==done)。"""
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.interfaces.media import MediaResult
    from epictrace.models import IngestRecord, Project
    from epictrace.services.index import IndexService
    from tests.fakes import FakeEmbedder, FakeVectorStore

    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    # 造一个项目 + 一个待索引的 pdf 记录。
    src = tmp_path / "a.pdf"; src.write_bytes(b"%PDF")
    with db.session() as s:
        proj = Project(title="P", folder_path=str(tmp_path)); s.add(proj); s.flush()
        rec = IngestRecord(project_id=proj.id, original_filename="a.pdf",
                           stored_path=str(src), content_hash="h", size_bytes=4,
                           mtime=0.0, ingest_method="file_direct", description="",
                           indexed=False)
        s.add(rec); s.flush()
        pid = proj.id

    class _Prov:
        def __init__(self):
            self._ready = False
            self.downloaded = False
        @property
        def state(self):
            return "ready" if self._ready else "installed_no_models"
        def is_ready(self):
            return self._ready
        def download_models(self, *, model_source="modelscope", progress_cb=None):
            self.downloaded = True
            self._ready = True

    prov = _Prov()

    class _Proc:
        def supports(self, _p):
            return True
        def process(self, _p, *, progress_cb=None, cancel=None):
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={})

    monkeypatch.setattr("epictrace.services.index.get_processor",
                        lambda p, config: _Proc())

    svc = IndexService(db, FakeEmbedder(), FakeVectorStore(), provisioner=prov)
    job = svc.index_project(pid)
    svc._run(job)  # 同步跑,确定性

    assert prov.downloaded is True
    assert job.status == "done"
    assert job.done == 1
```

注:`get_processor` 在 `index_project` 的「算 targets」阶段也被调用(判断 `is not None`);此处 monkeypatch 的是 `epictrace.services.index.get_processor`,两处都命中,故 `_Proc` 既参与选路又参与提取。

- [ ] **Step 2: 运行测试,预期 FAIL**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_index_service.py::test_index_downloads_models_when_installed_no_models -q`
预期 FAIL(`IndexService.__init__` 不接收 `provisioner` → `TypeError`)。

- [ ] **Step 3: 给 IndexService 注入 provisioner 并在 `_run` 提取前确保模型**

把 `backend/epictrace/services/index.py` 的 `__init__` 改为接收 `provisioner`:

```python
    def __init__(self, db: Database, embedder: EmbeddingProvider, vector_store,
                 provisioner=None) -> None:
        # vector_store 可以是 VectorStore 实例,或返回它的可调用(getter)。
        # 用 getter 时,Milvus(gRPC)的构造会被推迟到 _run 里、在 warmup 之后,
        # 避免 'gRPC 激活后再加载模型' 段错误(macOS)。
        self._db = db
        self._embedder = embedder
        self._vector_store = vector_store
        self._provisioner = provisioner
```

在 `_run` 中,`self._embedder.warmup()` 与 `store = self._resolve_store()` 之间(即首个目标提取前),仅当有目标时确保模型就绪:

```python
    def _run(self, job: IndexJob) -> None:
        targets = getattr(job, "_targets", [])
        # 关键顺序:先加载模型(warmup),再构造/使用 Milvus(gRPC)。
        # 反过来(gRPC 已激活后再 fork 加载模型)会在 macOS 上段错误。
        self._embedder.warmup()
        # 有富文档要提取且 MinerU「装了包没下模型」→ 先下模型(进度记进 job.errors 之外的状态:
        # 这里把下载失败计入 errors,使该轮按既有失败路径呈现,不静默)。
        if targets:
            self._ensure_models_ready(job)
        store = self._resolve_store()
        # …（其余原样:for rec_id, path_str in targets 循环不变）…
```

在类内新增辅助方法(下载失败 → 记一条 job 级错误,不抛、不中断整轮;后续 per-file 提取若因模型缺失失败会再各自记 error):

```python
    def _ensure_models_ready(self, job: IndexJob) -> None:
        """provisioner 为 installed_no_models 时先下模型。下载失败记进 job.errors(不静默),
        不抛(让后续 per-file 提取按既有失败路径各自呈现)。无 provisioner / 非该状态 → no-op。"""
        prov = self._provisioner
        if prov is None:
            return
        if getattr(prov, "state", None) != "installed_no_models":
            return
        from epictrace.services.settings import SettingsService

        ext = SettingsService(self._db.config).get_extraction_settings()
        try:
            prov.download_models(model_source=ext["model_source"])
        except Exception as e:  # noqa: BLE001 — 失败不静默:记 job 级错误,后续 per-file 提取也会失败并各自记录
            with job._lock:
                job.errors.append(f"model download failed: {e}")
```

- [ ] **Step 4: 在 projects 路由注入 provisioner**

把 `backend/epictrace/api/routers/projects.py` 的两处 `IndexService(...)` 构造加 `get_provisioner(request)`。先确认顶部已 `from epictrace.api.deps import ... get_provisioner`(若未导入则加上)。`index_project`:

```python
    svc = IndexService(db, get_embedder(request), lambda: get_vector_store(request),
                       provisioner=get_provisioner(request))
```

`reindex_project`:

```python
    svc = IndexService(db, get_embedder(request), lambda: get_vector_store(request),
                       provisioner=get_provisioner(request))
```

- [ ] **Step 5: 运行测试,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest tests/test_index_service.py tests/test_api_index.py -q`
预期 PASS(新增 1 个 + 既有索引测试全绿;既有测试不传 provisioner → `_ensure_models_ready` no-op)。

- [ ] **Step 6: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add backend/epictrace/services/index.py backend/epictrace/api/routers/projects.py backend/tests/test_index_service.py
git commit -m "Auto-download MinerU models before project indexing when needed

IndexService takes a provisioner and downloads models once (before extracting any
file) when state is installed_no_models; download failures are recorded on the job
rather than swallowed. projects index/reindex routes inject get_provisioner.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 前端 —— 引擎选择器壳 + MinerU 旋钮(api.ts + SettingsView)

`api.ts` 加调用;`SettingsView` 的 `ExtractionSection` 改为引擎选择器壳 + 条件渲染:状态徽标(新态)、安装、下载模型(进度)、effort 下拉、model_source 下拉(改动即 PUT 持久化)。本任务无后端测试,以 `npm run build` 为绿灯门槛。

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/views/SettingsView.tsx`

- [ ] **Step 1: api.ts —— 扩展类型 + 新增三个调用**

把 `frontend/src/lib/api.ts` 的 `ExtractionStatus` 接口的 `state` 联合类型扩展,并在其下新增 `ExtractionSettings`:

```typescript
export interface ExtractionStatus {
  state:
    | "not_installed"
    | "installing"
    | "installed_no_models"
    | "downloading_models"
    | "ready"
    | "failed";
  ready: boolean;
  error?: string | null;
}
export interface ExtractionSettings {
  engine: "mineru";
  effort: "high" | "medium";
  model_source: "modelscope" | "huggingface" | "local";
}
```

在 `api` 对象里 `provisionExtraction` 之后追加三个调用:

```typescript
  getExtractionSettings: () =>
    fetch(`${BASE}/api/extraction/settings`).then(j<ExtractionSettings>),
  putExtractionSettings: (payload: ExtractionSettings) =>
    fetch(`${BASE}/api/extraction/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<ExtractionSettings>),
  downloadModels: () =>
    fetch(`${BASE}/api/extraction/download-models`, { method: "POST" }).then(j<ExtractionStatus>),
```

- [ ] **Step 2: 运行类型检查,预期 PASS(本步只动 api.ts)**

Run: `cd /Users/william/Desktop/EpicTrace/frontend && npm run build`
预期 exit 0(api.ts 自洽;SettingsView 尚未用新调用,无报错)。

- [ ] **Step 3: SettingsView —— 引擎选择器壳 + 旋钮**

把 `frontend/src/views/SettingsView.tsx` 顶部 import `ExtractionStatus` 的那行扩成同时引入 `ExtractionSettings`:

```typescript
import { api, type ExtractionSettings, type ExtractionStatus, type LLMProfile, type Settings } from "@/lib/api";
```

把 `STATE_LABEL` 常量(状态文案)扩成六态:

```typescript
const STATE_LABEL: Record<ExtractionStatus["state"], string> = {
  not_installed: "未安装",
  installing: "安装中",
  installed_no_models: "已安装·未下模型",
  downloading_models: "下载模型中",
  ready: "就绪",
  failed: "失败",
};
```

把整个 `ExtractionSection` 函数替换为下列实现(引擎选择器壳 + 条件渲染 MinerU 旋钮:状态徽标 / 安装 / 下载模型 / effort / model_source):

```tsx
const EFFORT_LABEL: Record<ExtractionSettings["effort"], string> = {
  high: "高(版面/表格/公式/OCR 全开,较慢)",
  medium: "中(默认,文本问答足够,更快)",
};
const SOURCE_LABEL: Record<ExtractionSettings["model_source"], string> = {
  modelscope: "ModelScope(国内更快)",
  huggingface: "HuggingFace",
  local: "本地(已自备模型)",
};

function ExtractionSection() {
  const [status, setStatus] = useState<ExtractionStatus | null>(null);
  const [settings, setSettings] = useState<ExtractionSettings | null>(null);
  const [busy, setBusy] = useState(false); // 安装/下载进行中
  const [savingField, setSavingField] = useState<null | "effort" | "model_source">(null);
  const [err, setErr] = useState<string | null>(null);

  // 进入页面:并行拉状态 + 设置。
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getExtractionStatus(), api.getExtractionSettings()])
      .then(([s, cfg]) => {
        if (cancelled) return;
        setStatus(s);
        setSettings(cfg);
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  // 安装中 / 下载中:轮询 status 直到 ready/failed/installed_no_models 静止态。
  const transient =
    busy ||
    status?.state === "installing" ||
    status?.state === "downloading_models";
  useEffect(() => {
    if (!transient) return;
    const t = setInterval(() => {
      api
        .getExtractionStatus()
        .then((s) => {
          setStatus(s);
          if (
            s.state === "ready" ||
            s.state === "failed" ||
            s.state === "installed_no_models"
          ) {
            setBusy(false);
            clearInterval(t);
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [transient]);

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

  const download = async () => {
    setBusy(true);
    setErr(null);
    try {
      setStatus(await api.downloadModels());
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  // effort / model_source 改动即持久化(乐观更新 + 失败回滚)。
  const update = async (patch: Partial<ExtractionSettings>) => {
    if (!settings) return;
    const prev = settings;
    const next: ExtractionSettings = { ...settings, ...patch };
    setSettings(next);
    setSavingField(("effort" in patch ? "effort" : "model_source") as "effort" | "model_source");
    setErr(null);
    try {
      setSettings(await api.putExtractionSettings(next));
    } catch (e) {
      setSettings(prev); // 回滚
      setErr(String(e));
    } finally {
      setSavingField(null);
    }
  };

  const state = status?.state;
  const installed = state === "installed_no_models" || state === "downloading_models" || state === "ready" || state === "failed";
  const ready = status?.ready === true;
  const installing = busy && !installed ? true : state === "installing";
  const downloading = state === "downloading_models" || (busy && installed && !ready);

  return (
    <section className="mt-10 flex flex-col gap-3 border-t border-border/60 pt-8">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-foreground">高质量提取</h2>
        <p className="text-xs leading-relaxed text-muted-foreground">
          用版面/表格/公式/OCR 引擎替代基础 PDF/DOCX/PPTX 提取。装包与下模型分两步,装完全本地运行。
        </p>
      </div>

      {/* 引擎选择器壳:当前唯一项 MinerU,默认选中。将来加引擎时此处扩为多项。 */}
      <Field id="ext-engine" label="提取引擎">
        <select
          id="ext-engine"
          value="mineru"
          disabled
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="mineru">MinerU</option>
        </select>
      </Field>

      {/* 选中 MinerU 之下的旋钮(条件渲染:engine === "mineru")。 */}
      <div className="flex items-center gap-3 rounded-xl border border-border/70 bg-muted/30 px-3 py-2.5">
        <span className="flex items-center gap-2 text-sm text-foreground">
          {(installing || downloading) && <Loader2 className="size-3.5 animate-spin" />}
          {ready && <CheckCircle2 className="size-3.5 text-primary" strokeWidth={2.25} />}
          {state === "failed" && <TriangleAlert className="size-3.5 text-destructive" />}
          状态:{status ? STATE_LABEL[status.state] : "…"}
        </span>
        <div className="ml-auto flex gap-2">
          {!installed && state !== "installing" && (
            <Button type="button" size="sm" disabled={installing} onClick={install}
                    title="安装高质量提取引擎(装包)">
              {installing ? (<><Loader2 className="size-3.5 animate-spin" />安装中…</>)
                : state === "failed" ? "重试安装" : "安装"}
            </Button>
          )}
          {installed && !ready && (
            <Button type="button" size="sm" disabled={downloading} onClick={download}
                    title="下载模型(约数 GB)">
              {downloading ? (<><Loader2 className="size-3.5 animate-spin" />下载中…</>)
                : state === "failed" ? "重试下载" : "下载模型"}
            </Button>
          )}
          {ready && (
            <Button type="button" variant="outline" size="sm" disabled={downloading}
                    onClick={download} title="按当前模型源重新下载模型">
              重新下载模型
            </Button>
          )}
        </div>
      </div>

      {/* effort / model_source 下拉:改动即 PUT 持久化。 */}
      {settings && (
        <div className="flex flex-col gap-3 rounded-xl border border-border/70 bg-muted/20 px-3 py-3">
          <Field id="ext-effort" label="解析力度(effort)">
            <select
              id="ext-effort"
              value={settings.effort}
              disabled={savingField !== null}
              onChange={(e) => update({ effort: e.target.value as ExtractionSettings["effort"] })}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            >
              {(["medium", "high"] as const).map((v) => (
                <option key={v} value={v}>{EFFORT_LABEL[v]}</option>
              ))}
            </select>
          </Field>
          <Field id="ext-source" label="模型源(model source)">
            <select
              id="ext-source"
              value={settings.model_source}
              disabled={savingField !== null}
              onChange={(e) => update({ model_source: e.target.value as ExtractionSettings["model_source"] })}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            >
              {(["modelscope", "huggingface", "local"] as const).map((v) => (
                <option key={v} value={v}>{SOURCE_LABEL[v]}</option>
              ))}
            </select>
          </Field>
          <p className="-mt-1 text-[0.7rem] leading-relaxed text-muted-foreground">
            换模型源后需手动「重新下载模型」才生效(不会自动重下)。
          </p>
        </div>
      )}

      {(err || status?.error) && (
        <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
          {err || status?.error}
        </p>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 运行前端构建,预期 PASS**

Run: `cd /Users/william/Desktop/EpicTrace/frontend && npm run build`
预期 exit 0(tsc + vite build 通过,无未用 import / 类型错误)。

- [ ] **Step 5: 提交**

```bash
cd /Users/william/Desktop/EpicTrace
git add frontend/src/lib/api.ts frontend/src/views/SettingsView.tsx
git commit -m "Add extraction engine selector with install/download/effort/source controls

SettingsView ExtractionSection becomes an engine-selector shell (MinerU default);
under it renders the extended status badge, Install (package), Download models
(progress), and effort/model_source dropdowns that PUT on change. api.ts gains
getExtractionSettings/putExtractionSettings/downloadModels and the extended state.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 验证 —— 全套测试 + 前端构建绿

最终门槛:后端全套 pytest 绿,前端 build exit 0。

**Files:** 无(只跑命令)。

- [ ] **Step 1: 后端全套测试**

Run: `cd /Users/william/Desktop/EpicTrace/backend && ./.venv/bin/pytest -q`
预期:全绿(0 failed)。若有 `test_index_real_smoke.py` 等需真模型/重资源的测试因环境跳过,确认是 skip 而非 fail。

- [ ] **Step 2: 前端构建**

Run: `cd /Users/william/Desktop/EpicTrace/frontend && npm run build`
预期:exit 0。

- [ ] **Step 3:(若前两步有偏差)修正后重跑**

仅当 Step 1/2 未全绿时:按 superpowers:systematic-debugging 定位、最小修正,再回到 Step 1 重跑直到全绿。无偏差则跳过。

---

## 自审清单(对照 spec §3–§10)

- **§3 状态机**:Task 1(`installed_no_models` + marker + state 扩展)、Task 2(`downloading_models`→`ready`)。`is_ready()` = bin + 模型 marker(`mineru.json` + 模型目录非空)✓。
- **§4 持久化设置**:Task 3(`get/set_extraction_settings` + 校验);Task 4(registry 读持久化,回退 AppConfig)✓。
- **§5 provisioner 改造**:Task 1(`provision` 只装包)、Task 2(`download_models(model_source, progress_cb)` 可注入 runner + 不可重入)✓。
- **§6 无模型发文件→自动下载带进度**:Task 6(挂附件 SSE,`installed_no_models`→先下模型走 `progress_cb`→提取;`ready` 直提;`not_installed` 由 `process` 抛 `ExtractionEngineNotReady`)、Task 7(项目索引,下载失败记 `job.errors` 不静默)✓。
- **§7 API**:Task 5(`GET/PUT /extraction/settings`、`POST /extraction/download-models`、`status` 透传新态 + `last_error`)✓。
- **§8 前端**:Task 8(引擎选择器壳 + 条件渲染状态徽标新态 / 安装 / 下载模型进度 / effort / model_source 改动即 PUT;换源提示需重下;`api.ts` 三调用)✓。
- **§9 错误处理**:下载失败→`failed`+`last_error`(Task 2);PUT 非法→400(Task 5);自动下载失败按既有失败路径(Task 6 ValueError→error 事件 / Task 7 job.errors)✓。
- **§10 测试策略**:全 fake(注入 `uv_runner`/`models_runner`/`get_processor`/假 provisioner),不碰真 uv/mineru/网络/下载 ✓。

**跨任务一致性**:`download_models(*, model_source, progress_cb)`、`get_extraction_settings()`/`set_extraction_settings(engine,effort,model_source)`、状态名(`installed_no_models`/`downloading_models`/`ready`/`failed`)、API 路由(`/api/extraction/settings`、`/api/extraction/download-models`、`/api/extraction/status`)在 backend 与 frontend 全程一致。

**判断点(实现期确认)**:模型 marker 的确切路径(`<venv>/models/mineru.json` + 同级非空)按 pin 的 MinerU 版本核实;`mineru-models-download` 的确切旗标(`--source`)按 pin 版本核实——若版本差异,在 Task 2 的 `download_models` 命令与 Task 1 的 `_models_ready` 同步调整(两处都改,保持一致)。

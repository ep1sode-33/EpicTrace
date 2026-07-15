# EpicTrace macOS 真·app 打包(Plan 11)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 dev 形态(run.sh + venv + pywebview)打包成签名+公证、双击即用的 `EpicTrace.app`:包内只放 Swift 启动器 + 重签 uv + wheel + lockfile + 前端 dist + 预编译音频 helper,首启由 uv 全托管供给 Python 3.11 + venv + 锁定依赖。

**Architecture:** 设计文档 `docs/superpowers/specs/2026-07-14-epictrace-macos-app-packaging-design.md`(动手前先通读)。运行时分两根:`~/Library/Application Support/EpicTrace/runtime/`(uv 托管 python + venv + 缓存,纯派生可删)与 `~/.epictrace`(data_dir,不动,dev/打包共用)。启动器保活为父进程(TCC 归因锚点),marker.json 决定走快速路径还是供给路径。

**Tech Stack:** Swift(AppKit 启动器,swiftc 直编无 Xcode 工程)· uv(pinned 0.11.28,内嵌)· Python 3.11(uv 托管,pinned 补丁版)· bash 打包脚本 · codesign/notarytool。

## Global Constraints

- 分支:`feat/plan-11-macos-app-packaging`(从 main 切;Task 1 第一步创建)。
- 后端测试:`cd backend && .venv/bin/pytest tests/<file> -v`(全量回归 `.venv/bin/pytest`)。
- 目标平台:macOS ≥ 14.4,arm64-only;bundle id `com.epictrace.app` **一经发布永不更改**。
- Pinned 版本:uv `0.11.28`;CPython 补丁版写在 `packaging/python-version`(单一事实源,内容 `3.11.15`)。
- 文档/代码注释简体中文;标识符/路径/命令英文;**不提任何前身原型代号**。
- `.app` 内容运行时**绝对只读**(写包 = 破签名 seal)。
- commit 信息风格照旧(中文短主题,`feat(scope): …`),每个 commit 尾部带既有约定的 Co-Authored-By/Claude-Session 段。
- **merge gate:William 真机测试通过并明说合并之前,绝不合 main**(Task 9 只开 PR)。

---

### Task 1: AppConfig 环境变量注入

**Files:**
- Modify: `backend/epictrace/config.py`
- Test: `backend/tests/test_config_env.py`(新建)

**Interfaces:**
- Produces: `AppConfig.frontend_dist: Path | None`、`AppConfig.uv_bin: str | None`、`AppConfig.packaged: bool`(均由环境变量 default_factory 注入,构造参数可覆盖);`EPICTRACE_DATA_DIR` 覆盖 `_default_data_dir()`。后续 Task 2/3/4 及 Swift 启动器(Task 5,设置这些环境变量)都依赖这组名字。

- [ ] **Step 1: 建分支**

```bash
cd /Users/william/Desktop/EpicTrace && git checkout -b feat/plan-11-macos-app-packaging
```

- [ ] **Step 2: 写失败测试**

新建 `backend/tests/test_config_env.py`:

```python
"""AppConfig 的 EPICTRACE_* 环境变量注入(打包启动器 → 壳/后端的传参通道)。"""
from pathlib import Path

from epictrace.config import AppConfig


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EPICTRACE_DATA_DIR", str(tmp_path / "dd"))
    cfg = AppConfig()
    assert cfg.data_dir == tmp_path / "dd"
    assert cfg.data_dir.is_dir()  # 与默认路径同语义:构造时确保存在


def test_data_dir_default_home(monkeypatch):
    monkeypatch.delenv("EPICTRACE_DATA_DIR", raising=False)
    assert AppConfig().data_dir == Path.home() / ".epictrace"


def test_packaging_fields_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("EPICTRACE_FRONTEND_DIST", str(tmp_path / "dist"))
    monkeypatch.setenv("EPICTRACE_UV_BIN", "/pkg/Resources/uv")
    monkeypatch.setenv("EPICTRACE_PACKAGED", "1")
    cfg = AppConfig()
    assert cfg.frontend_dist == tmp_path / "dist"
    assert cfg.uv_bin == "/pkg/Resources/uv"
    assert cfg.packaged is True


def test_packaging_fields_default_absent(monkeypatch):
    for k in ("EPICTRACE_FRONTEND_DIST", "EPICTRACE_UV_BIN", "EPICTRACE_PACKAGED"):
        monkeypatch.delenv(k, raising=False)
    cfg = AppConfig()
    assert cfg.frontend_dist is None
    assert cfg.uv_bin is None
    assert cfg.packaged is False
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_config_env.py -v`
Expected: FAIL(`AppConfig` has no attribute `frontend_dist` / data_dir 不吃 env)

- [ ] **Step 4: 实现**

`backend/epictrace/config.py` 顶部加 `import os`,替换 `_default_data_dir` 并新增 helper:

```python
import os


def _default_data_dir() -> Path:
    # 打包版启动器/干净账户测试可用 EPICTRACE_DATA_DIR 重定向;默认 ~/.epictrace,与 dev 共用
    # (设计决策 1:数据与模型零迁移)。
    override = os.environ.get("EPICTRACE_DATA_DIR")
    d = Path(override).expanduser() if override else Path.home() / ".epictrace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else None
```

`AppConfig` dataclass 里(`extraction_effort` 字段之后)追加三个字段:

```python
    # 打包(.app)注入通道:启动器设 EPICTRACE_* 环境变量 → 这里读入。dev 形态三者皆空:
    # 前端 dist 走仓库相对路径回退,uv 走 PATH,系统内录 helper 走 swiftc 懒编译。
    frontend_dist: Path | None = field(default_factory=lambda: _env_path("EPICTRACE_FRONTEND_DIST"))
    uv_bin: str | None = field(default_factory=lambda: os.environ.get("EPICTRACE_UV_BIN") or None)
    packaged: bool = field(default_factory=lambda: os.environ.get("EPICTRACE_PACKAGED") == "1")
```

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `cd backend && .venv/bin/pytest tests/test_config_env.py -v && .venv/bin/pytest -q`
Expected: 新测试 4 passed;全量无回归(既有测试用构造参数注入 config,不受 default_factory 影响)。

- [ ] **Step 6: Commit**

```bash
git add backend/epictrace/config.py backend/tests/test_config_env.py
git commit -m "feat(config): EPICTRACE_* 环境变量注入(data_dir/frontend_dist/uv_bin/packaged)"
```

---

### Task 2: 前端 dist 挂载走 config(打包白屏 → 响铃)

**Files:**
- Modify: `backend/epictrace/api/app.py:103-111`(create_app 末尾静态挂载段)
- Test: `backend/tests/test_frontend_dist_mount.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `AppConfig.frontend_dist`。
- Produces: `create_app(config=cfg)` 在 `cfg.frontend_dist` 存在时挂载它;显式注入但路径缺失时打日志且不挂载(GET / 404,不再静默走错误的相对路径)。

背景:现状 `dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"` 锚在仓库布局,wheel 安装后指向 `<venv>/lib/python3.11`,静默不挂载 → 打包版白屏。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_frontend_dist_mount.py`:

```python
"""前端静态资源挂载:config.frontend_dist 注入优先,缺失时不静默回退到错误路径。"""
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig


def _cfg(tmp_path, dist):
    dd = tmp_path / "dd"
    dd.mkdir(exist_ok=True)
    return AppConfig(data_dir=dd, frontend_dist=dist)


def test_mount_injected_frontend_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>packaged-ok</html>", encoding="utf-8")
    app = create_app(config=_cfg(tmp_path, dist))
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "packaged-ok" in r.text


def test_injected_dist_missing_no_mount(tmp_path):
    # 显式注入但目录不存在:不挂载(404),也不回退 dev 相对路径——打包错误要响铃可见。
    app = create_app(config=_cfg(tmp_path, tmp_path / "nope"))
    assert TestClient(app).get("/").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_frontend_dist_mount.py -v`
Expected: 第一条 FAIL(injected dist 被忽略,404);第二条可能恰好 PASS(dev dist 未 build 时)或 FAIL——以第一条为准。

- [ ] **Step 3: 实现**

`backend/epictrace/api/app.py` 把末尾挂载段(`import os` 到 `app.mount(...)`)替换为:

```python
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles

    # 前端静态资源:打包模式由启动器经 EPICTRACE_FRONTEND_DIST 注入(config.frontend_dist);
    # dev 回退仓库相对路径。注入了却不存在 = 打包错误,打日志且不挂载(404 可见),
    # 绝不回退错误路径静默白屏。复用 create_app 上文已算好的 app.state.config
    # (= config or db.config or AppConfig()),不再二次构造,避免与 app 其余部位的
    # config 来源分叉(测试注入 db 自带 config 时也一致)。
    cfg = app.state.config
    if cfg.frontend_dist is not None:
        dist = cfg.frontend_dist
        if not dist.exists():
            print(f"[EpicTrace] frontend dist not found: {dist}", flush=True)
    else:
        dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app
```

(原段落里的 `import os` 一并删除——不再使用。)

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `cd backend && .venv/bin/pytest tests/test_frontend_dist_mount.py -v && .venv/bin/pytest -q`
Expected: 2 passed;全量无回归。

- [ ] **Step 5: Commit**

```bash
git add backend/epictrace/api/app.py backend/tests/test_frontend_dist_mount.py
git commit -m "feat(api): 前端 dist 挂载走 config 注入,注入缺失响铃不静默白屏"
```

---

### Task 3: MinerU provisioner 接线 config.uv_bin

**Files:**
- Modify: `backend/epictrace/api/deps.py:141`
- Modify: `backend/epictrace/services/settings.py:256`(`extraction_status` 内)
- Modify: `backend/epictrace/media/__init__.py:38`(`_rich_processors` 内——注意:公开入口是 `get_processor(path, config)`,模块里**不存在** `get_media_processors`)
- Test: `backend/tests/test_mineru_uv_wiring.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `AppConfig.uv_bin`;`MinerUProvisioner.__init__(venv_dir, *, uv_bin=None, ...)`(已存在的注入口)。
- Produces: 三个构造点全部传 `uv_bin=<config.uv_bin>`;`uv_bin=None` 时 provisioner 内部照旧 `shutil.which("uv")`(dev 行为不变)。

背景:Finder 启动的 .app 进程 PATH 只有 `/usr/bin:/bin:/usr/sbin:/sbin`,`shutil.which("uv")` 必失败 → MinerU 供给直接 RuntimeError。打包模式 MinerU 与主环境共用包内同一个 uv。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_mineru_uv_wiring.py`:

```python
"""三个 MinerUProvisioner 构造点必须把 config.uv_bin 传进去(打包内置 uv;dev None → PATH)。"""
from types import SimpleNamespace

from epictrace.config import AppConfig


class _CaptureProv:
    """捕获构造参数的假 provisioner,满足各消费点用到的接口面。"""

    last_kwargs: dict = {}

    def __init__(self, venv_dir, **kwargs):
        type(self).last_kwargs = {"venv_dir": venv_dir, **kwargs}
        self.state = "not_installed"
        self.last_error = None
        self.failed_stage = None

    def is_ready(self):
        return False


def _cfg(tmp_path):
    dd = tmp_path / "dd"
    dd.mkdir(exist_ok=True)
    return AppConfig(data_dir=dd, uv_bin="/pkg/Resources/uv")


def test_settings_extraction_status_passes_uv_bin(tmp_path, monkeypatch):
    import epictrace.services.settings as mod

    monkeypatch.setattr(mod, "MinerUProvisioner", _CaptureProv)
    mod.SettingsService(_cfg(tmp_path)).extraction_status()
    assert _CaptureProv.last_kwargs["uv_bin"] == "/pkg/Resources/uv"


def test_deps_get_provisioner_passes_uv_bin(tmp_path, monkeypatch):
    from epictrace.api import deps

    monkeypatch.setattr(
        "epictrace.media.mineru_provisioner.MinerUProvisioner", _CaptureProv
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=_cfg(tmp_path))))
    deps.get_provisioner(request)
    assert _CaptureProv.last_kwargs["uv_bin"] == "/pkg/Resources/uv"


def test_rich_processors_pass_uv_bin(tmp_path, monkeypatch):
    import epictrace.media as media

    monkeypatch.setattr(media, "MinerUProvisioner", _CaptureProv)

    class _FakeSettings:
        def __init__(self, config):
            pass

        def get_extraction_settings(self):
            return {"engine": "mineru", "model_source": "modelscope", "effort": "medium"}

    monkeypatch.setattr("epictrace.services.settings.SettingsService", _FakeSettings)
    media._rich_processors(_cfg(tmp_path))  # engine=mineru 分支内即 :38 的构造点
    assert _CaptureProv.last_kwargs["uv_bin"] == "/pkg/Resources/uv"
```

注:`deps.get_provisioner` 优先读 `app.state.provisioner`,SimpleNamespace 上 getattr 缺省返回 AttributeError 安全兜底——`getattr(request.app.state, "provisioner", None)` 对 SimpleNamespace 缺属性返回 None,随后 `setattr` 也可行,无需真 FastAPI Request。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_mineru_uv_wiring.py -v`
Expected: 3 FAIL(`last_kwargs` 里没有 `uv_bin` 键,KeyError)。

- [ ] **Step 3: 实现(三处一行改动)**

`backend/epictrace/api/deps.py:141`:

```python
    prov = MinerUProvisioner(config.mineru_venv_dir, uv_bin=getattr(config, "uv_bin", None))
```

`backend/epictrace/services/settings.py:256`:

```python
        prov = MinerUProvisioner(
            self._config.mineru_venv_dir, uv_bin=getattr(self._config, "uv_bin", None)
        )
```

`backend/epictrace/media/__init__.py:38`:

```python
        provisioner = MinerUProvisioner(
            config.mineru_venv_dir, uv_bin=getattr(config, "uv_bin", None)
        )
```

(统一 `getattr(..., None)`:测试假 config 可能没有该字段,dev None → provisioner 内部照旧 which。)

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `cd backend && .venv/bin/pytest tests/test_mineru_uv_wiring.py -v && .venv/bin/pytest -q`
Expected: 3 passed;全量无回归。

- [ ] **Step 5: Commit**

```bash
git add backend/epictrace/api/deps.py backend/epictrace/services/settings.py backend/epictrace/media/__init__.py backend/tests/test_mineru_uv_wiring.py
git commit -m "feat(media): MinerU provisioner 三构造点接线 config.uv_bin(打包内置 uv)"
```

---

### Task 4: 壳搬进 epictrace.shell 包

**Files:**
- Create: `backend/epictrace/shell/__init__.py`(= 现 `shell/run.py` 全文 + 本任务的三处修改)
- Create: `backend/epictrace/shell/__main__.py`
- Move: `shell/native/SystemAudioCapture.swift` → `backend/epictrace/shell/native/SystemAudioCapture.swift`
- Move: `shell/native/build.sh` → `backend/epictrace/shell/native/build.sh`
- Delete: `shell/run.py`(及空目录 `shell/`)
- Modify: `backend/pyproject.toml`(package-data)
- Modify: `run.sh:51`
- Modify: `backend/tests/test_shell_reveal.py`(它按文件路径硬加载 `shell/run.py`,搬迁后必炸;改成 import)
- Modify: `README.md:74`、`README.md:102`(两处指向旧 `shell/` 路径)
- Test: `backend/tests/test_shell_packaging.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `AppConfig.packaged`。
- Produces: `python -m epictrace.shell` 为壳入口(Task 5 启动器、run.sh 共用);`epictrace.shell.main()`;wheel 内含 `epictrace/shell/native/*.swift`(dev 懒编译用)。

- [ ] **Step 1: git mv + 建包**

```bash
cd /Users/william/Desktop/EpicTrace
mkdir -p backend/epictrace/shell/native
git mv shell/run.py backend/epictrace/shell/__init__.py
git mv shell/native/SystemAudioCapture.swift backend/epictrace/shell/native/SystemAudioCapture.swift
git mv shell/native/build.sh backend/epictrace/shell/native/build.sh
rmdir shell/native shell 2>/dev/null || true
```

新建 `backend/epictrace/shell/__main__.py`:

```python
"""python -m epictrace.shell 入口(启动器与 run.sh 共用)。"""
from epictrace.shell import main

main()
```

- [ ] **Step 2: 修改搬入后的 `__init__.py` 三处**

(a) 模块 docstring 下方、`if __name__ == "__main__":` 块**删除**(入口在 `__main__.py`;保留 `main()` 函数本体)。

(b) `_ensure_sysaudio_helper()`:在 `if out.exists(): return` 之后插入 packaged 分支(函数其余不动;`src` 的 `Path(__file__).resolve().parent / "native" / ...` 相对定位在新位置天然成立):

```python
    cfg = AppConfig()
    out = cfg.data_dir / "bin" / "epictrace-sysaudio"
    if out.exists():
        return
    if cfg.packaged:
        # 打包模式:helper 由启动器从 .app Resources 预置;缺失说明启动器流程有错。
        # 终端用户无 Xcode CLT,绝不在用户机器上找 swiftc。
        print("[EpicTrace] packaged 模式缺系统内录 helper(应由启动器预置);系统内录暂不可用", flush=True)
        return
```

(原先的 `out = AppConfig().data_dir / ...` 两行被上面替换。)

(c) 新增门面修复函数,并在 `main()` 里接线:

```python
def _fix_app_identity() -> None:
    """窗口进程是 venv python,能修的门面尽量修:应用菜单名(CFBundleName trick,只影响
    菜单栏第一项;Dock 悬停名/强退框仍显示 Python——设计决策 2 已接受)。
    须在 webview.start() 前调用。"""
    try:
        from AppKit import NSBundle

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "EpicTrace"
    except Exception as e:  # noqa: BLE001 — 纯门面,失败不挡启动
        print(f"[EpicTrace] fix app name failed: {e}", flush=True)


def _apply_dock_icon(window: "webview.Window") -> None:
    """EPICTRACE_APP_ICON 指向 .icns 时,窗口 shown 后在主线程把 Dock 图标换成它
    (NSApp 要等 webview 起完才存在;AppKit 调用必须主线程,同 HUD 调级别的模式)。"""
    icon = os.environ.get("EPICTRACE_APP_ICON")
    if not icon or not os.path.isfile(icon):
        return

    def _set() -> None:
        try:
            from AppKit import NSApp, NSImage

            img = NSImage.alloc().initWithContentsOfFile_(icon)
            if img is not None:
                NSApp.setApplicationIconImage_(img)
        except Exception as e:  # noqa: BLE001
            print(f"[EpicTrace] set dock icon: {e}", flush=True)

    def _sched() -> None:
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_set)
        except Exception as e:  # noqa: BLE001
            print(f"[EpicTrace] schedule dock icon: {e}", flush=True)

    window.events.shown += _sched
```

`main()` 改为:

```python
def main() -> None:
    _fix_app_identity()
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    # 缺失则后台构建系统内录 helper(不阻塞开窗;packaged 模式内部直接跳过)。
    threading.Thread(target=_ensure_sysaudio_helper, daemon=True).start()
    if not _wait_until_ready():
        print("[EpicTrace] backend not ready in time; opening window anyway.", flush=True)
    api = Api()
    window = webview.create_window(
        "EpicTrace", f"http://{HOST}:{PORT}", js_api=api, width=1100, height=750
    )
    api.set_window(window)
    _register_native_drop(window)  # 原生拖拽转发,纯附加,不改动既有逻辑
    _apply_dock_icon(window)
    webview.start()
```

- [ ] **Step 3: pyproject package-data + run.sh**

`backend/pyproject.toml` 末尾追加:

```toml
[tool.setuptools.package-data]
"epictrace.shell" = ["native/*.swift", "native/build.sh"]
```

`run.sh:51` 的 `exec "$PY" shell/run.py` 改为:

```bash
  exec "$PY" -m epictrace.shell
```

(顶部注释里的 `壳(shell/run.py)` 同步改成 `壳(epictrace.shell)`。)

`README.md` 两处旧路径同步修:

- `README.md:74` 目录说明行改为:`backend/epictrace/shell/  pywebview 桌面外壳 + macOS 系统内录原生 helper(native/)`
- `README.md:102` 启动命令改为:`cd ../backend && .venv/bin/python -m epictrace.shell`

`backend/tests/test_shell_reveal.py` 弃文件路径加载,文件头(docstring 到 `api` fixture)整体替换为:

```python
"""epictrace.shell 的 reveal_in_finder 路径守卫:不存在的路径不触发 `open -R`,存在才触发。
monkeypatch subprocess.run 以免真的弹 Finder。"""
import pytest

pytest.importorskip("webview")


@pytest.fixture()
def api():
    from epictrace.shell import Api

    return Api()
```

(文件里两个 test 函数本体不动;删掉 `importlib.util`/`Path` 导入、`_RUN_PY` 与 `_load_shell`。)

- [ ] **Step 4: 写测试**

新建 `backend/tests/test_shell_packaging.py`:

```python
"""壳搬进包后的两条底线:可导入(无仓库布局假设)、packaged 模式绝不找 swiftc。"""
import pytest

webview = pytest.importorskip("webview")  # 无 GUI 依赖的环境跳过整文件


def test_shell_module_importable():
    import epictrace.shell  # noqa: F401
    from epictrace.shell import main  # noqa: F401


def test_ensure_helper_skips_swiftc_in_packaged_mode(monkeypatch, tmp_path):
    import epictrace.shell as shell

    monkeypatch.setenv("EPICTRACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EPICTRACE_PACKAGED", "1")

    def _boom(_name):
        raise AssertionError("packaged 模式不得调用 shutil.which 找 swiftc")

    monkeypatch.setattr("shutil.which", _boom)
    shell._ensure_sysaudio_helper()  # 不抛 = 在 packaged 分支提前返回
    assert not (tmp_path / "bin" / "epictrace-sysaudio").exists()


def test_ensure_helper_degrades_without_swiftc(monkeypatch, tmp_path):
    import epictrace.shell as shell

    monkeypatch.setenv("EPICTRACE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("EPICTRACE_PACKAGED", raising=False)
    monkeypatch.setattr("shutil.which", lambda _n: None)
    shell._ensure_sysaudio_helper()  # dev 无 swiftc:打日志降级,不抛
    assert not (tmp_path / "bin" / "epictrace-sysaudio").exists()
```

- [ ] **Step 5: 跑测试 + wheel 内容验证 + dev 冒烟**

Run: `cd backend && .venv/bin/pytest tests/test_shell_packaging.py tests/test_shell_reveal.py -v && .venv/bin/pytest -q`
Expected: 3 + 2 passed(或 webview 缺失全跳过——本机 venv 有,应跑);全量无回归。

Run(wheel 里有 swift 源):
```bash
cd backend && .venv/bin/pip wheel . --no-deps -w /tmp/et-wheel-check -q && unzip -l /tmp/et-wheel-check/epictrace-*.whl | grep -E "shell/(__init__|__main__)|native/SystemAudioCapture" && rm -rf /tmp/et-wheel-check
```
Expected: 三行都在(`epictrace/shell/__init__.py`、`__main__.py`、`native/SystemAudioCapture.swift`)。

Run(壳还能起——不真开窗,验 import 链):
```bash
cd /Users/william/Desktop/EpicTrace && backend/.venv/bin/python -c "import epictrace.shell; print('shell ok')"
```
Expected: `shell ok`。(完整 `./run.sh --no-build` 开窗冒烟留给 Task 9 真机环节,此处不阻塞。)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(shell): 壳搬进 epictrace.shell 包;packaged 跳过 swiftc;run.sh 改 -m 入口"
```

---

### Task 5: Swift 启动器(供给引擎 + GUI + headless)

**Files:**
- Create: `packaging/launcher/Provisioner.swift`
- Create: `packaging/launcher/main.swift`

**Interfaces:**
- Consumes: `.app` Resources 布局(Task 7 组装):`uv`、`requirements.lock`、`python-version`、`epictrace-*.whl`、`frontend-dist/`、`epictrace-sysaudio`、可选 `AppIcon.icns`。
- Produces: 可执行 `EpicTrace`(bundle 主执行体):GUI 模式(默认)与 CLI 模式 `--headless-provision --resources <dir> [--runtime <dir>] [--data-dir <dir>] [--force]`、`--print-plan`。拉起壳:`<venv>/bin/python -m epictrace.shell`,注入 `EPICTRACE_PACKAGED=1`、`EPICTRACE_FRONTEND_DIST`、`EPICTRACE_UV_BIN`、(有图标时)`EPICTRACE_APP_ICON`,并带全套 `UV_*` 隔离变量(MinerU 子调用继承)。

- [ ] **Step 1: 写 `packaging/launcher/Provisioner.swift`**

```swift
// EpicTrace 启动器:供给引擎。uv 全托管 —— python install / venv / pip sync / wheel /
// helper 预置 / provenance 自愈,marker.json 判定是否已就绪。UI 无关,GUI 与 headless 共用。
import CryptoKit
import Foundation

struct ProvisionError: Error, CustomStringConvertible {
    let stage: String
    let message: String
    var description: String { "[\(stage)] \(message)" }
}

/// 首启失败最高频的两类要说人话(设计 §4.3):按错误文本粗分类。
func humanMessage(_ error: Error) -> String {
    let raw = String(describing: error)
    let lower = raw.lowercased()
    if lower.contains("no space left") || lower.contains("enospc") || lower.contains("磁盘") {
        return "磁盘空间不足:首次启动需要约 2GB 可用空间。\n\n\(raw)"
    }
    for hint in ["error sending request", "connection", "network", "timed out",
                 "dns error", "operation not permitted (os error", "tcp connect"] {
        if lower.contains(hint) {
            return "网络不可用或不稳定:首次启动需要联网下载运行环境,请检查网络后重试。\n\n\(raw)"
        }
    }
    return raw
}

/// 判据 = wheel 内容 hash(不是文件名/版本号):开发/验收期版本恒 0.1.0,
/// 同版本重打包 wheel hash 必变 → 自动触发增量 re-sync,helper 也随之更新。
struct Marker: Codable, Equatable {
    let lockSha256: String
    let wheelSha256: String
    let pythonVersion: String
}

final class ProvisionEngine {
    let resourcesDir: URL
    let runtimeDir: URL
    let dataDir: URL
    /// 全量输出(uv stdout/stderr 逐行)→ bootstrap.log;progress = 阶段级人话(UI 状态行)。
    let log: (String) -> Void
    let progress: (String) -> Void

    init(resourcesDir: URL, runtimeDir: URL, dataDir: URL,
         log: @escaping (String) -> Void, progress: @escaping (String) -> Void) {
        self.resourcesDir = resourcesDir
        self.runtimeDir = runtimeDir
        self.dataDir = dataDir
        self.log = log
        self.progress = progress
    }

    // ---- 路径 ----
    var uvBin: URL { resourcesDir.appendingPathComponent("uv") }
    var lockFile: URL { resourcesDir.appendingPathComponent("requirements.lock") }
    var frontendDist: URL { resourcesDir.appendingPathComponent("frontend-dist") }
    var helperSrc: URL { resourcesDir.appendingPathComponent("epictrace-sysaudio") }
    var appIcon: URL { resourcesDir.appendingPathComponent("AppIcon.icns") }
    var venvDir: URL { runtimeDir.appendingPathComponent("venv") }
    var venvPython: URL { venvDir.appendingPathComponent("bin/python") }
    var markerFile: URL { runtimeDir.appendingPathComponent("marker.json") }
    var pythonInstallDir: URL { runtimeDir.appendingPathComponent("python") }

    func pythonVersion() throws -> String {
        guard let s = try? String(contentsOf: resourcesDir.appendingPathComponent("python-version"),
                                  encoding: .utf8)
        else { throw ProvisionError(stage: "resources", message: "python-version 缺失") }
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func wheelURL() throws -> URL {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: resourcesDir, includingPropertiesForKeys: nil)) ?? []
        guard let whl = files.first(where: { $0.lastPathComponent.hasSuffix(".whl") })
        else { throw ProvisionError(stage: "resources", message: "Resources 里没有 .whl") }
        return whl
    }

    // ---- uv 状态全隔离(设计 §3.2;ComfyUI 泄漏全局的反面教材) ----
    func uvEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let r = runtimeDir.path
        env["UV_CACHE_DIR"] = r + "/uv-cache"
        env["UV_PYTHON_INSTALL_DIR"] = r + "/python"
        env["UV_PYTHON_CACHE_DIR"] = r + "/uv-python-cache"
        env["UV_PYTHON_BIN_DIR"] = r + "/python-bin"
        env["UV_TOOL_DIR"] = r + "/uv-tools"
        env["UV_TOOL_BIN_DIR"] = r + "/uv-tool-bin"
        env["UV_NO_CONFIG"] = "1"
        env["UV_MANAGED_PYTHON"] = "1"
        return env
    }

    /// 拉起壳用:uv 隔离变量(MinerU 子调用继承)+ EPICTRACE_* 注入。
    /// 供给已完成 → 维护性 uv 调用禁再下 Python(设计 §3.2);EPICTRACE_DATA_DIR 与
    /// installHelper 用的 dataDir 保持同源(--data-dir 覆盖时 helper 位置才不分叉)。
    func shellEnvironment() -> [String: String] {
        var env = uvEnvironment()
        env["UV_PYTHON_DOWNLOADS"] = "never"
        env["EPICTRACE_PACKAGED"] = "1"
        env["EPICTRACE_DATA_DIR"] = dataDir.path
        env["EPICTRACE_FRONTEND_DIST"] = frontendDist.path
        env["EPICTRACE_UV_BIN"] = uvBin.path
        if FileManager.default.fileExists(atPath: appIcon.path) {
            env["EPICTRACE_APP_ICON"] = appIcon.path
        }
        return env
    }

    // ---- marker ----
    func expectedMarker() throws -> Marker {
        let lock = try Data(contentsOf: lockFile)
        let lockHash = SHA256.hash(data: lock).map { String(format: "%02x", $0) }.joined()
        let wheel = try Data(contentsOf: try wheelURL())
        let wheelHash = SHA256.hash(data: wheel).map { String(format: "%02x", $0) }.joined()
        return Marker(lockSha256: lockHash,
                      wheelSha256: wheelHash,
                      pythonVersion: try pythonVersion())
    }

    func currentMarker() -> Marker? {
        guard let d = try? Data(contentsOf: markerFile) else { return nil }
        return try? JSONDecoder().decode(Marker.self, from: d)
    }

    func isProvisioned() -> Bool {
        guard let cur = currentMarker(), let exp = try? expectedMarker() else { return false }
        return cur == exp && FileManager.default.isExecutableFile(atPath: venvPython.path)
    }

    // ---- 子进程 ----
    @discardableResult
    func run(_ argv: [String], env: [String: String]? = nil, stage: String) throws -> Int32 {
        log("$ " + argv.joined(separator: " "))
        let p = Process()
        p.executableURL = URL(fileURLWithPath: argv[0])
        p.arguments = Array(argv.dropFirst())
        if let env { p.environment = env }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch {
            throw ProvisionError(stage: stage, message: "无法启动 \(argv[0]): \(error.localizedDescription)")
        }
        // 后台线程同步读到 EOF,替代 readabilityHandler:
        // 1) 子进程退出瞬间滞留在管道缓冲、尚未派发的最后几行不会丢(失败时最有
        //    诊断价值的恰是这几行),读干 EOF 后才取 tail;
        // 2) 子进程存活期间始终有人在读,管道写满不会反压阻塞子进程(经典死锁坑)。
        var tail: [String] = []
        let drained = DispatchSemaphore(value: 0)
        let reading = pipe.fileHandleForReading
        DispatchQueue.global(qos: .utility).async { [log] in
            // 字节域按 \n 分行 + 有损解码:read 边界切在多字节 UTF-8 字符中间时,
            // 残缺尾部留在 buf 等下一个 chunk 拼合,绝不整块丢弃。
            var buf = Data()
            func emit(_ data: Data) {
                guard !data.isEmpty else { return }  // 与旧 split 行为一致:跳过空行
                let line = String(decoding: data, as: UTF8.self)
                log(line)
                tail.append(line)
                if tail.count > 30 { tail.removeFirst() }
            }
            while true {
                let chunk = reading.availableData  // 阻塞读;空 Data = EOF
                if chunk.isEmpty { break }
                buf.append(chunk)
                while let nl = buf.firstIndex(of: 0x0A) {
                    emit(buf.subdata(in: buf.startIndex ..< nl))
                    buf.removeSubrange(buf.startIndex ... nl)
                }
            }
            emit(buf)  // 无换行结尾的最后一段
            drained.signal()
        }
        p.waitUntilExit()
        drained.wait()  // 等读线程见到 EOF,tail 才完整
        guard p.terminationStatus == 0 else {
            throw ProvisionError(stage: stage,
                                 message: "退出码 \(p.terminationStatus)\n" + tail.suffix(12).joined(separator: "\n"))
        }
        return p.terminationStatus
    }

    // ---- 自愈梯度(设计 §7):0=直接供给;1=清 uv 缓存后供给;2=删 runtime 全量重建 ----
    func provision(escalation: Int) throws {
        switch escalation {
        case 1:
            progress("清理下载缓存后重试…")
            _ = try? run([uvBin.path, "cache", "clean"], env: uvEnvironment(), stage: "cache-clean")
            try provision(force: false)
        case 2:
            try provision(force: true)
        default:
            try provision(force: false)
        }
    }

    // ---- 主流程 ----
    func provision(force: Bool = false) throws {
        let fm = FileManager.default
        if force, fm.fileExists(atPath: runtimeDir.path) {
            progress("清理旧运行时,全量重建…")
            try fm.removeItem(at: runtimeDir)
        }
        try fm.createDirectory(at: runtimeDir, withIntermediateDirectories: true)
        let ver = try pythonVersion()
        let env = uvEnvironment()

        // python 补丁版本变更:venv 指向旧解释器,必须重建(sync 不会换 python)。
        if let cur = currentMarker(), cur.pythonVersion != ver,
           fm.fileExists(atPath: venvDir.path) {
            progress("Python 版本变更,重建虚拟环境…")
            try fm.removeItem(at: venvDir)
        }

        progress("安装 Python \(ver)(约 26MB)…")
        try run([uvBin.path, "python", "install", ver], env: env, stage: "python-install")

        if !fm.fileExists(atPath: venvPython.path) {
            progress("创建虚拟环境…")
            try run([uvBin.path, "venv", "--python", ver, venvDir.path], env: env, stage: "venv")
        }

        progress("同步依赖(首次约 2GB,请保持联网)…")
        try run([uvBin.path, "pip", "sync", "--require-hashes",
                 "--python", venvPython.path, lockFile.path], env: env, stage: "pip-sync")

        progress("安装 EpicTrace…")
        try run([uvBin.path, "pip", "install", "--no-deps",
                 "--python", venvPython.path, try wheelURL().path], env: env, stage: "wheel")

        progress("预置系统内录组件…")
        try installHelper()

        progress("修复 Gatekeeper 属性…")
        selfHeal()

        let data = try JSONEncoder().encode(try expectedMarker())
        try data.write(to: markerFile)
        progress("环境就绪。")
    }

    func installHelper() throws {
        let fm = FileManager.default
        let binDir = dataDir.appendingPathComponent("bin")
        try fm.createDirectory(at: binDir, withIntermediateDirectories: true)
        let dst = binDir.appendingPathComponent("epictrace-sysaudio")
        if fm.fileExists(atPath: dst.path) { try fm.removeItem(at: dst) }
        try fm.copyItem(at: helperSrc, to: dst)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dst.path)
    }

    /// provenance/quarantine xattr 递归清除 + 解释器 ad-hoc 重签。
    /// uv#16726:macOS 15.6+/26.x 上 com.apple.provenance + ad-hoc 组合会被内核 SIGKILL
    /// (无日志)或静默挂起;上游未修,自愈必备、幂等、无副作用。若将来观测到 venv 内
    /// torch .so 也中招,把重签范围扩到 venv(backlog,代价是首启多几分钟)。
    func selfHeal() {
        for attr in ["com.apple.provenance", "com.apple.quarantine"] {
            _ = try? run(["/usr/bin/xattr", "-r", "-d", attr, runtimeDir.path], stage: "xattr")
        }
        let fm = FileManager.default
        guard let en = fm.enumerator(at: pythonInstallDir, includingPropertiesForKeys: nil)
        else { return }
        for case let f as URL in en {
            let name = f.lastPathComponent
            let parent = f.deletingLastPathComponent().lastPathComponent
            let isMachO = parent == "bin" || name.hasSuffix(".dylib") || name.hasSuffix(".so")
            if isMachO, fm.isExecutableFile(atPath: f.path) || name.hasSuffix(".dylib") || name.hasSuffix(".so") {
                _ = try? run(["/usr/bin/codesign", "--force", "-s", "-", f.path], stage: "resign")
            }
        }
    }
}
```

- [ ] **Step 2: 写 `packaging/launcher/main.swift`**

```swift
// EpicTrace 启动器入口:GUI(默认,进度窗 + 拉起壳保活)与 headless(打包冒烟/干净账户测试)。
// 启动器保活为父进程 = TCC responsible process 锚点:python/helper 的麦克风、系统内录
// 弹窗与授权都归因到本 .app(设计 §8),所以绝不能 exec 后自退。
import AppKit
import Foundation

// ---- 公共:路径与日志 ----
let fm = FileManager.default
let home = fm.homeDirectoryForCurrentUser
let defaultRuntime = home.appendingPathComponent("Library/Application Support/EpicTrace/runtime")
let logDir = home.appendingPathComponent("Library/Logs/EpicTrace")

func openAppendHandle(_ fileName: String) -> FileHandle? {
    try? fm.createDirectory(at: logDir, withIntermediateDirectories: true)
    let f = logDir.appendingPathComponent(fileName)
    if !fm.fileExists(atPath: f.path) { fm.createFile(atPath: f.path, contents: nil) }
    let h = try? FileHandle(forWritingTo: f)
    _ = try? h?.seekToEnd()
    return h
}

let logHandle = openAppendHandle("bootstrap.log")
let logQueue = DispatchQueue(label: "epictrace.launcher.log")
func writeLog(_ line: String) {
    logQueue.sync {
        if let d = (line + "\n").data(using: .utf8) { try? logHandle?.write(contentsOf: d) }
    }
}

// ---- 参数解析 ----
var args = Array(CommandLine.arguments.dropFirst())
func takeValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    let v = args[i + 1]
    args.removeSubrange(i ... i + 1)
    return v
}
let headless = args.contains("--headless-provision")
let printPlan = args.contains("--print-plan")
let force = args.contains("--force")
let resourcesOverride = takeValue("--resources")
let runtimeOverride = takeValue("--runtime")
let dataDirOverride = takeValue("--data-dir")

func resolveResources() -> URL {
    if let r = resourcesOverride { return URL(fileURLWithPath: r) }
    if let r = Bundle.main.resourceURL { return r }
    fputs("无法定位 Resources(bundle 外运行请传 --resources)\n", stderr)
    exit(2)
}

// data_dir 解析:--data-dir > EPICTRACE_DATA_DIR > ~/.epictrace,与壳/后端同一优先级语义
// (installHelper 的落点和 shellEnvironment 注入的 EPICTRACE_DATA_DIR 因此恒同源)。
func resolveDataDir() -> URL {
    if let d = dataDirOverride { return URL(fileURLWithPath: d) }
    if let d = ProcessInfo.processInfo.environment["EPICTRACE_DATA_DIR"], !d.isEmpty {
        return URL(fileURLWithPath: (d as NSString).expandingTildeInPath)
    }
    return home.appendingPathComponent(".epictrace")
}

let engine = ProvisionEngine(
    resourcesDir: resolveResources(),
    runtimeDir: runtimeOverride.map { URL(fileURLWithPath: $0) } ?? defaultRuntime,
    dataDir: resolveDataDir(),
    log: { line in
        writeLog(line)
        if headless || printPlan { print(line) }
    },
    progress: { msg in
        writeLog("== " + msg)
        if headless { print("== " + msg) }
        AppState.shared?.setStatus(msg)
    }
)

// ---- headless / print-plan 模式 ----
if printPlan {
    print("resources: \(engine.resourcesDir.path)")
    print("runtime:   \(engine.runtimeDir.path)")
    print("data-dir:  \(engine.dataDir.path)")
    print("provisioned: \(engine.isProvisioned())")
    if let m = try? engine.expectedMarker() {
        print("expect: python \(m.pythonVersion), wheel \(m.wheelSha256.prefix(12))…, lock \(m.lockSha256.prefix(12))…")
    }
    for (k, v) in engine.uvEnvironment().sorted(by: { $0.key < $1.key }) where k.hasPrefix("UV_") {
        print("env \(k)=\(v)")
    }
    exit(0)
}
if headless {
    // 快速路径与 GUI 对齐:marker 命中且未 --force 时秒退(冒烟可重复跑)。
    if !force, engine.isProvisioned() {
        print("already provisioned → \(engine.runtimeDir.path)")
        exit(0)
    }
    do {
        try engine.provision(force: force)
        print("OK provisioned → \(engine.runtimeDir.path)")
        exit(0)
    } catch {
        fputs("供给失败:\(humanMessage(error))\n日志:\(logDir.path)/bootstrap.log\n", stderr)
        exit(1)
    }
}

// ---- GUI 模式 ----
final class AppState: NSObject, NSApplicationDelegate {
    static var shared: AppState?
    var window: NSWindow?
    var statusLabel: NSTextField?
    var shell: Process?
    var shellLaunchedAt: Date?
    /// 自愈梯度游标(设计 §7):0 直接供给 → 1 清缓存重试 → 2 删 runtime 全量重建。
    var escalation = 0

    func setStatus(_ msg: String) {
        DispatchQueue.main.async { self.statusLabel?.stringValue = msg }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        AppState.shared = self
        installSignalForwarders()
        if engine.isProvisioned() {
            launchShell()
        } else {
            showProgressWindow()
            DispatchQueue.global(qos: .userInitiated).async { self.runProvision() }
        }
    }

    func runProvision() {
        do {
            try engine.provision(escalation: escalation)
            DispatchQueue.main.async {
                self.window?.orderOut(nil)
                self.launchShell()
            }
        } catch {
            DispatchQueue.main.async { self.showError(error) }
        }
    }

    func showProgressWindow() {
        // 供给阶段临时成为普通 app(Dock 出现 EpicTrace);拉起壳后降回 accessory 交还前台。
        NSApp.setActivationPolicy(.regular)
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 120),
                         styleMask: [.titled], backing: .buffered, defer: false)
        w.title = "EpicTrace 首次启动"
        w.center()
        let label = NSTextField(labelWithString: "准备运行环境…")
        label.frame = NSRect(x: 20, y: 66, width: 440, height: 20)
        let bar = NSProgressIndicator(frame: NSRect(x: 20, y: 34, width: 440, height: 20))
        bar.style = .bar
        bar.isIndeterminate = true
        bar.startAnimation(nil)
        w.contentView?.addSubview(label)
        w.contentView?.addSubview(bar)
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = w
        statusLabel = label
    }

    func showError(_ error: Error) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "EpicTrace 环境准备失败"
        let next = escalation >= 1 ? "重试(全量重建)" : "重试(清缓存)"
        alert.informativeText = "\(humanMessage(error))\n\n日志:~/Library/Logs/EpicTrace/bootstrap.log"
        alert.addButton(withTitle: next)
        alert.addButton(withTitle: "打开日志")
        alert.addButton(withTitle: "退出")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            escalation = min(escalation + 1, 2)
            if window == nil { showProgressWindow() } else { window?.makeKeyAndOrderFront(nil) }
            DispatchQueue.global(qos: .userInitiated).async { self.runProvision() }
        case .alertSecondButtonReturn:
            NSWorkspace.shared.open(logDir.appendingPathComponent("bootstrap.log"))
            NSApp.terminate(nil)
        default:
            NSApp.terminate(nil)
        }
    }

    func launchShell() {
        NSApp.setActivationPolicy(.accessory)  // 把 Dock 交还给壳(python)进程
        let p = Process()
        p.executableURL = engine.venvPython
        p.arguments = ["-m", "epictrace.shell"]
        p.environment = engine.shellEnvironment()
        // 壳输出不能无声消失:Finder 双击启动无终端,Python 侧 print/异常栈全部
        // append 到 shell.log(stdout/stderr 共用同一 handle)。
        if let shellLog = openAppendHandle("shell.log") {
            p.standardOutput = shellLog
            p.standardError = shellLog
            writeLog("shell 输出重定向 → \(logDir.path)/shell.log")
        }
        p.terminationHandler = { proc in
            writeLog("shell 退出,code=\(proc.terminationStatus)")
            DispatchQueue.main.async { self.handleShellExit(proc) }
        }
        do {
            try p.run()
            shell = p
            shellLaunchedAt = Date()
            writeLog("shell 已拉起 pid=\(p.processIdentifier)")
            watchBackendHealth()
        } catch {
            showError(ProvisionError(stage: "launch", message: error.localizedDescription))
        }
    }

    /// 壳秒退且非 0 = 环境损坏(marker 在但 venv 起不来等):给「重建环境」出口(设计 §4.3)。
    func handleShellExit(_ proc: Process) {
        let uptime = shellLaunchedAt.map { Date().timeIntervalSince($0) } ?? .infinity
        if proc.terminationStatus != 0, uptime < 10 {
            let alert = NSAlert()
            alert.alertStyle = .critical
            alert.messageText = "EpicTrace 启动失败"
            alert.informativeText = "运行环境可能已损坏(退出码 \(proc.terminationStatus))。"
                + "\n\n日志:~/Library/Logs/EpicTrace/bootstrap.log"
            alert.addButton(withTitle: "重建环境")
            alert.addButton(withTitle: "退出")
            if alert.runModal() == .alertFirstButtonReturn {
                escalation = 2
                showProgressWindow()
                DispatchQueue.global(qos: .userInitiated).async { self.runProvision() }
                return
            }
        }
        NSApp.terminate(nil)
    }

    /// 超时兜底(设计 §4.1/§11):30s 内后端未就绪且壳还活着 → 报错提示(不自动换端口)。
    func watchBackendHealth() {
        DispatchQueue.global(qos: .utility).async {
            let deadline = Date().addingTimeInterval(30)
            let url = URL(string: "http://127.0.0.1:8765/api/health")!
            while Date() < deadline {
                if (try? Data(contentsOf: url)) != nil { return }
                Thread.sleep(forTimeInterval: 1.0)
            }
            guard let shell = self.shell, shell.isRunning else { return }
            writeLog("后端 30s 未就绪(壳仍在运行)")
            DispatchQueue.main.async {
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "后端未在 30 秒内就绪"
                alert.informativeText = "端口 8765 可能被其它程序占用。"
                    + "\n\n日志:~/Library/Logs/EpicTrace/bootstrap.log"
                alert.addButton(withTitle: "继续等待")
                alert.addButton(withTitle: "退出")
                if alert.runModal() == .alertSecondButtonReturn {
                    shell.terminate()
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func installSignalForwarders() {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler {
                self.shell?.terminate()
                NSApp.terminate(nil)
            }
            src.resume()
            signalSources.append(src)
        }
    }
}

var signalSources: [DispatchSourceSignal] = []
let app = NSApplication.shared
let delegate = AppState()
app.delegate = delegate
app.run()
```

- [ ] **Step 3: 编译验证**

Run:
```bash
cd /Users/william/Desktop/EpicTrace && swiftc -O -target arm64-apple-macosx14.4 packaging/launcher/main.swift packaging/launcher/Provisioner.swift -o /tmp/et-launcher-test
```
Expected: 编译成功无 error(warning 可容忍)。**`-target arm64-apple-macosx14.4` 必带**:不带时产物 minos = 构建机系统版本(实测 26.0),macOS 14.4~15.x 上 dyld 直接拒载,与 Info.plist 的 LSMinimumSystemVersion=14.4 矛盾。编译错误按报错修——以编译器为准微调 API 细节,**不改变行为设计**。(此代码已在计划评审时用同命令编译通过。)

- [ ] **Step 4: headless 错误路径验证(不联网、不下载)**

Run(注意 zsh 下不要用 `rm -f dir/*` 清空——空目录 glob 失配会断掉 `&&` 链):
```bash
rm -rf /tmp/et-fake-res && mkdir -p /tmp/et-fake-res \
  && /tmp/et-launcher-test --print-plan --resources /tmp/et-fake-res --runtime /tmp/et-rt --data-dir /tmp/et-dd \
  ; /tmp/et-launcher-test --headless-provision --resources /tmp/et-fake-res --runtime /tmp/et-rt --data-dir /tmp/et-dd; echo "exit=$?"
```
Expected: `--print-plan` 打印 resources/runtime/data-dir 与 UV_* 环境(provisioned: false,expect 行缺 python-version 时不打);headless 供给以人话错误失败(`python-version 缺失`),`exit=1`;`/tmp/et-rt` 下无 venv。清理:`rm -rf /tmp/et-rt /tmp/et-dd /tmp/et-fake-res`。

- [ ] **Step 5: Commit**

```bash
git add packaging/launcher/
git commit -m "feat(packaging): Swift 启动器——uv 全托管供给引擎 + GUI 进度窗 + headless 模式"
```

---

### Task 6: 打包资产(Info.plist / entitlements / python-version / licenses)

**Files:**
- Create: `packaging/Info.plist.in`
- Create: `packaging/entitlements/launcher.entitlements`
- Create: `packaging/python-version`
- Create: `packaging/licenses/uv-LICENSE-MIT`、`packaging/licenses/uv-LICENSE-APACHE`
- Modify: `.gitignore`(追加 `packaging/vendor/`、`dist/`)

**Interfaces:**
- Produces: Task 7 打包脚本消费的全部静态资产。`@VERSION@` 占位符由脚本 sed 替换。

- [ ] **Step 1: Info.plist.in**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>zh_CN</string>
  <key>CFBundleExecutable</key><string>EpicTrace</string>
  <key>CFBundleIdentifier</key><string>com.epictrace.app</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>EpicTrace</string>
  <key>CFBundleDisplayName</key><string>EpicTrace</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>@VERSION@</string>
  <key>CFBundleVersion</key><string>@VERSION@</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>14.4</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>EpicTrace 仅在你主动开始录制会话时使用麦克风进行语音转写。</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>EpicTrace 仅在你主动开始录制会话且选择「系统声音」时采集系统音频。</string>
</dict>
</plist>
```

要点:`LSUIElement=true`(启动器默认无 Dock 身份,供给时临时升 regular,壳接管后降回——见 Task 5);两条 usage description **必须在**(TCC 按 responsible process 查本 Info.plist,缺失时 python 子进程开麦会被 TCC 杀死);bundle id 永不改;`CFBundleIconFile` 无 icns 时系统给通用图标,无害。

- [ ] **Step 2: entitlements(ASCII XML,公证要求;最小化)**

`packaging/entitlements/launcher.entitlements`(audio-input 是无害保险:归因边角情形下 hardened 进程碰音频需要它;**不加** disable-library-validation——hardened runtime 不遗传子进程,启动器自身不 dlopen 未签名库):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.device.audio-input</key><true/>
</dict>
</plist>
```

helper **不建 entitlements 文件**(按 spec §6:纯系统音频 tap 无对应 hardened-runtime entitlement、只吃 TCC;保持最小化),打包脚本只对它 `--options runtime --timestamp` 签名。**若 Task 9 真机测试系统内录失败,第一排查项**就是给 helper 补 `com.apple.security.device.audio-input` 重签验证,确认后回写 spec §6。

- [ ] **Step 3: python-version + licenses + .gitignore**

```bash
cd /Users/william/Desktop/EpicTrace
printf '3.11.15\n' > packaging/python-version
mkdir -p packaging/licenses
curl -fsSL https://raw.githubusercontent.com/astral-sh/uv/main/LICENSE-MIT -o packaging/licenses/uv-LICENSE-MIT
curl -fsSL https://raw.githubusercontent.com/astral-sh/uv/main/LICENSE-APACHE -o packaging/licenses/uv-LICENSE-APACHE
printf 'packaging/vendor/\n' >> .gitignore   # dist/ 已在 .gitignore:20,勿重复
```

验证:`head -3 packaging/licenses/uv-LICENSE-MIT` 应是 MIT 文本(`Permission is hereby granted…` 在文件内)。

- [ ] **Step 4: Commit**

```bash
git add packaging/ .gitignore
git commit -m "feat(packaging): Info.plist/entitlements/python-version/uv 许可文本"
```

---

### Task 7: package_app.sh(组装 + --no-sign 冒烟)

**Files:**
- Create: `scripts/package_app.sh`(chmod +x)

**Interfaces:**
- Consumes: Task 4 wheel 布局、Task 5 启动器源码、Task 6 资产。
- Produces: `dist/EpicTrace.app`(默认签名;`--no-sign` 跳过)、`dist/EpicTrace-<ver>.dmg`(`--notarize` 时);vendored uv 缓存在 `packaging/vendor/uv-<ver>/uv`。

- [ ] **Step 1: 写脚本**

```bash
#!/usr/bin/env bash
# EpicTrace 打包:组装签名/公证的 .app 与 dmg。设计:docs/superpowers/specs/
# 2026-07-14-epictrace-macos-app-packaging-design.md(§3 布局、§6 流水线)。
#
# 用法:
#   scripts/package_app.sh                   构建 + 签名(需 SIGN_IDENTITY)
#   scripts/package_app.sh --no-sign         构建不签名(本地冒烟)
#   scripts/package_app.sh --notarize        构建 + 签名 + dmg + 公证 + staple
#                                            (需 SIGN_IDENTITY 与 NOTARY_PROFILE)
#   scripts/package_app.sh --skip-frontend   跳过 npm build(前端没改时)
# 环境:
#   SIGN_IDENTITY   例 "Developer ID Application: <Name> (<TEAMID>)"
#   NOTARY_PROFILE  notarytool keychain profile 名(xcrun notarytool store-credentials 建)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_VERSION="0.11.28"
BUILD="$ROOT/dist/build"
APP="$ROOT/dist/EpicTrace.app"
PY="$ROOT/backend/.venv/bin/python"

sign=1; notarize=0; frontend=1
for a in "$@"; do case "$a" in
  --no-sign) sign=0 ;;
  --notarize) notarize=1 ;;
  --skip-frontend) frontend=0 ;;
  *) echo "未知参数:$a" >&2; exit 2 ;;
esac; done
[ "$sign" = 1 ] && : "${SIGN_IDENTITY:?需要 SIGN_IDENTITY(或用 --no-sign)}"
[ "$notarize" = 1 ] && : "${NOTARY_PROFILE:?--notarize 需要 NOTARY_PROFILE}"
[ -x "$PY" ] || { echo "✗ backend/.venv 缺失" >&2; exit 1; }

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/backend/pyproject.toml" | head -1)"
echo "▶ EpicTrace $VERSION(uv $UV_VERSION,python $(cat "$ROOT/packaging/python-version"))"
rm -rf "$BUILD" "$APP"; mkdir -p "$BUILD"

# [1] 前端
if [ "$frontend" = 1 ]; then
  echo "▶ 构建前端…"
  (cd "$ROOT/frontend" && { [ -d node_modules ] || npm install; } && npm run build)
fi
[ -f "$ROOT/frontend/dist/index.html" ] || { echo "✗ frontend/dist 缺失(先 build)" >&2; exit 1; }

# [2] vendor uv(缓存;下载校验 .sha256)
VENDOR="$ROOT/packaging/vendor/uv-$UV_VERSION"
if [ ! -x "$VENDOR/uv" ]; then
  echo "▶ 下载 uv $UV_VERSION…"
  mkdir -p "$VENDOR"; cd "$VENDOR"
  base="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
  curl -fsSL -o uv.tar.gz "$base/uv-aarch64-apple-darwin.tar.gz"
  curl -fsSL -o uv.tar.gz.sha256 "$base/uv-aarch64-apple-darwin.tar.gz.sha256"
  echo "$(awk '{print $1}' uv.tar.gz.sha256)  uv.tar.gz" | shasum -a 256 -c -
  tar -xzf uv.tar.gz --strip-components=1 uv-aarch64-apple-darwin/uv
  rm uv.tar.gz uv.tar.gz.sha256; cd "$ROOT"
fi

# [3] wheel + lockfile(vendored uv 编译,单平台 arm64-mac,带 hash)
echo "▶ 构建 wheel 与 lockfile…"
"$PY" -m pip wheel "$ROOT/backend" --no-deps -w "$BUILD/wheels" -q
WHEEL="$(ls "$BUILD"/wheels/epictrace-*.whl)"
"$VENDOR/uv" pip compile "$ROOT/backend/pyproject.toml" --generate-hashes \
  --python-version 3.11 -o "$BUILD/requirements.lock" -q

# [4] 编译 Swift(helper + 启动器)。-target 必带:不带时 minos=构建机系统版本,
# 14.4~15.x 的机器 dyld 直接拒载(与 LSMinimumSystemVersion=14.4 矛盾)。
SWIFT_TARGET="arm64-apple-macosx14.4"
echo "▶ 编译 Swift…"
swiftc -O -target "$SWIFT_TARGET" "$ROOT/backend/epictrace/shell/native/SystemAudioCapture.swift" -o "$BUILD/epictrace-sysaudio"
swiftc -O -target "$SWIFT_TARGET" "$ROOT/packaging/launcher/main.swift" "$ROOT/packaging/launcher/Provisioner.swift" -o "$BUILD/EpicTrace"

# [5] 组装 .app
echo "▶ 组装 $APP…"
RES="$APP/Contents/Resources"
mkdir -p "$APP/Contents/MacOS" "$RES"
cp "$BUILD/EpicTrace" "$APP/Contents/MacOS/EpicTrace"
sed "s/@VERSION@/$VERSION/g" "$ROOT/packaging/Info.plist.in" > "$APP/Contents/Info.plist"
cp "$VENDOR/uv" "$RES/uv"
cp "$WHEEL" "$RES/"
cp "$BUILD/requirements.lock" "$RES/requirements.lock"
cp "$ROOT/packaging/python-version" "$RES/python-version"
cp "$BUILD/epictrace-sysaudio" "$RES/epictrace-sysaudio"
cp -R "$ROOT/frontend/dist" "$RES/frontend-dist"
mkdir -p "$RES/licenses" && cp "$ROOT/packaging/licenses/"* "$RES/licenses/"
[ -f "$ROOT/packaging/AppIcon.icns" ] && cp "$ROOT/packaging/AppIcon.icns" "$RES/AppIcon.icns"

# [6] 签名(inside-out:先嵌套二进制,后 .app;禁 --deep)
if [ "$sign" = 1 ]; then
  echo "▶ 签名…"
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" "$RES/uv"
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" "$RES/epictrace-sysaudio"
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" \
    --entitlements "$ROOT/packaging/entitlements/launcher.entitlements" "$APP"
  codesign --verify --strict --verbose=2 "$APP"
fi

# [7] dmg + 公证(可选)
if [ "$notarize" = 1 ]; then
  echo "▶ 打 dmg 并公证…"
  DMGROOT="$BUILD/dmgroot"; rm -rf "$DMGROOT"; mkdir -p "$DMGROOT"
  cp -R "$APP" "$DMGROOT/"; ln -s /Applications "$DMGROOT/Applications"
  DMG="$ROOT/dist/EpicTrace-$VERSION.dmg"
  hdiutil create -volname "EpicTrace" -srcfolder "$DMGROOT" -ov -format UDZO "$DMG"
  codesign --force --timestamp -s "$SIGN_IDENTITY" "$DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  echo "✓ $DMG(已公证 + staple)"
  # 可选增强(设计 §6):先单独公证并 staple .app 再打 dmg,离线首启不查在线票据;MVP 不做。
fi
echo "✓ 完成:$APP"
```

```bash
chmod +x scripts/package_app.sh
```

- [ ] **Step 2: 未签名组装冒烟**

Run: `scripts/package_app.sh --no-sign`
Expected: `✓ 完成:…/dist/EpicTrace.app`;检查布局:

```bash
ls dist/EpicTrace.app/Contents/MacOS/EpicTrace dist/EpicTrace.app/Contents/Resources/{uv,requirements.lock,python-version,epictrace-sysaudio} dist/EpicTrace.app/Contents/Resources/epictrace-*.whl dist/EpicTrace.app/Contents/Resources/frontend-dist/index.html
```
Expected: 全部存在。

- [ ] **Step 3: headless 真实供给冒烟(联网,~2GB,一次)**

Run:
```bash
dist/EpicTrace.app/Contents/MacOS/EpicTrace --headless-provision \
  --resources dist/EpicTrace.app/Contents/Resources \
  --runtime /tmp/et-smoke-runtime --data-dir /tmp/et-smoke-data
echo "exit=$?"
/tmp/et-smoke-runtime/venv/bin/python -c "import epictrace, fastapi, webview; print('venv ok')"
ls /tmp/et-smoke-data/bin/epictrace-sysaudio && cat /tmp/et-smoke-runtime/marker.json
ls ~/.cache/uv ~/.local/share/uv 2>&1 | head -3   # 隔离验证:不应因本次运行新建
```
Expected: `exit=0`;`venv ok`;helper 与 marker.json 就位;用户全局 uv 目录未被本次运行创建/改动(原本就有的不算)。二次运行同命令应秒级完成(marker 命中,幂等)。清理:`rm -rf /tmp/et-smoke-runtime /tmp/et-smoke-data`。

- [ ] **Step 4: Commit**

```bash
git add scripts/package_app.sh
git commit -m "feat(packaging): package_app.sh 一键组装 .app(--no-sign 冒烟 / --notarize 全链)"
```

---

### Task 8: 签名 + 公证走通 + 文档

**Files:**
- Modify: `README.md`(打包章节)
- Modify: `CLAUDE.md`(补打包命令一行)

前置(William 手工,一次性;可在会话里用 `!` 前缀跑):
- 确认证书:`security find-identity -v -p codesigning`(有 "Developer ID Application" 项)
- 存公证凭据:`xcrun notarytool store-credentials epictrace-notary --apple-id <id> --team-id <TEAMID> --password <app-specific-password>`

- [ ] **Step 1: 签名构建**

Run: `SIGN_IDENTITY="Developer ID Application: <按实际>" scripts/package_app.sh --skip-frontend`
Expected: `codesign --verify --strict` 通过。抽查:

```bash
codesign -dvv dist/EpicTrace.app 2>&1 | grep -E "Identifier|TeamIdentifier|runtime"
codesign -dvv dist/EpicTrace.app/Contents/Resources/uv 2>&1 | grep -E "TeamIdentifier|runtime"
```
Expected: Identifier=com.epictrace.app;两者 TeamIdentifier 为真实 Team、flags 含 runtime(uv 重签生效——原 release 是 adhoc/linker-signed)。

公证完成后(Step 2 之后)对 .app 本体也 assess 一次(spec §9.5;评估走在线票据查询):

```bash
spctl -a -vv -t exec dist/EpicTrace.app
```
Expected: `accepted`,`source=Notarized Developer ID`。

- [ ] **Step 2: 公证 + staple**

Run: `SIGN_IDENTITY="…" NOTARY_PROFILE=epictrace-notary scripts/package_app.sh --skip-frontend --notarize`
Expected: notarytool `status: Accepted`;staple 成功;验:

```bash
xcrun stapler validate dist/EpicTrace-*.dmg && spctl -a -vv -t open --context context:primary-signature dist/EpicTrace-*.dmg
```
Expected: validate 通过;spctl accepted(source=Notarized Developer ID)。
失败排查:`xcrun notarytool log <submission-id> --keychain-profile epictrace-notary`(常见:漏签的 Mach-O、entitlements 非 ASCII XML)。

- [ ] **Step 3: 文档**

README.md 追加(dev 命令区之后):

```markdown
## 打包(macOS .app / dmg)

```bash
scripts/package_app.sh --no-sign     # 本地冒烟(不签名)
SIGN_IDENTITY="Developer ID Application: …" scripts/package_app.sh           # 签名 .app
SIGN_IDENTITY="…" NOTARY_PROFILE=epictrace-notary scripts/package_app.sh --notarize  # dmg+公证
```

首启由启动器用内嵌 uv 供给运行环境到 `~/Library/Application Support/EpicTrace/runtime/`
(纯派生,可删除重建);用户数据照旧在 `~/.epictrace`。设计与事实来源:
`docs/superpowers/specs/2026-07-14-epictrace-macos-app-packaging-design.md`。
```

CLAUDE.md 底部占位注释区补一行:``打包:`scripts/package_app.sh --no-sign`(签名/公证见 README「打包」节)``。

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: 打包命令与首启供给说明(README/CLAUDE)"
```

---

### Task 9: 真机验收(gate)+ PR

不写代码;把 spec §9 的验收跑完,过了才开 PR。**William 亲测通过并明说合并之前,绝不合 main。**

- [ ] **Step 1: 全量回归 + dev 形态不回退**

Run: `cd backend && .venv/bin/pytest -q`,然后 William 跑 `./run.sh --no-build` 确认 dev 壳照常。

- [ ] **Step 2: 真机验收清单(William,签名版 .app)**

- [ ] 双击打开(无右键绕行);首启进度窗 → 供给完成 → 主窗口出现
- [ ] 断网首启(spec §9.2):关 Wi-Fi 双击 → 进度窗报**断网人话错误**(非原始 uv 输出)→ 恢复网络后「重试」可完成供给
- [ ] 麦克风首用弹窗:归因显示 **EpicTrace** 与 Info.plist 中文文案;录音转写正常(ASR 三层链)
- [ ] 系统内录首用:「仅系统录音」授权项出现并可用(设置 > 隐私与安全性 > 录屏与系统录音)
- [ ] MinerU 供给走包内 uv(断言:Finder 启动、PATH 无 uv 时仍能装)
- [ ] 索引 + 引用问答 + 跳回会话时刻,全链路与 dev 一致(数据/模型共用验证)
- [ ] 升级路径:bump backend/pyproject version → 重打包 → 覆盖安装 → 启动器增量 sync(秒级~分钟级,非全量重下)
- [ ] 干净账户首启:新建 macOS 用户账户,拷入 dmg,完整首启(无 venv/模型/Xcode CLT);或主账户 headless 模拟(与启动器 CLI 语义一致,不污染真实 `~/.epictrace`):`dist/EpicTrace.app/Contents/MacOS/EpicTrace --headless-provision --resources <…>/Resources --runtime /tmp/et-clean-rt --data-dir /tmp/et-clean-dd`
- [ ] 顺手实测:Dock 悬停名/强退框是否显示 "Python"(记录结果回填设计文档 §10 决策 2 的注)
- [ ] 环境自愈:删 `~/Library/Application Support/EpicTrace/runtime/` 后再启动,自动重供给

- [ ] **Step 3: push + PR**

```bash
git push -u origin feat/plan-11-macos-app-packaging
gh pr create --title "feat: macOS 真·app 打包——引导器 .app + uv 全托管(Plan 11)" --body "<按仓库惯例:概述/改动清单/测试证据/真机验收结果;引用设计文档路径>"
```

---

## Self-review 备忘(计划作者已核 + 三路对抗审查修订)

- spec §3-§9 全部条目均有对应 Task(§3 布局=T6/T7、§4 启动器含错误分类/健康兜底/环境损坏出口=T5、§5 四断点+搬迁=T1-T4、§6 流水线=T7/T8、§7 自愈=T5 的 escalation 梯度[清缓存→全量重建]+provenance selfHeal+marker、§8 TCC=T6 plist+T5 保活、§9 验收含断网/spctl=T8/T9)。
- 类型/名字一致性:`EPICTRACE_*` 五个变量名 T1(定义)=T4(消费)=T5(注入,含 `EPICTRACE_DATA_DIR` 同源回注);`python -m epictrace.shell` T4=T5;Resources 文件名 T5(engine 路径)=T7(脚本 cp)。
- 对抗审查(3 agent,含 Swift 真编译)已修订:media 入口名(`_rich_processors`)、`test_shell_reveal.py` 搬迁改造、swiftc `-target arm64-apple-macosx14.4`、headless 幂等快速路径、自愈梯度、UV_PYTHON_DOWNLOADS=never、data_dir 三级解析、zsh glob 陷阱、README 旧路径、helper entitlements 与 spec 对齐。Task 5 的 Swift 代码块以 `-target arm64-apple-macosx14.4` 实际编译通过并冒烟(--print-plan / headless 错误路径)。
- 已知留白(有意,不算 placeholder):Swift 编译细节以 swiftc 报错为准微调;公证失败排查走 notarytool log;PR body 按仓库惯例现场写。

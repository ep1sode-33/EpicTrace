import subprocess
import threading
from pathlib import Path

import pytest

from epictrace.media.mineru_provisioner import MinerUProvisioner


def _venv_dir(tmp_path: Path) -> Path:
    return tmp_path / ".MinerU-venv"


def test_not_ready_before_provision(tmp_path: Path):
    p = MinerUProvisioner(_venv_dir(tmp_path), uv_bin="/usr/local/bin/uv")
    assert p.is_ready() is False
    assert p.state == "not_installed"


def test_is_ready_requires_a_file_not_a_directory(tmp_path: Path):
    """mineru_bin 路径若是目录(而非可执行文件)不算就绪。"""
    venv = _venv_dir(tmp_path)
    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv")
    # 在 mineru_bin() 处造一个同名目录占位
    bin_path = Path(p.mineru_bin())
    bin_path.mkdir(parents=True, exist_ok=True)
    assert bin_path.is_dir()
    assert p.is_ready() is False
    assert p.state == "not_installed"


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


def test_state_is_installing_while_provisioning(tmp_path: Path):
    """provision 进行中 state 必须为 installing(前端"安装中"徽标依赖它)。"""
    venv = _venv_dir(tmp_path)
    observed: list[str] = []

    def uv_runner(cmd, timeout):
        observed.append(p.state)  # 在 provision 子进程步骤里观察当前状态
        if "venv" in cmd:
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "mineru").write_text("#!/bin/sh\n")
            (venv / "bin" / "mineru").chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", uv_runner=uv_runner)
    p.provision()
    assert "installing" in observed
    assert p.state == "installed_no_models"


def test_duplicate_provision_while_installing_is_noop(tmp_path: Path):
    """安装中再次 provision 必须 no-op(不起第二次安装),返回当前状态。"""
    venv = _venv_dir(tmp_path)
    release = threading.Event()
    started = threading.Event()
    venv_calls = {"n": 0}

    def uv_runner(cmd, timeout):
        if "venv" in cmd:
            venv_calls["n"] += 1
            started.set()
            release.wait(timeout=5)  # 卡住第一次 provision,使其保持 installing
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "mineru").write_text("#!/bin/sh\n")
            (venv / "bin" / "mineru").chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", uv_runner=uv_runner)
    t = threading.Thread(target=p.provision, daemon=True)
    t.start()
    assert started.wait(timeout=5)
    assert p.state == "installing"
    # 第二次调用必须立刻返回(no-op),不阻塞、不开第二次安装
    p.provision()
    assert p.state == "installing"  # 仍在第一次安装中
    release.set()
    t.join(timeout=5)
    assert p.state == "installed_no_models"
    assert venv_calls["n"] == 1  # 只跑了一次 `uv venv`


def test_provision_uv_bin_error_sets_failed_with_last_error(tmp_path: Path, monkeypatch):
    """install 之前的失败(uv_bin 抛 RuntimeError)也要置 failed + last_error,前端才会停止轮询。

    关键是 *state* 变 failed(轮询据此停),而非是否抛出。"""
    venv = _venv_dir(tmp_path)
    # uv 不在 PATH 且未注入 → uv_bin() 抛 RuntimeError(在 _run_or_fail 之前)
    monkeypatch.setattr(
        "epictrace.media.mineru_provisioner.shutil.which", lambda name: None
    )
    p = MinerUProvisioner(venv)  # 无 uv_bin
    with pytest.raises(RuntimeError):
        p.provision()
    assert p.state == "failed"
    assert p.last_error
    assert "uv" in p.last_error.lower()


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
        # 模拟成功的子进程:MinerU 把权重落到 HF/modelscope 缓存(不在 venv 内),
        # 不造任何 marker。就绪 sentinel 由 download_models 成功后自行写入。
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    # 下载前:无 sentinel → 未就绪
    assert p.models_ready_sentinel().exists() is False
    progress: list[str] = []
    p.download_models(progress_cb=progress.append)

    assert p.state == "ready"
    assert p.is_ready() is True
    # 成功后写了 app-owned sentinel
    assert p.models_ready_sentinel().is_file()
    # 跑的是 venv 内的 mineru-models-download,且带 -s <source> 与 -m all(hybrid 需全部模型)
    assert calls[0][0] == str(venv / "bin" / "mineru-models-download")
    assert "-s" in calls[0]
    assert "-m" in calls[0]
    m_idx = calls[0].index("-m")
    assert calls[0][m_idx + 1] == "all"
    assert len(progress) >= 1


def test_download_models_takes_model_source(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    calls: list[list[str]] = []

    def models_runner(cmd, timeout):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    p.download_models(model_source="huggingface")
    src_idx = calls[0].index("-s")
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


# ---- FIX 1: ensure_models_ready 阻塞到就绪(并发不抢跑) ----


def test_ensure_models_ready_when_ready_returns_immediately(tmp_path: Path):
    """已就绪(sentinel 在)→ ensure_models_ready 立即返回,不跑任何下载。"""
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv",
                          models_runner=lambda c, t: pytest.fail("should not download"))
    p.models_ready_sentinel().write_text("ready\n")
    assert p.is_ready()
    p.ensure_models_ready(model_source="modelscope")  # no-op,不调 runner


def test_ensure_models_ready_runs_download_when_installed_no_models(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    calls = {"n": 0}

    def models_runner(cmd, timeout):
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    p.ensure_models_ready(model_source="modelscope")
    assert calls["n"] == 1
    assert p.is_ready()


def test_ensure_models_ready_second_caller_waits_for_first(tmp_path: Path):
    """两个并发 caller:只跑一次下载,两者都在下载完成(ready)后才返回——
    不会有第二个 caller 在 downloading 时跳过、抢先提取。"""
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    release = threading.Event(); started = threading.Event()
    dl_calls = {"n": 0}

    def models_runner(cmd, timeout):
        dl_calls["n"] += 1
        started.set()
        release.wait(timeout=5)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    ready_when_returned: list[bool] = []

    def caller():
        p.ensure_models_ready(model_source="modelscope")
        ready_when_returned.append(p.is_ready())

    t1 = threading.Thread(target=caller, daemon=True)
    t1.start()
    assert started.wait(timeout=5)
    assert p.state == "downloading_models"
    # 第二个 caller 此刻进入:看到 downloading,必须阻塞(不抢跑、不立即返回)。
    t2 = threading.Thread(target=caller, daemon=True)
    t2.start()
    t2.join(timeout=0.3)
    assert t2.is_alive(), "second caller must block while download in progress"
    release.set()
    t1.join(timeout=5); t2.join(timeout=5)
    assert dl_calls["n"] == 1               # 只跑一次下载
    assert ready_when_returned == [True, True]  # 两者返回时都已就绪


def test_ensure_models_ready_waiter_sees_failure(tmp_path: Path):
    """下载失败时,阻塞等待的 caller 也要看到失败(抛错),而非误以为就绪。"""
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    release = threading.Event(); started = threading.Event()

    def models_runner(cmd, timeout):
        started.set()
        release.wait(timeout=5)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="net down")

    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv", models_runner=models_runner)
    errors: list[Exception] = []

    def caller():
        try:
            p.ensure_models_ready(model_source="modelscope")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=caller, daemon=True); t1.start()
    assert started.wait(timeout=5)
    t2 = threading.Thread(target=caller, daemon=True); t2.start()
    t2.join(timeout=0.3)
    assert t2.is_alive()
    release.set()
    t1.join(timeout=5); t2.join(timeout=5)
    assert len(errors) == 2  # 跑者与等待者都看到失败
    assert p.is_ready() is False


# ---- FIX 3: 模型下载进度流式回调 ----


def test_download_models_streams_progress_to_cb(tmp_path: Path, monkeypatch):
    """无注入 runner + 有 progress_cb → 走 Popen 流式读 stderr,把下载进度逐条回调。
    用假 Popen,完全不碰真 mineru-models-download/网络。"""
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    captured_kwargs: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured_kwargs.update(kwargs)
            self.returncode = None
            self.stderr = iter([
                "Downloading doclayout_yolo.pt:  10%|█   | 1/10\r",
                "Downloading doclayout_yolo.pt:  50%|████| 5/10\n",
                "Downloading doclayout_yolo.pt: 100%|████| 10/10\n",
            ])

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "epictrace.media.mineru_provisioner.subprocess.Popen", _FakePopen
    )
    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv")
    seen: list[str] = []
    p.download_models(model_source="modelscope", progress_cb=seen.append)
    # stdout 必须 DEVNULL(避免管道死锁,与 mineru_runner 一致)。
    assert captured_kwargs.get("stdout") is subprocess.DEVNULL
    assert len(seen) >= 2  # 收到增量进度(不止开头那一条静态文案)
    assert any("下载" in s for s in seen)
    # 成功仍写 sentinel → 就绪。
    assert p.is_ready() is True


def test_download_models_streaming_failure_surfaces(tmp_path: Path, monkeypatch):
    """流式路径下子进程非零退出 → 失败(failed_stage=download + last_error),不写 sentinel。"""
    venv = _venv_dir(tmp_path)
    _install_only(venv)

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            self.returncode = None
            self.stderr = iter(["boom: download error\n"])

        def wait(self, timeout=None):
            self.returncode = 1
            return 1

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "epictrace.media.mineru_provisioner.subprocess.Popen", _FakePopen
    )
    p = MinerUProvisioner(venv, uv_bin="/usr/local/bin/uv")
    with pytest.raises(RuntimeError):
        p.download_models(model_source="modelscope", progress_cb=lambda _m: None)
    assert p.is_ready() is False
    assert p.failed_stage == "download"
    assert p.last_error and "boom" in p.last_error


# ---- FIX 5: 区分 install / download 失败;就绪后重下失败仍暴露错误 ----


def test_provision_failure_sets_failed_stage_install(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    p = MinerUProvisioner(
        venv, uv_bin="/usr/local/bin/uv",
        uv_runner=lambda c, t: subprocess.CompletedProcess(c, 1, stdout="", stderr="x"),
    )
    with pytest.raises(RuntimeError):
        p.provision()
    assert p.state == "failed"
    assert p.failed_stage == "install"


def test_download_failure_sets_failed_stage_download(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    p = MinerUProvisioner(
        venv, uv_bin="/usr/local/bin/uv",
        models_runner=lambda c, t: subprocess.CompletedProcess(c, 1, stdout="", stderr="x"),
    )
    with pytest.raises(RuntimeError):
        p.download_models()
    assert p.failed_stage == "download"


def test_redownload_failure_after_ready_keeps_state_but_surfaces_error(tmp_path: Path):
    """已成功下载(sentinel 在 → cached 可用),稍后重下失败:state 仍可用(ready),
    但 failed_stage=download + last_error 暴露失败(否则 UI 完全看不到重下失败)。"""
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    p = MinerUProvisioner(
        venv, uv_bin="/usr/local/bin/uv",
        models_runner=lambda c, t: subprocess.CompletedProcess(c, 0, stdout="", stderr=""),
    )
    p.download_models()  # 首次成功 → sentinel 写入
    assert p.is_ready() and p.state == "ready"
    # 切到一个会失败的 runner 重下。
    p._models_runner = lambda c, t: subprocess.CompletedProcess(c, 1, stdout="", stderr="redownload boom")
    with pytest.raises(RuntimeError):
        p.download_models()
    # cached 模型仍在 → 仍可用(state ready,sentinel 未删)。
    assert p.state == "ready"
    assert p.is_ready() is True
    # 但失败被暴露。
    assert p.failed_stage == "download"
    assert p.last_error and "redownload boom" in p.last_error


def test_successful_download_clears_failed_stage(tmp_path: Path):
    venv = _venv_dir(tmp_path)
    _install_only(venv)
    p = MinerUProvisioner(
        venv, uv_bin="/usr/local/bin/uv",
        models_runner=lambda c, t: subprocess.CompletedProcess(c, 0, stdout="", stderr=""),
    )
    p._failed_stage = "download"; p.last_error = "old error"
    p.download_models()
    assert p.failed_stage is None
    assert p.last_error is None

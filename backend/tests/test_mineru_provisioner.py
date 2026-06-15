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

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

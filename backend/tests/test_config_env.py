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

from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService


def test_default_asr_settings(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    s = svc.get_asr_settings()
    assert s["model"] == "large-v3" and s["language"] == "auto" and s["vad"] is True


def test_set_and_validate(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.set_asr_settings({"model": "medium"})
    assert svc.get_asr_settings()["model"] == "medium"
    with pytest.raises(ValueError):
        svc.set_asr_settings({"model": "bogus"})


def test_set_asr_preserves_extraction(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.set_extraction_settings(engine="mineru", effort="high", model_source="modelscope")
    svc.set_asr_settings({"model": "small"})
    assert svc.get_extraction_settings()["engine"] == "mineru"  # 不被吞


def test_input_device_persists(tmp_path: Path):
    # 输入设备索引(sounddevice device index)持久化往返;默认 None(系统默认)。
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    assert svc.get_asr_settings()["input_device"] is None
    svc.set_asr_settings({"input_device": 3})
    assert svc.get_asr_settings()["input_device"] == 3


def test_get_migrates_invalid_persisted_model(tmp_path: Path):
    """FIX G:settings.json 里残留已下架的 model(distil-large-v3)→ 读时迁移成默认 large-v3,
    并落盘固定(下次读不再带非法值)。"""
    import json

    data_dir = tmp_path
    (data_dir / "settings.json").write_text(
        json.dumps({"asr": {"model": "distil-large-v3", "vad": False}}), encoding="utf-8"
    )
    svc = SettingsService(AppConfig(data_dir=data_dir))
    s = svc.get_asr_settings()
    assert s["model"] == "large-v3"   # 非法 model 被矫正为默认
    assert s["vad"] is False          # 其余持久化字段保留
    # 已落盘迁移:磁盘上的 asr.model 也被改写。
    persisted = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert persisted["asr"]["model"] == "large-v3"


def test_set_validates_compute_type(tmp_path: Path):
    """FIX H:compute_type 必须在白名单内;未知值 → ValueError(路由层转 400)。"""
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.set_asr_settings({"compute_type": "int8"})
    assert svc.get_asr_settings()["compute_type"] == "int8"
    svc.set_asr_settings({"compute_type": "float32"})
    assert svc.get_asr_settings()["compute_type"] == "float32"
    with pytest.raises(ValueError):
        svc.set_asr_settings({"compute_type": "bf16"})


def test_set_validates_window_seconds(tmp_path: Path):
    """FIX H:window_seconds 必须在正向合理区间(5–120);<=0 或越界 → ValueError。"""
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.set_asr_settings({"window_seconds": 12.0})
    assert svc.get_asr_settings()["window_seconds"] == 12.0
    with pytest.raises(ValueError):
        svc.set_asr_settings({"window_seconds": 0})
    with pytest.raises(ValueError):
        svc.set_asr_settings({"window_seconds": -5})
    with pytest.raises(ValueError):
        svc.set_asr_settings({"window_seconds": 999})

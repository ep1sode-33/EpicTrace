from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService


def test_default_asr_settings(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    s = svc.get_asr_settings()
    assert s["model"] == "large-v3" and s["language"] == "zh" and s["vad"] is True


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

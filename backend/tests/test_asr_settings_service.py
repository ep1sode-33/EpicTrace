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

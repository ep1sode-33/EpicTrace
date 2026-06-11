from pathlib import Path

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService


def test_settings_roundtrip_and_masking(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.update_chat_llm(base_url="http://x", api_key="secret", model="m")
    loaded = svc.get_chat_llm()
    assert loaded.base_url == "http://x" and loaded.model == "m" and loaded.api_key == "secret"
    # 面板回传打码:不泄露明文,但能看出已设
    masked = svc.public_view()
    assert masked["chat_llm"]["api_key_set"] is True
    assert "secret" not in str(masked)


def test_settings_defaults_when_no_file(tmp_path: Path):
    v = SettingsService(AppConfig(data_dir=tmp_path)).public_view()
    assert v["chat_llm"]["api_key_set"] is False

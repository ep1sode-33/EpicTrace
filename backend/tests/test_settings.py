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


def test_update_with_none_key_preserves_existing(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.update_chat_llm(base_url="http://x", api_key="secret", model="m")
    # 仅改 model、不传 key → 旧 key 必须保留
    svc.update_chat_llm(base_url="http://x", model="m2", api_key=None)
    loaded = svc.get_chat_llm()
    assert loaded.api_key == "secret" and loaded.model == "m2"
    # 显式传非 None 才替换(空串可清空)
    svc.update_chat_llm(base_url="http://x", model="m2", api_key="")
    assert svc.get_chat_llm().api_key == ""


def test_is_configured_false_before_save_true_after(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    assert svc.is_configured() is False
    assert svc.public_view()["configured"] is False
    svc.update_chat_llm(base_url="http://x", model="m", api_key="k")
    assert svc.is_configured() is True
    assert svc.public_view()["configured"] is True


def test_public_view_never_leaks_key(tmp_path: Path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.update_chat_llm(base_url="http://x", model="m", api_key="topsecret")
    assert "topsecret" not in str(svc.public_view())

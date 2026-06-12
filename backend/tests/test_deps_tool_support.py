from types import SimpleNamespace

from epictrace.api.deps import get_chat_model_factory, get_supports_tools
from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService


def _request_with_profile(tmp_path):
    config = AppConfig(data_dir=tmp_path)
    settings = SettingsService(config)
    settings.create_profile("P", "https://api.deepseek.com", "k", "deepseek-chat")
    state = SimpleNamespace(config=config)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_factory_builds_chat_model_from_active_profile(tmp_path):
    req = _request_with_profile(tmp_path)
    factory = get_chat_model_factory(req)
    model = factory()
    assert model.__class__.__name__ == "ChatOpenAI"


def test_factory_none_when_no_active_profile(tmp_path):
    state = SimpleNamespace(config=AppConfig(data_dir=tmp_path))
    req = SimpleNamespace(app=SimpleNamespace(state=state))
    assert get_chat_model_factory(req) is None


def test_supports_tools_uses_cache_on_app_state(tmp_path, monkeypatch):
    req = _request_with_profile(tmp_path)
    probes = []

    def fake_probe(model):
        probes.append(1)
        return True

    # cached_supports_tools 调用的是 tool_probe 模块里的 probe_tool_calling,
    # 故须在该解析点打桩,才能真正拦住调用(否则会走真探测发网络请求)。
    monkeypatch.setattr("epictrace.agent.tool_probe.probe_tool_calling", fake_probe)
    supports = get_supports_tools(req)
    assert supports() is True
    assert supports() is True
    assert probes == [1]   # cached on app.state → probed once

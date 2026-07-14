"""三个 MinerUProvisioner 构造点必须把 config.uv_bin 传进去(打包内置 uv;dev None → PATH)。"""
from types import SimpleNamespace

from epictrace.config import AppConfig


class _CaptureProv:
    """捕获构造参数的假 provisioner,满足各消费点用到的接口面。"""

    last_kwargs: dict = {}

    def __init__(self, venv_dir, **kwargs):
        type(self).last_kwargs = {"venv_dir": venv_dir, **kwargs}
        self.state = "not_installed"
        self.last_error = None
        self.failed_stage = None

    def is_ready(self):
        return False


def _cfg(tmp_path):
    dd = tmp_path / "dd"
    dd.mkdir(exist_ok=True)
    return AppConfig(data_dir=dd, uv_bin="/pkg/Resources/uv")


def test_settings_extraction_status_passes_uv_bin(tmp_path, monkeypatch):
    import epictrace.services.settings as mod

    monkeypatch.setattr(mod, "MinerUProvisioner", _CaptureProv)
    mod.SettingsService(_cfg(tmp_path)).extraction_status()
    assert _CaptureProv.last_kwargs["uv_bin"] == "/pkg/Resources/uv"


def test_deps_get_provisioner_passes_uv_bin(tmp_path, monkeypatch):
    from epictrace.api import deps

    monkeypatch.setattr(
        "epictrace.media.mineru_provisioner.MinerUProvisioner", _CaptureProv
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=_cfg(tmp_path))))
    deps.get_provisioner(request)
    assert _CaptureProv.last_kwargs["uv_bin"] == "/pkg/Resources/uv"


def test_rich_processors_pass_uv_bin(tmp_path, monkeypatch):
    import epictrace.media as media

    monkeypatch.setattr(media, "MinerUProvisioner", _CaptureProv)

    class _FakeSettings:
        def __init__(self, config):
            pass

        def get_extraction_settings(self):
            return {"engine": "mineru", "model_source": "modelscope", "effort": "medium"}

    monkeypatch.setattr("epictrace.services.settings.SettingsService", _FakeSettings)
    media._rich_processors(_cfg(tmp_path))  # engine=mineru 分支内即 :38 的构造点
    assert _CaptureProv.last_kwargs["uv_bin"] == "/pkg/Resources/uv"

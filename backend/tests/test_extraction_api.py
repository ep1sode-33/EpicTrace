import threading

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


class _FakeProvisioner:
    def __init__(self):
        self._installed = False
        self._models = False
        self.provisioned = threading.Event()
        self.downloaded = threading.Event()
        self.last_error = None

    def is_ready(self):
        return self._installed and self._models

    @property
    def state(self):
        if self._installed and self._models:
            return "ready"
        if self._installed:
            return "installed_no_models"
        return "not_installed"

    def provision(self, progress_cb=None):
        self._installed = True
        self.provisioned.set()

    def download_models(self, *, model_source="modelscope", progress_cb=None):
        self._models = True
        self.downloaded.set()


@pytest.fixture()
def app_and_prov(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    app = create_app(db=db)
    prov = _FakeProvisioner()
    app.state.provisioner = prov   # 注入假 provisioner(deps.get_provisioner 优先用它)
    return TestClient(app), prov


def test_status_reports_not_installed(app_and_prov):
    client, _ = app_and_prov
    r = client.get("/api/extraction/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "not_installed"
    assert body["ready"] is False


def test_provision_kicks_off_and_becomes_ready(app_and_prov):
    client, prov = app_and_prov
    r = client.post("/api/extraction/provision")
    assert r.status_code == 200
    assert prov.provisioned.wait(timeout=5)  # 后台线程跑完
    # provision 只装包 → installed_no_models(尚未下模型)。
    assert client.get("/api/extraction/status").json()["state"] == "installed_no_models"


def test_status_reports_installed_no_models(app_and_prov):
    client, prov = app_and_prov
    prov.provision()  # 包装好、模型未下
    body = client.get("/api/extraction/status").json()
    assert body["state"] == "installed_no_models"
    assert body["ready"] is False


def test_get_extraction_settings_defaults(app_and_prov):
    client, _ = app_and_prov
    body = client.get("/api/extraction/settings").json()
    assert body == {"engine": "mineru", "effort": "medium", "model_source": "modelscope"}


def test_put_extraction_settings_persists(app_and_prov):
    client, _ = app_and_prov
    r = client.put("/api/extraction/settings",
                   json={"engine": "mineru", "effort": "high", "model_source": "huggingface"})
    assert r.status_code == 200
    assert r.json() == {"engine": "mineru", "effort": "high", "model_source": "huggingface"}
    # 持久化:再 GET 取到新值。
    assert client.get("/api/extraction/settings").json()["effort"] == "high"


def test_put_extraction_settings_rejects_bad_value(app_and_prov):
    client, _ = app_and_prov
    r = client.put("/api/extraction/settings",
                   json={"engine": "mineru", "effort": "ultra", "model_source": "modelscope"})
    assert r.status_code == 400


def test_download_models_kicks_off_and_becomes_ready(app_and_prov):
    client, prov = app_and_prov
    prov.provision()  # 先装包
    r = client.post("/api/extraction/download-models")
    assert r.status_code == 200
    assert prov.downloaded.wait(timeout=5)  # 后台线程跑完
    assert client.get("/api/extraction/status").json()["ready"] is True

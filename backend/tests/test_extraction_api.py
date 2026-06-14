import threading

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


class _FakeProvisioner:
    def __init__(self):
        self._ready = False
        self.provisioned = threading.Event()

    def is_ready(self):
        return self._ready

    @property
    def state(self):
        return "ready" if self._ready else "not_installed"

    def provision(self, progress_cb=None):
        self._ready = True
        self.provisioned.set()


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
    # 轮询 status 直到 ready
    assert client.get("/api/extraction/status").json()["ready"] is True

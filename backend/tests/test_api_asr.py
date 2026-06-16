"""ASR 设置/状态/下载 + partial 路由测试(镜像 test_api_settings / test_extraction_api)。

覆盖 Phase 6 加的:GET/PUT /api/asr/settings、GET /api/asr/status、
POST /api/asr/download-model、capture 的 partial POST→GET 往返。

全程不碰真 faster-whisper:settings/status/partial 路由本就不加载模型;download-model
注入假 provisioner(get_asr_provisioner 优先用 app.state.asr_provisioner),不触发真下载。
"""
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.asr.config import AsrConfig
from epictrace.config import AppConfig
from epictrace.db import Database


class _FakeAsrProvisioner:
    """假 ASR provisioner:记录被请求下载的模型,不碰真 faster-whisper / 网络。

    state/is_ready(model) 形状对齐真 AsrProvisioner(routers/settings._asr_status 用到)。
    """

    def __init__(self):
        self._ready_models: set[str] = set()
        self.downloaded: list[str] = []
        self.event = threading.Event()
        self.last_error: str | None = None

    def is_ready(self, model: str) -> bool:
        return model in self._ready_models

    @property
    def state(self) -> str:
        return "ready" if self._ready_models else "not_downloaded"

    def download_model(self, model: str, *, progress_cb=None) -> str:
        self.downloaded.append(model)
        self._ready_models.add(model)
        self.event.set()
        return self.state


def _client(tmp_path: Path, prov=None):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    if prov is not None:
        app.state.asr_provisioner = prov  # 注入假件:deps.get_asr_provisioner 优先用它
    return TestClient(app)


@pytest.fixture()
def app_client(tmp_path):
    return _client(tmp_path)


# ---- GET /api/asr/settings:默认值 = AsrConfig 默认 ----


def test_get_asr_settings_defaults(app_client):
    body = app_client.get("/api/asr/settings").json()
    assert body == AsrConfig().to_dict()
    # 抽查几个弱音友好默认(spec §9)。
    assert body["model"] == "large-v3"
    assert body["language"] == "zh"
    assert body["vad"] is True
    assert body["condition_prev"] is False
    assert body["force_confirm_after"] == 4


# ---- PUT /api/asr/settings:部分更新 + 校验 ----


def test_put_asr_settings_partial_update_persists(app_client):
    r = app_client.put("/api/asr/settings", json={"model": "medium"})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "medium"
    # 部分更新:未给的键保留默认,不被重置。
    assert body["language"] == "zh"
    assert body["vad"] is True
    # 持久化:再 GET 取到新值。
    assert app_client.get("/api/asr/settings").json()["model"] == "medium"


def test_put_asr_settings_only_changes_given_keys(app_client):
    app_client.put("/api/asr/settings", json={"model": "small"})
    # 第二次只改 vad,model 应保留 small(部分更新不回滚)。
    r = app_client.put("/api/asr/settings", json={"vad": False})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "small" and body["vad"] is False


def test_put_asr_settings_rejects_bad_model(app_client):
    r = app_client.put("/api/asr/settings", json={"model": "bogus"})
    assert r.status_code == 400


def test_put_asr_settings_preserves_extraction(app_client):
    """改 ASR 设置不应吞掉 extraction 顶层键(各自独立持久化)。"""
    app_client.put("/api/extraction/settings",
                   json={"engine": "mineru", "effort": "high", "model_source": "modelscope"})
    app_client.put("/api/asr/settings", json={"model": "small"})
    assert app_client.get("/api/extraction/settings").json()["engine"] == "mineru"


# ---- GET /api/asr/status:形状 + 反映选中模型就绪与否 ----


def test_asr_status_shape_not_downloaded(tmp_path):
    prov = _FakeAsrProvisioner()
    c = _client(tmp_path, prov)
    body = c.get("/api/asr/status").json()
    assert set(body) == {"state", "ready", "model", "error"}
    assert body["state"] == "not_downloaded"
    assert body["ready"] is False
    assert body["model"] == "large-v3"  # 默认配置模型
    assert body["error"] is None


def test_asr_status_reflects_configured_model_readiness(tmp_path):
    prov = _FakeAsrProvisioner()
    prov._ready_models.add("medium")  # 假装 medium 已就绪,large-v3 没下
    c = _client(tmp_path, prov)
    # 默认配置是 large-v3 → 未就绪。
    assert c.get("/api/asr/status").json()["ready"] is False
    # 切到 medium → status 看的是选中模型,变就绪。
    c.put("/api/asr/settings", json={"model": "medium"})
    body = c.get("/api/asr/status").json()
    assert body["model"] == "medium" and body["ready"] is True and body["state"] == "ready"


# ---- POST /api/asr/download-model:触发后台下载选中模型 ----


def test_download_model_kicks_off_configured_model(tmp_path):
    prov = _FakeAsrProvisioner()
    c = _client(tmp_path, prov)
    c.put("/api/asr/settings", json={"model": "small"})
    r = c.post("/api/asr/download-model")
    assert r.status_code == 200
    # 后台线程跑完:下的是当前配置模型(small),非默认。
    assert prov.event.wait(timeout=5)
    assert prov.downloaded == ["small"]
    assert c.get("/api/asr/status").json()["ready"] is True


# ---- capture partial POST→GET 往返(内存态,不落库)----


def test_partial_post_then_get_roundtrip(tmp_path):
    # 起带 mic 的 session 需模型就绪(FIX 1 服务端门控);注入预置就绪的假 provisioner。
    prov = _FakeAsrProvisioner()
    prov._ready_models.add("large-v3")
    app_client = _client(tmp_path, prov)
    sid = app_client.post("/api/capture/sessions",
                          json={"sources": ["mic"]}).json()["id"]
    # 无 partial 时空 dict。
    assert app_client.get(f"/api/capture/sessions/{sid}/partial").json() == {}
    # POST 一条 mic partial → GET 读回。
    app_client.post(f"/api/capture/sessions/{sid}/partial",
                    json={"source": "mic", "text": "暂定一"})
    assert app_client.get(f"/api/capture/sessions/{sid}/partial").json() == {"mic": "暂定一"}
    # 再 POST device + 覆盖 mic → 按 source 分别保最新。
    app_client.post(f"/api/capture/sessions/{sid}/partial",
                    json={"source": "device", "text": "暂定二"})
    app_client.post(f"/api/capture/sessions/{sid}/partial",
                    json={"source": "mic", "text": "暂定三"})
    assert app_client.get(f"/api/capture/sessions/{sid}/partial").json() == {
        "mic": "暂定三", "device": "暂定二"}

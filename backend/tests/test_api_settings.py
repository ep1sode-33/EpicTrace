import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


@pytest.fixture()
def app_client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    return TestClient(create_app(db=db))


def test_get_settings_empty(app_client):
    v = app_client.get("/api/settings").json()
    assert v["configured"] is False
    assert v["active_profile_id"] is None
    assert v["profiles"] == []


def test_create_profile_appears_and_becomes_active(app_client):
    r = app_client.post(
        "/api/settings/profiles",
        json={"name": "A", "base_url": "http://x", "api_key": "secret", "model": "m"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert len(body["profiles"]) == 1
    prof = body["profiles"][0]
    assert prof["name"] == "A" and prof["model"] == "m"
    assert prof["api_key_set"] is True
    assert body["active_profile_id"] == prof["id"]
    # GET 回传真 key(本地单机,允许查看/编辑/复制)
    got = app_client.get("/api/settings").json()
    assert got["profiles"][0]["api_key"] == "secret"
    assert got["active_profile_id"] == prof["id"]


def test_update_profile_blank_key_keeps_existing(app_client):
    pid = app_client.post(
        "/api/settings/profiles",
        json={"name": "A", "base_url": "http://x", "api_key": "secret", "model": "m"},
    ).json()["profiles"][0]["id"]
    # 只改 model,api_key 留空 → 保留既有
    r = app_client.put(
        f"/api/settings/profiles/{pid}",
        json={"model": "m2", "api_key": ""},
    )
    assert r.status_code == 200
    prof = r.json()["profiles"][0]
    assert prof["model"] == "m2" and prof["api_key_set"] is True
    # 变更后 LLM 缓存被失效,deps 按新 model 重建
    from epictrace.api.deps import get_llm
    from fastapi import Request

    req = Request({"type": "http", "app": app_client.app})
    assert get_llm(req)._model == "m2"


def test_delete_active_profile_clears_or_reassigns(app_client):
    a = app_client.post(
        "/api/settings/profiles",
        json={"name": "A", "base_url": "http://a", "api_key": "k1", "model": "m1"},
    ).json()["profiles"][0]["id"]
    app_client.post(
        "/api/settings/profiles",
        json={"name": "B", "base_url": "http://b", "api_key": "k2", "model": "m2"},
    )
    body = app_client.delete(f"/api/settings/profiles/{a}").json()
    # 删的是活动 → 改指剩余的
    assert body["active_profile_id"] is not None
    assert all(p["id"] != a for p in body["profiles"])


def test_set_active_switches(app_client):
    app_client.post(
        "/api/settings/profiles",
        json={"name": "A", "base_url": "http://a", "api_key": "k1", "model": "m1"},
    )
    b = app_client.post(
        "/api/settings/profiles",
        json={"name": "B", "base_url": "http://b", "api_key": "k2", "model": "m2"},
    ).json()["profiles"][1]["id"]
    body = app_client.put("/api/settings/active", json={"profile_id": b}).json()
    assert body["active_profile_id"] == b


def test_test_profile_success(app_client, monkeypatch):
    calls = {}

    def fake_complete(self, messages, **kwargs):
        calls["messages"] = messages
        calls["kwargs"] = kwargs
        return "pong"

    monkeypatch.setattr(
        "epictrace.llm.openai_compat.OpenAICompatLLM.complete", fake_complete
    )
    r = app_client.post(
        "/api/settings/test",
        json={"base_url": "http://x/v1", "api_key": "k", "model": "m"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sample"] == "pong"
    assert body["error"] is None
    # 真实最小调用:带 max_tokens 和一条 user 消息
    assert calls["kwargs"].get("max_tokens") == 16
    assert calls["messages"][0]["role"] == "user"


def test_test_profile_failure_is_data_not_http_error(app_client, monkeypatch):
    def boom(self, messages, **kwargs):
        raise RuntimeError("Chat completion bad format")

    monkeypatch.setattr(
        "epictrace.llm.openai_compat.OpenAICompatLLM.complete", boom
    )
    r = app_client.post(
        "/api/settings/test",
        json={"base_url": "http://x/v1", "api_key": "k", "model": "m"},
    )
    # 失败也是 200(让前端能展示网关原始错误)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Chat completion bad format" in body["error"]
    assert body["sample"] is None


def test_chat_409_when_no_profile(app_client):
    pid = app_client.post(
        "/api/projects", json={"title": "P", "folder_path": "/tmp/p_409"}
    ).json()["id"]
    cid = app_client.post(f"/api/projects/{pid}/conversations", json={}).json()["id"]
    r = app_client.request(
        "POST", f"/api/conversations/{cid}/messages", json={"content": "x"}
    )
    assert r.status_code == 409

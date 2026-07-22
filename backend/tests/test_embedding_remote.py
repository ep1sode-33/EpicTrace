"""远程 Embedding provider 测试(需求 8):接口契约 + 设置持久化 + deps 选路。"""

import types

import pytest

from epictrace.config import AppConfig
from epictrace.embedding.openai_compat import OpenAICompatEmbedder
from epictrace.services.settings import SettingsService


class _FakeEmbeddingsAPI:
    """openai client.embeddings.create 的假件:记录请求,返回确定性向量。"""

    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(kwargs["input"])
        data = [types.SimpleNamespace(index=i, embedding=[float(i), 1.0]) for i in range(n)]
        return types.SimpleNamespace(data=data)


def _embedder_with_fake(monkeypatch, **kw):
    fake_api = _FakeEmbeddingsAPI()

    class FakeOpenAI:
        def __init__(self, **_):
            self.embeddings = fake_api

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    defaults = {"base_url": "https://api.example.com/v1", "api_key": "k",
                "model": "emb-model", "dimensions": 512}
    defaults.update(kw)
    return OpenAICompatEmbedder(**defaults), fake_api


def test_contract_and_request_shape(monkeypatch):
    emb, api = _embedder_with_fake(monkeypatch)
    assert emb.model_id == "openai:emb-model"
    assert emb.dimensions == 512
    vectors = emb.embed(["甲", "乙"])
    assert vectors == [[0.0, 1.0], [1.0, 1.0]]  # 顺序与输入对齐
    assert api.calls[0]["model"] == "emb-model"
    assert api.calls[0]["dimensions"] == 512
    assert api.calls[0]["input"] == ["甲", "乙"]


def test_dimensions_omitted_when_unset(monkeypatch):
    emb, api = _embedder_with_fake(monkeypatch, dimensions=None)
    emb.embed(["x"])
    assert "dimensions" not in api.calls[0]


def test_batching(monkeypatch):
    emb, api = _embedder_with_fake(monkeypatch)
    emb.embed([f"t{i}" for i in range(150)])
    assert len(api.calls) == 3  # 64 + 64 + 22


def test_requires_base_url_and_model():
    with pytest.raises(ValueError):
        OpenAICompatEmbedder(base_url="", api_key="", model="m")
    with pytest.raises(ValueError):
        OpenAICompatEmbedder(base_url="https://x", api_key="", model="")


# ---- 设置持久化 + API ----

def test_embedding_settings_roundtrip(tmp_path):
    svc = SettingsService(AppConfig(data_dir=tmp_path))
    assert svc.get_embedding_settings()["provider"] == "local"
    out = svc.set_embedding_settings({
        "provider": "remote", "base_url": "https://api.example.com/v1",
        "model": "bge-m3", "dimensions": 1024,
    })
    assert out["provider"] == "remote"
    # 换实例读回(持久化生效)
    assert SettingsService(AppConfig(data_dir=tmp_path)).get_embedding_settings()["model"] == "bge-m3"
    with pytest.raises(ValueError):
        svc.set_embedding_settings({"provider": "yolo"})
    with pytest.raises(ValueError):
        svc.set_embedding_settings({"dimensions": 1})


def test_embedding_settings_api(client):
    r = client.get("/api/settings/embedding")
    assert r.json()["provider"] == "local"
    assert r.json()["dimensions"] == 1024

    r = client.put("/api/settings/embedding",
                   json={"provider": "remote", "base_url": "https://api.example.com/v1",
                         "model": "emb-x", "dimensions": 768})
    assert r.status_code == 200
    assert r.json()["provider"] == "remote"
    assert r.json()["dimensions"] == 768

    assert client.put("/api/settings/embedding", json={"provider": "yolo"}).status_code == 400


def test_get_embedder_switches_to_remote(client):
    """deps.get_embedder 按设置选路;改设置后签名变化自动重建(验收 8 的选路证据)。"""
    from epictrace.api.deps import get_embedder

    class Req:
        pass

    req = Req()
    req.app = client.app
    e1 = get_embedder(req)
    assert e1.model_id != "openai:emb-x"

    client.put("/api/settings/embedding",
               json={"provider": "remote", "base_url": "https://api.example.com/v1",
                     "model": "emb-x", "dimensions": 768})
    e2 = get_embedder(req)
    assert isinstance(e2, OpenAICompatEmbedder)
    assert e2.model_id == "openai:emb-x"
    assert e2.dimensions == 768

    # 切回 local → 重建为 BGE-M3(不实例化真模型——只断言类型切换逻辑的话会拉起真件,
    # 所以这里只验证「设置回 local 后不再返回 remote 实例」)
    client.put("/api/settings/embedding", json={"provider": "local"})
    e3 = get_embedder(req)
    assert not isinstance(e3, OpenAICompatEmbedder)

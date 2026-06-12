from pathlib import Path

from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from tests.fakes import FakeEmbedder, FakeReranker, FakeVectorStore


def _client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    store = FakeVectorStore()
    app = create_app(db=db, embedder=FakeEmbedder(), reranker=FakeReranker())
    app.state.attachment_store = store  # 注入临时附件 store(避免起真 Milvus/模型)
    return TestClient(app), store


def _proj_conv(client, tmp_path):
    folder = tmp_path / "p"; folder.mkdir()
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/conversations", json={"title": "t"}).json()["id"]
    return cid


def test_large_external_indexed_via_api_then_cleaned_on_detach(tmp_path: Path):
    client, store = _client(tmp_path)
    client.post("/api/settings/profiles", json={"name": "A", "base_url": "http://x",
                "api_key": "k", "model": "m", "context_window": 8})
    cid = _proj_conv(client, tmp_path)
    f = tmp_path / "big.md"; f.write_text("页表把虚拟地址映射到物理地址。" * 30, encoding="utf-8")
    r = client.post(f"/api/conversations/{cid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 201 and r.json()["mode"] == "indexed"
    rid = r.json()["id"]
    assert store.list_by({"reference_id": rid})
    assert client.delete(f"/api/conversations/{cid}/references/{rid}").status_code == 204
    assert store.list_by({"reference_id": rid}) == []


def test_delete_conversation_cleans_attachment_vectors(tmp_path: Path):
    client, store = _client(tmp_path)
    client.post("/api/settings/profiles", json={"name": "A", "base_url": "http://x",
                "api_key": "k", "model": "m", "context_window": 8})
    cid = _proj_conv(client, tmp_path)
    f = tmp_path / "big.md"; f.write_text("内容内容内容。" * 50, encoding="utf-8")
    rid = client.post(f"/api/conversations/{cid}/references",
                      json={"kind": "external", "source_path": str(f)}).json()["id"]
    assert store.list_by({"conversation_id": cid})
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert store.list_by({"conversation_id": cid}) == []

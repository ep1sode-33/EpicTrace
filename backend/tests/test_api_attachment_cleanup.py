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


def _proj_session(client, tmp_path):
    folder = tmp_path / "p"; folder.mkdir()
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    sid = client.post("/api/cowork/sessions", json={"project_id": pid}).json()["id"]
    return sid


def test_large_external_indexed_via_api_then_cleaned_on_detach(tmp_path: Path):
    client, store = _client(tmp_path)
    client.post("/api/settings/profiles", json={"name": "A", "base_url": "http://x",
                "api_key": "k", "model": "m", "context_window": 8})
    sid = _proj_session(client, tmp_path)
    f = tmp_path / "big.md"; f.write_text("页表把虚拟地址映射到物理地址。" * 30, encoding="utf-8")
    r = client.post(f"/api/cowork/sessions/{sid}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 201 and r.json()["mode"] == "indexed"
    rid = r.json()["id"]
    assert store.list_by({"reference_id": rid})
    assert client.delete(f"/api/cowork/sessions/{sid}/references/{rid}").status_code == 204
    assert store.list_by({"reference_id": rid}) == []

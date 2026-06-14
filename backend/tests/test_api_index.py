import time

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from tests.fakes import FakeEmbedder, FakeVectorStore


def _poll_until_done(client, pid, timeout=10.0):
    """轮询 status,直到不再 running(FakeEmbedder 很快)。返回最终 body。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/projects/{pid}/index/status").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"index job did not finish within {timeout}s: last={body}")


def test_index_endpoint_indexes_pending(index_client, tmp_path):
    folder = tmp_path / "P"
    pid = index_client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("page table " * 200, encoding="utf-8")
    index_client.post(f"/api/projects/{pid}/scan")

    # POST 立刻返回 running(后台线程推进),不等待完成。
    resp = index_client.post(f"/api/projects/{pid}/index")
    assert resp.status_code == 200
    started = resp.json()
    assert started["total"] == 1 and started["status"] == "running"

    # 轮询 status 直到完成,再断言。
    body = _poll_until_done(index_client, pid)
    assert body["total"] == 1 and body["done"] == 1 and body["status"] == "done"

    files = index_client.get(f"/api/files?project_id={pid}").json()
    assert all(f["indexed"] for f in files)


def test_index_status_unknown_project_404(index_client):
    assert index_client.post("/api/projects/99999/index").status_code == 404


def test_index_status_endpoint_unknown_project_404(index_client):
    # Fix 4:status 对不存在的项目也应 404,而非返回 idle。
    assert index_client.get("/api/projects/99999/index/status").status_code == 404


@pytest.fixture()
def reindex_client(tmp_path):
    """带 FakeVectorStore 的 index_client,便于断言重建时向量被清理。"""
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    store = FakeVectorStore()
    app = create_app(db=db, embedder=FakeEmbedder(), vector_store=store)
    return TestClient(app), store


def test_reindex_endpoint_clears_vectors_and_reindexes(reindex_client, tmp_path):
    client, store = reindex_client
    folder = tmp_path / "P"
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("page table " * 200, encoding="utf-8")
    client.post(f"/api/projects/{pid}/scan")

    # 先建一次索引并跑完(文件翻成 indexed=True)。
    client.post(f"/api/projects/{pid}/index")
    _poll_until_done(client, pid)
    assert all(f["indexed"] for f in client.get(f"/api/files?project_id={pid}").json())

    # 重建:立刻返回 running(后台线程推进);向量已被按项目清理;total 含全部文件。
    resp = client.post(f"/api/projects/{pid}/reindex")
    assert resp.status_code == 200
    started = resp.json()
    assert started["status"] == "running" and started["total"] == 1
    assert pid in store.deleted_projects

    body = _poll_until_done(client, pid)
    assert body["total"] == 1 and body["done"] == 1 and body["status"] == "done"
    assert all(f["indexed"] for f in client.get(f"/api/files?project_id={pid}").json())


def test_reindex_unknown_project_404(reindex_client):
    client, _ = reindex_client
    assert client.post("/api/projects/99999/reindex").status_code == 404

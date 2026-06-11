from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from tests.fakes import FakeVectorStore


def test_create_and_list_projects(client, tmp_path):
    folder = str(tmp_path / "CS 2506")
    resp = client.post("/api/projects", json={"title": "CS 2506", "folder_path": folder})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "CS 2506"
    assert body["id"] > 0

    listed = client.get("/api/projects").json()
    assert len(listed) == 1
    assert listed[0]["folder_path"] == folder


@pytest.fixture()
def delete_client(tmp_path):
    """带 FakeVectorStore 的 client,便于断言项目删除时向量被清理。"""
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    store = FakeVectorStore()
    app = create_app(db=db, vector_store=store)
    return TestClient(app), store, db


def _make_project_with_record(client: TestClient, folder: Path) -> int:
    """建项目 + 在文件夹里放一个文件并扫描登记一条 IngestRecord。"""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "note.md").write_text("hello", encoding="utf-8")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    client.post(f"/api/projects/{pid}/scan")
    return pid


def test_delete_project_removes_from_list_and_records(delete_client, tmp_path):
    from sqlalchemy import select

    from epictrace.models import IngestRecord

    client, store, db = delete_client
    folder = tmp_path / "proj"
    pid = _make_project_with_record(client, folder)
    assert len(client.get(f"/api/files?project_id={pid}").json()) == 1
    # 标记该记录已索引,使删除时确有向量需要清理(否则按设计跳过 Milvus)。
    with db.session() as s:
        rec = (
            s.execute(select(IngestRecord).where(IngestRecord.project_id == pid))
            .scalars()
            .first()
        )
        rec.indexed = True

    resp = client.request("DELETE", f"/api/projects/{pid}")
    assert resp.status_code in (200, 204)

    # 项目从列表消失
    assert client.get("/api/projects").json() == []
    # 其 ingest records 随级联删除
    assert client.get(f"/api/files?project_id={pid}").json() == []
    # 向量库被请求按项目清理
    assert pid in store.deleted_projects


def test_delete_project_keeps_folder_by_default(delete_client, tmp_path):
    client, _, _ = delete_client
    folder = tmp_path / "proj"
    pid = _make_project_with_record(client, folder)

    client.request("DELETE", f"/api/projects/{pid}")
    # 默认 delete_folder=False:磁盘文件夹保留
    assert folder.exists()
    assert (folder / "note.md").exists()


def test_delete_project_with_delete_folder_removes_folder(delete_client, tmp_path):
    client, _, _ = delete_client
    folder = tmp_path / "proj"
    pid = _make_project_with_record(client, folder)

    client.request("DELETE", f"/api/projects/{pid}?delete_folder=true")
    assert not folder.exists()


def test_delete_unknown_project_404(delete_client):
    client, _, _ = delete_client
    assert client.request("DELETE", "/api/projects/99999").status_code == 404


def test_delete_project_without_constructed_store_is_ok(client, tmp_path):
    """vector_store 为 None(从未索引)时,删除仍应成功(惰性构造或跳过)。"""
    folder = tmp_path / "proj"
    folder.mkdir(parents=True, exist_ok=True)
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    resp = client.request("DELETE", f"/api/projects/{pid}")
    assert resp.status_code in (200, 204)
    assert client.get("/api/projects").json() == []
